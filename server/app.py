import asyncio
import os
import sys

import requests

# Asegurar que el directorio de server esté en el PYTHONPATH
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List

from database import get_history, get_stats, clear_history
from traffic_controller import TrafficController
from vision_tracker import VisionTracker

app = FastAPI(title="Semáforo Inteligente Adaptativo - Central de Control")

# Directorio web para archivos estáticos
WEB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))

# Inicializar motor de visión y controlador de semáforo
DEFAULT_LANE1_URL = os.getenv("LANE1_URL", None)
DEFAULT_LANE2_URL = os.getenv("LANE2_URL", None)

DEFAULT_DETECTION_MODE = os.getenv("DETECTION_MODE", "yolo")
vision = VisionTracker(mode=DEFAULT_DETECTION_MODE)
controller = TrafficController(
    lane1_url=DEFAULT_LANE1_URL,
    lane2_url=DEFAULT_LANE2_URL
)
if DEFAULT_LANE1_URL or DEFAULT_LANE2_URL:
    vision.set_camera_urls(DEFAULT_LANE1_URL, DEFAULT_LANE2_URL)

# Conexiones WebSocket activas
active_websockets: List[WebSocket] = []
hardware_link = {"gpio_ready": False}

class SettingsPayload(BaseModel):
    lane1_url: Optional[str] = None
    lane2_url: Optional[str] = None
    esp32_lane1_url: Optional[str] = None
    esp32_lane2_url: Optional[str] = None
    detection_mode: Optional[str] = None  # 'mog2' o 'yolo'
    use_simulation: Optional[bool] = None

class PausePayload(BaseModel):
    paused: bool

@app.on_event("startup")
async def startup_event():
    # Iniciar máquina de estados del semáforo
    await controller.start()
    # Tarea de actualización periódica de conteo vehicular hacia el controlador
    asyncio.create_task(sync_vision_loop())
    asyncio.create_task(poll_agent_cameras())
    # Tarea de broadcast por WebSocket a los clientes conectados
    asyncio.create_task(websocket_broadcast_loop())

async def poll_agent_cameras():
    """Consulta al agente local si cada webcam USB está realmente abierta."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            payload = await loop.run_in_executor(None, _read_agent_status)
            lanes = payload.get("lanes", {})
            vision.note_agent_cameras(
                bool(lanes.get("lane1", {}).get("online")),
                bool(lanes.get("lane2", {}).get("online")),
            )
            hardware_link["gpio_ready"] = bool(payload.get("gpio_ready"))
        except Exception:
            pass
        await asyncio.sleep(2)

def _read_agent_status() -> dict:
    lane1 = os.getenv("LANE1_URL", "http://127.0.0.1:8080/lane1").rstrip("/")
    for suffix in ("/stream", "/lane1", "/lane2"):
        if lane1.endswith(suffix):
            lane1 = lane1[: -len(suffix)]
    origin = lane1.rstrip("/")
    response = requests.get(f"{origin}/status", timeout=0.6)
    response.raise_for_status()
    return response.json()

async def sync_vision_loop():
    """Sincroniza periódicamente el conteo del motor de visión con el controlador sin bloquear asyncio."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            # Obtener estado de luces para el simulador/procesador
            light_l1 = "GREEN" if controller.lights["lane1"]["green"] else ("YELLOW" if controller.lights["lane1"]["yellow"] else "RED")
            light_l2 = "GREEN" if controller.lights["lane2"]["green"] else ("YELLOW" if controller.lights["lane2"]["yellow"] else "RED")

            c1 = await loop.run_in_executor(None, vision.get_count_fast, 1, light_l1)
            c2 = await loop.run_in_executor(None, vision.get_count_fast, 2, light_l2)

            controller.update_vehicle_counts(c1, c2)
        except Exception as e:
            print(f"[SyncVision] Error: {e}")
        await asyncio.sleep(0.1)

async def websocket_broadcast_loop():
    """Envía telemetría en tiempo real a todos los clientes web (10 Hz)."""
    while True:
        if active_websockets:
            telemetry = controller.get_telemetry()
            telemetry["detection_mode"] = vision.mode
            telemetry["use_simulation"] = vision.use_simulation
            telemetry["vision"] = vision.get_summary()
            telemetry["gpio_ready"] = hardware_link["gpio_ready"]
            telemetry["stats"] = get_stats()

            disconnected = []
            for ws in active_websockets:
                try:
                    await ws.send_json(telemetry)
                except Exception:
                    disconnected.append(ws)

            for ws in disconnected:
                if ws in active_websockets:
                    active_websockets.remove(ws)

        await asyncio.sleep(0.1)

# ==========================================
# ENDPOINTS DE STREAMING DE VIDEO MJPEG
# ==========================================
def generate_mjpeg(lane_id: int):
    while True:
        light_state = "GREEN" if controller.lights[f"lane{lane_id}"]["green"] else ("YELLOW" if controller.lights[f"lane{lane_id}"]["yellow"] else "RED")
        jpeg_bytes, _ = vision.get_jpeg_frame(lane_id, light_state)
        if jpeg_bytes:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + jpeg_bytes + b"\r\n")
        time_to_wait = 0.04  # ~25 FPS
        import time
        time.sleep(time_to_wait)

@app.get("/video_feed/lane1")
def video_feed_lane1():
    return StreamingResponse(generate_mjpeg(1), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/video_feed/lane2")
def video_feed_lane2():
    return StreamingResponse(generate_mjpeg(2), media_type="multipart/x-mixed-replace; boundary=frame")

# ==========================================
# WEBSOCKET EN TIEMPO REAL
# ==========================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    try:
        while True:
            # Mantener conexión abierta y recibir posibles comandos
            data = await websocket.receive_text()
            if data == "force_change":
                controller.force_change()
            elif data == "toggle_pause":
                controller.pause(not controller.paused)
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)

# ==========================================
# REST API
# ==========================================
@app.get("/api/telemetry")
def api_telemetry():
    t = controller.get_telemetry()
    t["detection_mode"] = vision.mode
    t["use_simulation"] = vision.use_simulation
    t["vision"] = vision.get_summary()
    t["gpio_ready"] = hardware_link["gpio_ready"]
    t["stats"] = get_stats()
    return t

@app.get("/api/history")
def api_history(limit: int = 50):
    return get_history(limit)

@app.get("/api/stats")
def api_stats():
    return get_stats()

@app.post("/api/settings")
def api_settings(settings: SettingsPayload):
    target_l1 = settings.lane1_url if settings.lane1_url is not None else settings.esp32_lane1_url
    target_l2 = settings.lane2_url if settings.lane2_url is not None else settings.esp32_lane2_url

    if target_l1 is not None:
        controller.lane1_url = target_l1
    if target_l2 is not None:
        controller.lane2_url = target_l2
    
    # Actualizar flujos de video en el motor de visión
    vision.set_camera_urls(controller.lane1_url, controller.lane2_url)

    if settings.detection_mode is not None:
        vision.set_mode(settings.detection_mode)
    if settings.use_simulation is not None:
        vision.toggle_simulation(settings.use_simulation)
    return {"status": "ok", "mode": vision.mode, "simulation": vision.use_simulation}

@app.post("/api/control/force_change")
def api_force_change():
    controller.force_change()
    return {"status": "ok"}

@app.post("/api/control/pause")
def api_pause(payload: PausePayload):
    controller.pause(payload.paused)
    return {"status": "ok", "paused": controller.paused}

@app.post("/api/control/clear_history")
def api_clear_history():
    clear_history()
    return {"status": "ok"}

# Montar frontend estático al final
if os.path.exists(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

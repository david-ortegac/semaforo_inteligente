"""
=============================================================================
SEMÁFORO INTELIGENTE ADAPTATIVO - AGENTE DUAL PARA 1x RASPBERRY PI ZERO 2 W
=============================================================================
Configuración:
- 1 sola Raspberry Pi Zero 2 W controlando ambos carriles de la maqueta:
  * Carril 1: Raspberry Pi Camera v1.3 (OmniVision OV5647) por conector CSI.
  * Carril 2: Cámara Web USB conectada vía cable adaptador Micro-USB OTG.
- Control de 6 LEDs (2 semáforos completos) mediante el conector GPIO de 40 pines:
  * Semáforo Carril 1:
      - Rojo:     GPIO 17 (Pin 11)
      - Amarillo: GPIO 27 (Pin 13)
      - Verde:    GPIO 22 (Pin 15)
  * Semáforo Carril 2:
      - Rojo:     GPIO 23 (Pin 16)
      - Amarillo: GPIO 24 (Pin 18)
      - Verde:    GPIO 25 (Pin 22)
  * GNDs: Pines 6, 9, 14, 20, 30, 34, 39.

Endpoints expuestos:
- /lane1/stream  ó  /stream/lane1  : Video MJPEG Carril 1 (CSI v1.3)
- /lane2/stream  ó  /stream/lane2  : Video MJPEG Carril 2 (USB Webcam)
- /lane1/leds?r=1&y=0&g=0         : Control semáforo Carril 1
- /lane2/leds?r=1&y=0&g=0         : Control semáforo Carril 2
- /leds?lane=1&r=1&y=0&g=0        : Endpoint unificado de control
- /status                         : Diagnóstico completo del hardware y cámaras
- /                               : Panel web de prueba en vivo (2 cámaras + 6 LEDs)
=============================================================================
"""

import argparse
import glob
import os
import socket
import sys
import threading
import time
from typing import Optional, Tuple

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import uvicorn

# Detección de hardware Raspberry Pi
HAS_PICAMERA = False
HAS_GPIO = False

try:
    from picamera2 import Picamera2
    HAS_PICAMERA = True
except ImportError:
    pass

try:
    from gpiozero import LED
    HAS_GPIO = True
except ImportError:
    pass

parser = argparse.ArgumentParser(description="Agente Dual para Raspberry Pi 400 (2 Webcams USB)")
parser.add_argument("--port", type=int, default=8000, help="Puerto HTTP para el servicio")
parser.add_argument("--cam1-index", type=int, default=None, help="Índice de dispositivo V4L2 para cámara USB Carril 1 (/dev/videoX)")
parser.add_argument("--cam2-index", type=int, default=None, help="Índice de dispositivo V4L2 para cámara USB Carril 2 (/dev/videoY)")
parser.add_argument("--usb-index", type=int, default=None, help="Alias de compatibilidad para cam1-index")
parser.add_argument("--width", type=int, default=320, help="Ancho de frame")
parser.add_argument("--height", type=int, default=240, help="Alto de frame")
parser.add_argument("--fps", type=int, default=25, help="FPS objetivo")

# Pines Carril 1
parser.add_argument("--pin-l1-red", type=int, default=17, help="GPIO BCM Rojo Carril 1")
parser.add_argument("--pin-l1-yellow", type=int, default=27, help="GPIO BCM Amarillo Carril 1")
parser.add_argument("--pin-l1-green", type=int, default=22, help="GPIO BCM Verde Carril 1")

# Pines Carril 2
parser.add_argument("--pin-l2-red", type=int, default=23, help="GPIO BCM Rojo Carril 2")
parser.add_argument("--pin-l2-yellow", type=int, default=24, help="GPIO BCM Amarillo Carril 2")
parser.add_argument("--pin-l2-green", type=int, default=25, help="GPIO BCM Verde Carril 2")

args, _ = parser.parse_known_args()

app = FastAPI(title="Agente Dual - Raspberry Pi 400 (2 Webcams USB + 6 LEDs)")

# ==========================================
# GESTIÓN DE HARDWARE LEDS (GPIO)
# ==========================================
lights_state = {
    "lane1": {"red": 1, "yellow": 0, "green": 0},
    "lane2": {"red": 1, "yellow": 0, "green": 0}
}

gpio_leds = {
    "lane1": {"red": None, "yellow": None, "green": None},
    "lane2": {"red": None, "yellow": None, "green": None}
}

if HAS_GPIO:
    try:
        gpio_leds["lane1"]["red"] = LED(args.pin_l1_red)
        gpio_leds["lane1"]["yellow"] = LED(args.pin_l1_yellow)
        gpio_leds["lane1"]["green"] = LED(args.pin_l1_green)

        gpio_leds["lane2"]["red"] = LED(args.pin_l2_red)
        gpio_leds["lane2"]["yellow"] = LED(args.pin_l2_yellow)
        gpio_leds["lane2"]["green"] = LED(args.pin_l2_green)
        print("[GPIO] Inicializados 6 pines BCM para Carril 1 y Carril 2.")
    except Exception as e:
        print(f"[GPIO] Advertencia al configurar pines: {e}")
else:
    print("[GPIO] Modo simulación activo (gpiozero no detectado).")

def apply_leds(lane_key: str, r: int, y: int, g: int):
    """Actualiza el estado físico de los LEDs para el carril indicado."""
    lights_state[lane_key]["red"] = 1 if r > 0 else 0
    lights_state[lane_key]["yellow"] = 1 if y > 0 else 0
    lights_state[lane_key]["green"] = 1 if g > 0 else 0

    if HAS_GPIO:
        leds = gpio_leds.get(lane_key, {})
        for color, val in lights_state[lane_key].items():
            led_obj = leds.get(color)
            if led_obj:
                if val:
                    led_obj.on()
                else:
                    led_obj.off()

# Iniciar ambos semáforos en Rojo por seguridad
apply_leds("lane1", 1, 0, 0)
apply_leds("lane2", 1, 0, 0)

# ==========================================
# UTILIDADES DE SISTEMA CON CACHÉ
# ==========================================
_cached_cpu_temp = "N/A"
_last_cpu_temp_time = 0.0

def get_cpu_temp() -> str:
    global _cached_cpu_temp, _last_cpu_temp_time
    now = time.time()
    if (now - _last_cpu_temp_time) < 3.0:
        return _cached_cpu_temp
    _last_cpu_temp_time = now
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            t = float(f.read().strip()) / 1000.0
            _cached_cpu_temp = f"{t:.1f} °C"
    except Exception:
        _cached_cpu_temp = "N/A"
    return _cached_cpu_temp

_cached_ip = "127.0.0.1"
_last_ip_time = 0.0

def get_local_ip() -> str:
    global _cached_ip, _last_ip_time
    now = time.time()
    if (now - _last_ip_time) < 5.0:
        return _cached_ip
    _last_ip_time = now
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        _cached_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return _cached_ip

# ==========================================
# GESTIÓN DE 2 WEBCAMS USB INDEPENDIENTES
# ==========================================
def find_available_v4l2_indices() -> list:
    """Descubre los dispositivos V4L2 reales con capacidad de captura."""
    found = []
    # 1. Probar enlaces by-id para preferir índices de dispositivos físicos
    by_id = sorted(glob.glob("/dev/v4l/by-id/*"))
    for link in by_id:
        try:
            real_path = os.path.realpath(link)
            idx = int(real_path.replace("/dev/video", ""))
            if idx not in found:
                found.append(idx)
        except Exception:
            pass

    # 2. Agregar índices estándar comunes en Linux
    for idx in [0, 2, 4, 1, 3]:
        if idx not in found:
            found.append(idx)

    valid = []
    for idx in found:
        dev = f"/dev/video{idx}"
        if not os.path.exists(dev):
            continue
        try:
            cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                time.sleep(0.04)
                if ret and frame is not None and frame.size > 0:
                    valid.append(idx)
        except Exception:
            pass
    return valid

# Resolver índices de cámaras para Carril 1 y Carril 2
cam1_arg = args.cam1_index if args.cam1_index is not None else args.usb_index
cam2_arg = args.cam2_index

discovered = find_available_v4l2_indices()

if cam1_arg is not None:
    cam1_target = cam1_arg
else:
    cam1_target = discovered[0] if len(discovered) > 0 else 0

if cam2_arg is not None:
    cam2_target = cam2_arg
else:
    cam2_target = discovered[1] if len(discovered) > 1 else (2 if cam1_target != 2 else 1)

print(f"[Cámaras] Carril 1 -> /dev/video{cam1_target} | Carril 2 -> /dev/video{cam2_target}")

# Hilos y buffers de video desacoplados
latest_jpeg_l1: Optional[bytes] = None
latest_jpeg_l2: Optional[bytes] = None
lock_l1 = threading.Lock()
lock_l2 = threading.Lock()

def make_fallback_frame(lane_id: int, label: str) -> bytes:
    """Genera un cuadro sintético informativo cuando la webcam no está disponible."""
    img = np.full((args.height, args.width, 3), (35, 38, 42), dtype=np.uint8)
    cv2.putText(img, f"CARRIL {lane_id}: {label}", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 200), 1)
    cv2.putText(img, f"Temp: {get_cpu_temp()}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
    cv2.putText(img, f"IP: {get_local_ip()}:{args.port}", (10, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
    
    # Dibujar estado del semáforo en el fallback
    lane_key = f"lane{lane_id}"
    cur_state = lights_state[lane_key]
    color = (0, 0, 255) if cur_state["red"] else ((0, 220, 255) if cur_state["yellow"] else (0, 255, 0))
    cv2.circle(img, (280, 40), 12, color, -1)
    cv2.circle(img, (280, 40), 12, (255, 255, 255), 1)

    _, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return enc.tobytes()

class USBCameraWorker(threading.Thread):
    """Hilo independiente de captura para una webcam USB con autoreconexión continua."""
    def __init__(self, lane_id: int, dev_idx: int):
        super().__init__(daemon=True)
        self.lane_id = lane_id
        self.dev_idx = dev_idx
        self.cap: Optional[cv2.VideoCapture] = None
        self.connected = False

    def _open_camera(self) -> bool:
        dev_path = f"/dev/video{self.dev_idx}"
        if not os.path.exists(dev_path):
            self.connected = False
            return False
        try:
            cap = cv2.VideoCapture(self.dev_idx, cv2.CAP_V4L2)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
                cap.set(cv2.CAP_PROP_FPS, args.fps)
                for _ in range(3):
                    cap.read()
                self.cap = cap
                self.connected = True
                print(f"[Cámara Carril {self.lane_id}] Abierta exitosamente en /dev/video{self.dev_idx}.")
                return True
        except Exception as e:
            print(f"[Cámara Carril {self.lane_id}] Error al abrir /dev/video{self.dev_idx}: {e}")
        return False

    def run(self):
        global latest_jpeg_l1, latest_jpeg_l2
        lock = lock_l1 if self.lane_id == 1 else lock_l2
        fail_count = 0

        while True:
            if self.cap is None or not self.cap.isOpened():
                if not self._open_camera():
                    fallback = make_fallback_frame(self.lane_id, f"Esperando Webcam (/dev/video{self.dev_idx})...")
                    with lock:
                        if self.lane_id == 1:
                            latest_jpeg_l1 = fallback
                        else:
                            latest_jpeg_l2 = fallback
                    time.sleep(1.5)
                    continue

            ret, frame = self.cap.read()
            if ret and frame is not None and frame.size > 0:
                fail_count = 0
                if frame.shape[1] != args.width or frame.shape[0] != args.height:
                    frame = cv2.resize(frame, (args.width, args.height))
                ret_enc, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ret_enc:
                    frame_bytes = enc.tobytes()
                    with lock:
                        if self.lane_id == 1:
                            latest_jpeg_l1 = frame_bytes
                        else:
                            latest_jpeg_l2 = frame_bytes
            else:
                fail_count += 1
                if fail_count > 25:
                    print(f"[Cámara Carril {self.lane_id}] Desconectada o sin señal en /dev/video{self.dev_idx}. Reconectando...")
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None
                    self.connected = False
                    fail_count = 0
                    time.sleep(1.0)
                    continue

            time.sleep(1.0 / args.fps)

# Iniciar los dos hilos de captura independientes
worker_l1 = USBCameraWorker(1, cam1_target)
worker_l2 = USBCameraWorker(2, cam2_target)
worker_l1.start()
worker_l2.start()

def mjpeg_generator(lane: int):
    """Generador HTTP para streaming MJPEG."""
    lock = lock_l1 if lane == 1 else lock_l2
    while True:
        with lock:
            frame_bytes = latest_jpeg_l1 if lane == 1 else latest_jpeg_l2

        if frame_bytes:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")

        time.sleep(1.0 / args.fps)

# ==========================================
# ENDPOINTS REST & STREAMING
# ==========================================

# Streams de Video MJPEG
@app.get("/lane1/stream")
@app.get("/stream/lane1")
def stream_lane1():
    """Stream de video para Carril 1 (Pi Camera v1.3)."""
    return StreamingResponse(mjpeg_generator(1), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/lane2/stream")
@app.get("/stream/lane2")
def stream_lane2():
    """Stream de video para Carril 2 (Webcam USB)."""
    return StreamingResponse(mjpeg_generator(2), media_type="multipart/x-mixed-replace; boundary=frame")

# Control de LEDs Carril 1
@app.get("/lane1/leds")
def leds_lane1(r: Optional[int] = None, y: Optional[int] = None, g: Optional[int] = None):
    nr = lights_state["lane1"]["red"] if r is None else r
    ny = lights_state["lane1"]["yellow"] if y is None else y
    ng = lights_state["lane1"]["green"] if g is None else g
    apply_leds("lane1", nr, ny, ng)
    return {"lane": 1, "red": nr, "yellow": ny, "green": ng, "status": "ok"}

# Control de LEDs Carril 2
@app.get("/lane2/leds")
def leds_lane2(r: Optional[int] = None, y: Optional[int] = None, g: Optional[int] = None):
    nr = lights_state["lane2"]["red"] if r is None else r
    ny = lights_state["lane2"]["yellow"] if y is None else y
    ng = lights_state["lane2"]["green"] if g is None else g
    apply_leds("lane2", nr, ny, ng)
    return {"lane": 2, "red": nr, "yellow": ny, "green": ng, "status": "ok"}

# Endpoint unificado de control de semáforos
@app.get("/leds")
def leds_unified(lane: int = 1, r: Optional[int] = None, y: Optional[int] = None, g: Optional[int] = None):
    lane_key = f"lane{lane}"
    if lane_key not in lights_state:
        return JSONResponse({"error": "Carril no válido (use 1 o 2)"}, status_code=400)

    nr = lights_state[lane_key]["red"] if r is None else r
    ny = lights_state[lane_key]["yellow"] if y is None else y
    ng = lights_state[lane_key]["green"] if g is None else g
    apply_leds(lane_key, nr, ny, ng)
    return {"lane": lane, "red": nr, "yellow": ny, "green": ng, "status": "ok"}

def gpio_is_ready() -> bool:
    """True cuando los 6 LEDs tienen un pin GPIO abierto."""
    required = (
        gpio_leds["lane1"]["red"],
        gpio_leds["lane1"]["yellow"],
        gpio_leds["lane1"]["green"],
        gpio_leds["lane2"]["red"],
        gpio_leds["lane2"]["yellow"],
        gpio_leds["lane2"]["green"],
    )
    return all(led is not None for led in required)

@app.get("/status")
def status():
    return {
        "device": "Raspberry Pi 400 (Nodo Dual 2x USB)",
        "gpio_ready": gpio_is_ready(),
        "ip": get_local_ip(),
        "port": args.port,
        "cpu_temp": get_cpu_temp(),
        "lanes": {
            "lane1": {
                "camera": f"Webcam USB Carril 1 (/dev/video{cam1_target})",
                "online": worker_l1.connected,
                "stream_url": f"http://{get_local_ip()}:{args.port}/lane1/stream",
                "leds_url": f"http://{get_local_ip()}:{args.port}/lane1/leds",
                "state": lights_state["lane1"],
                "gpios": {"red": args.pin_l1_red, "yellow": args.pin_l1_yellow, "green": args.pin_l1_green}
            },
            "lane2": {
                "camera": f"Webcam USB Carril 2 (/dev/video{cam2_target})",
                "online": worker_l2.connected,
                "stream_url": f"http://{get_local_ip()}:{args.port}/lane2/stream",
                "leds_url": f"http://{get_local_ip()}:{args.port}/lane2/leds",
                "state": lights_state["lane2"],
                "gpios": {"red": args.pin_l2_red, "yellow": args.pin_l2_yellow, "green": args.pin_l2_green}
            }
        }
    }

# Panel web de diagnóstico
@app.get("/", response_class=HTMLResponse)
def index():
    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="utf-8">
        <title>Raspberry Pi 400 - Nodo Dual (Carril 1 & Carril 2)</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f1f5f9; padding: 25px; }}
            .container {{ max-width: 900px; margin: 0 auto; }}
            header {{ text-align: center; margin-bottom: 25px; }}
            h1 {{ font-size: 24px; color: #38bdf8; margin-bottom: 8px; }}
            .chips {{ display: flex; justify-content: center; gap: 10px; margin-bottom: 20px; }}
            .chip {{ background: #1e293b; padding: 6px 14px; border-radius: 20px; font-size: 13px; border: 1px solid #334155; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
            @media (max-width: 700px) {{ .grid {{ grid-template-columns: 1fr; }} }}
            .card {{ background: #1e293b; border-radius: 12px; padding: 18px; border: 1px solid #334155; }}
            .card-title {{ font-size: 18px; font-weight: bold; margin-bottom: 6px; display: flex; justify-content: space-between; }}
            .camera-type {{ font-size: 12px; color: #94a3b8; margin-bottom: 12px; display: block; }}
            .view-stream {{ width: 100%; border-radius: 8px; border: 2px solid #475569; background: #020617; }}
            .btn-row {{ display: flex; gap: 8px; margin-top: 15px; justify-content: center; }}
            .btn {{ padding: 8px 16px; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; }}
            .btn-g {{ background: #10b981; color: white; }}
            .btn-y {{ background: #f59e0b; color: black; }}
            .btn-r {{ background: #ef4444; color: white; }}
            .footer-info {{ text-align: center; margin-top: 25px; color: #64748b; font-size: 13px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>🚦 Raspberry Pi 400 - Nodo Dual</h1>
                <p>Control Centralizado de 2 Carriles con 2 Webcams USB y 6 Pines GPIO</p>
                <div class="chips">
                    <span class="chip">IP: {get_local_ip()}:{args.port}</span>
                    <span class="chip">CPU Temp: {get_cpu_temp()}</span>
                    <span class="chip">Host: semaforo.local</span>
                </div>
            </header>

            <div class="grid">
                <!-- CARRIL 1 -->
                <div class="card">
                    <div class="card-title">
                        <span>CARRIL 1</span>
                        <span style="color: #10b981;">ACTIVO</span>
                    </div>
                    <span class="camera-type">📷 Webcam USB Carril 1 (/dev/video{cam1_target})</span>
                    <img class="view-stream" src="/lane1/stream" alt="Feed Carril 1">
                    <div class="btn-row">
                        <button class="btn btn-g" onclick="fetch('/lane1/leds?r=0&y=0&g=1')">🟢 Verde</button>
                        <button class="btn btn-y" onclick="fetch('/lane1/leds?r=0&y=1&g=0')">🟡 Amarillo</button>
                        <button class="btn btn-r" onclick="fetch('/lane1/leds?r=1&y=0&g=0')">🔴 Rojo</button>
                    </div>
                </div>

                <!-- CARRIL 2 -->
                <div class="card">
                    <div class="card-title">
                        <span>CARRIL 2</span>
                        <span style="color: #10b981;">ACTIVO</span>
                    </div>
                    <span class="camera-type">📷 Webcam USB Carril 2 (/dev/video{cam2_target})</span>
                    <img class="view-stream" src="/lane2/stream" alt="Feed Carril 2">
                    <div class="btn-row">
                        <button class="btn btn-g" onclick="fetch('/lane2/leds?r=0&y=0&g=1')">🟢 Verde</button>
                        <button class="btn btn-y" onclick="fetch('/lane2/leds?r=0&y=1&g=0')">🟡 Amarillo</button>
                        <button class="btn btn-r" onclick="fetch('/lane2/leds?r=1&y=0&g=0')">🔴 Rojo</button>
                    </div>
                </div>
            </div>

            <div class="footer-info">
                Pines GPIO BCM: Carril 1 (Rojo: {args.pin_l1_red}, Amarillo: {args.pin_l1_yellow}, Verde: {args.pin_l1_green}) | 
                Carril 2 (Rojo: {args.pin_l2_red}, Amarillo: {args.pin_l2_yellow}, Verde: {args.pin_l2_green})
            </div>
        </div>
    </body>
    </html>
    """

if __name__ == "__main__":
    print("=================================================================")
    print(f"Iniciando Agente Dual en http://0.0.0.0:{args.port}")
    print(f"  - Feed Carril 1 (USB /dev/video{cam1_target}): http://{get_local_ip()}:{args.port}/lane1/stream")
    print(f"  - Feed Carril 2 (USB /dev/video{cam2_target}): http://{get_local_ip()}:{args.port}/lane2/stream")
    print(f"  - Panel de control web:                        http://{get_local_ip()}:{args.port}/")
    print("=================================================================")
    uvicorn.run(app, host="0.0.0.0", port=args.port)

import cv2
import numpy as np
import time
import math
import random
from typing import Tuple, Dict, Any, Optional

VEHICLE_CLASS_NAMES = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    67: "car",  # En vista cenital 90° de maqueta, YOLO COCO detecta miniaturas Hot Wheels como 'cell phone'
}
VEHICLE_CLASS_LABELS = {
    "car": "Auto",
    "motorcycle": "Moto",
    "bus": "Bus",
    "truck": "Camion",
}

# Lazos virtuales / Zonas de parada de precisión calibradas para la maqueta (320x240)
LANE_SPOTS = {
    1: [
        {"name": "Subcarril 1 (Superior)", "box": (180, 48, 305, 98)},
        {"name": "Subcarril 2 (Central)",  "box": (180, 104, 305, 154)},
        {"name": "Subcarril 3 (Inferior)", "box": (180, 160, 305, 210)},
    ],
    2: [
        {"name": "Subcarril 1 (Izquierdo)", "box": (148, 38, 202, 152)},
        {"name": "Subcarril 2 (Derecho)",   "box": (212, 38, 268, 152)},
    ],
}

class VisionTracker:
    """
    Motor de Visión por Computadora para Detección de Vehículos de Juguete.
    
    Características:
    - Modos de detección:
      1. 'mog2' (Lazos virtuales / sustracción de fondo + contornos y masa)
      2. 'yolo' (Inferencia neuronal con YOLOv8-Nano)
    - Soporte de ingesta desde:
      a) Stream HTTP desde Raspberry Pi Zero 2 W (Pi Cam v1.3 CSI y Webcam USB a 320x240)
      b) Generador de simulación sintética de tráfico de carritos en maqueta
    - Anotación gráfica con Bounding Boxes, ROI de detección, estado del semáforo y FPS.
    """

    def __init__(self, mode: str = "yolo"):
        self.mode = mode  # 'yolo' o 'mog2'
        self.min_car_area = 1200  # Umbral de área en píxeles para 320x240
        self.confidence_threshold = 0.30

        # Substractores de fondo para carril 1 y carril 2 (modo fallback MOG2)
        self.subtractor_l1 = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)
        self.subtractor_l2 = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=False)

        # Cargar modelo YOLOv8 si está disponible
        self.yolo_model = None
        try:
            from ultralytics import YOLO
            self.yolo_model = YOLO("yolov8n.pt")
            print("[VisionTracker] Modelo YOLOv8n cargado exitosamente.")
        except Exception as e:
            print(f"[VisionTracker] YOLOv8 no inicializado (usando modo MOG2 de alta precisión): {e}")
            if self.mode == "yolo":
                self.mode = "mog2"

        # Estado del simulador de tráfico
        self.use_simulation = False
        self.sim_time = time.time()
        
        # Carritos simulados en cada carril: [{'x', 'y', 'speed', 'color', 'w', 'h', 'stopped'}]
        self.sim_cars_l1 = [
            {"pos": 50, "speed": 1.8, "color": (40, 50, 220), "len": 40, "stopped": False},
            {"pos": 140, "speed": 1.6, "color": (220, 160, 30), "len": 45, "stopped": False}
        ]
        self.sim_cars_l2 = [
            {"pos": 80, "speed": 1.7, "color": (50, 200, 50), "len": 42, "stopped": False}
        ]
        self.last_spawn_l1 = time.time()
        self.last_spawn_l2 = time.time()

        # URLs de las cámaras físicas
        self.cam_url_l1: Optional[str] = None
        self.cam_url_l2: Optional[str] = None
        self.cap_l1: Optional[cv2.VideoCapture] = None
        self.cap_l2: Optional[cv2.VideoCapture] = None

        # Sincronización y caché de frames para evitar contención entre hilos
        import threading
        self.lock_l1 = threading.Lock()
        self.lock_l2 = threading.Lock()
        self.latest_jpeg_l1: Optional[bytes] = None
        self.latest_jpeg_l2: Optional[bytes] = None
        self.latest_count_l1: int = 0
        self.latest_count_l2: int = 0
        self.last_ts_l1: float = 0.0
        self.last_ts_l2: float = 0.0
        self.camera_online = {1: False, 2: False}
        self.agent_camera_online = {1: None, 2: None}
        self.class_counts = {
            1: {name: 0 for name in VEHICLE_CLASS_NAMES.values()},
            2: {name: 0 for name in VEHICLE_CLASS_NAMES.values()},
        }

    def _format_stream_url(self, url: Optional[str]) -> Optional[str]:
        if not url or not url.strip():
            return None
        cleaned = url.strip().rstrip('/')
        if not (cleaned.endswith('/stream') or cleaned.endswith('.mjpg') or cleaned.endswith('.cgi')):
            cleaned = f"{cleaned}/stream"
        return cleaned

    def set_camera_urls(self, url_l1: Optional[str], url_l2: Optional[str]):
        """Configura las URLs de las cámaras (Raspberry Pi o ESP32) y reinicia capturas."""
        formatted_l1 = self._format_stream_url(url_l1)
        formatted_l2 = self._format_stream_url(url_l2)

        if formatted_l1 != self.cam_url_l1:
            if self.cap_l1 is not None:
                self.cap_l1.release()
            self.cap_l1 = None
            self.cam_url_l1 = formatted_l1

        if formatted_l2 != self.cam_url_l2:
            if self.cap_l2 is not None:
                self.cap_l2.release()
            self.cap_l2 = None
            self.cam_url_l2 = formatted_l2

        if self.cam_url_l1 or self.cam_url_l2:
            self.use_simulation = False
        else:
            self.use_simulation = True


    def toggle_simulation(self, enable: bool):
        self.use_simulation = enable

    def note_agent_cameras(self, lane1_online: Optional[bool], lane2_online: Optional[bool]):
        """Recibe si cada webcam USB está abierta en el agente de la Raspberry Pi."""
        if lane1_online is not None:
            self.agent_camera_online[1] = lane1_online
        if lane2_online is not None:
            self.agent_camera_online[2] = lane2_online

    def set_mode(self, new_mode: str):
        if new_mode in ["mog2", "yolo"]:
            self.mode = new_mode

    def _generate_simulated_frame(self, lane_id: int, light_state: str) -> np.ndarray:
        """Genera un cuadro sintético realista de 320x240 con carritos circulando en maqueta."""
        frame = np.full((240, 320, 3), (35, 38, 42), dtype=np.uint8)
        
        # Dibujar carretera / pista
        cv2.rectangle(frame, (60, 0), (260, 240), (55, 58, 62), -1)
        # Líneas laterales
        cv2.line(frame, (62, 0), (62, 240), (230, 230, 230), 2)
        cv2.line(frame, (258, 0), (258, 240), (230, 230, 230), 2)
        # Línea discontinua central
        for y in range(0, 240, 30):
            cv2.line(frame, (160, y), (160, y + 16), (240, 200, 30), 2)

        # Línea de detención del semáforo
        stop_y = 170
        cv2.line(frame, (65, stop_y), (255, stop_y), (255, 255, 255), 3)
        cv2.putText(frame, "STOP LINE", (70, stop_y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)

        # Zona virtual de espera (ROI)
        roi_pts = np.array([[70, 70], [250, 70], [250, 170], [70, 170]], np.int32)
        cv2.polylines(frame, [roi_pts], True, (0, 180, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, f"ROI ESPERA (C{lane_id})", (75, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 180, 255), 1)

        # Actualizar física de carritos
        is_green = (light_state == "GREEN")
        cars = self.sim_cars_l1 if lane_id == 1 else self.sim_cars_l2
        
        # Control de generación de nuevos carritos
        now = time.time()
        last_spawn = self.last_spawn_l1 if lane_id == 1 else self.last_spawn_l2
        if now - last_spawn > random.uniform(5.0, 9.0) and len(cars) < 3:
            colors = [(40, 50, 230), (230, 160, 40), (40, 210, 80), (200, 50, 200), (30, 220, 240)]
            cars.append({
                "pos": 0,
                "speed": random.uniform(1.4, 2.0),
                "color": random.choice(colors),
                "len": random.randint(38, 48),
                "stopped": False
            })
            if lane_id == 1:
                self.last_spawn_l1 = now
            else:
                self.last_spawn_l2 = now

        # Mover y dibujar cada carrito
        remaining_cars = []
        for i, car in enumerate(cars):
            lead_pos = cars[i-1]["pos"] if i > 0 else 999
            
            # Frenar ante semáforo en rojo o auto adelante
            car_front = car["pos"] + car["len"]
            stop_condition = False

            if not is_green:
                if car_front >= (stop_y - 5) and car["pos"] < stop_y:
                    stop_condition = True
            
            if i > 0 and (cars[i-1]["pos"] - car_front) < 15:
                stop_condition = True

            if stop_condition:
                car["stopped"] = True
            else:
                car["stopped"] = False
                car["pos"] += car["speed"]

            # Si el carrito cruzó toda la pantalla, sale
            if car["pos"] < 250:
                remaining_cars.append(car)

                # Dibujar carrito
                cx = 160
                cy = int(car["pos"] + car["len"] / 2)
                cw = 32
                ch = car["len"]
                x1, y1 = cx - cw // 2, cy - ch // 2
                x2, y2 = cx + cw // 2, cy + ch // 2

                # Chasis y sombra
                cv2.rectangle(frame, (x1 + 2, y1 + 2), (x2 + 2, y2 + 2), (15, 15, 15), -1)
                cv2.rectangle(frame, (x1, y1), (x2, y2), car["color"], -1)
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 255), 1)

                # Parabrisas delantero y trasero
                cv2.rectangle(frame, (x1 + 4, y1 + 6), (x2 - 4, y1 + 12), (40, 40, 40), -1)
                cv2.rectangle(frame, (x1 + 4, y2 - 12), (x2 - 4, y2 - 6), (40, 40, 40), -1)

                # Ruedas
                cv2.circle(frame, (x1 - 1, y1 + 8), 3, (10, 10, 10), -1)
                cv2.circle(frame, (x2 + 1, y1 + 8), 3, (10, 10, 10), -1)
                cv2.circle(frame, (x1 - 1, y2 - 8), 3, (10, 10, 10), -1)
                cv2.circle(frame, (x2 + 1, y2 - 8), 3, (10, 10, 10), -1)

        if lane_id == 1:
            self.sim_cars_l1 = remaining_cars
        else:
            self.sim_cars_l2 = remaining_cars

        return frame

    def process_lane(self, lane_id: int, light_state: str) -> Tuple[np.ndarray, int]:
        lock = self.lock_l1 if lane_id == 1 else self.lock_l2
        with lock:
            return self._process_lane_internal(lane_id, light_state)

    def _process_lane_internal(self, lane_id: int, light_state: str) -> Tuple[np.ndarray, int]:
        """
        Procesa el carril indicado:
        - Obtiene el cuadro (real o simulado).
        - Aplica el algoritmo de visión (MOG2 o YOLO).
        - Devuelve el cuadro anotado con bounding boxes y el conteo de vehículos en espera.
        """
        frame = None

        if self.use_simulation:
            frame = self._generate_simulated_frame(lane_id, light_state)
        else:
            # Captura desde Raspberry Pi Zero 2 W (o cámara física configurada)
            url = self.cam_url_l1 if lane_id == 1 else self.cam_url_l2
            cap = self.cap_l1 if lane_id == 1 else self.cap_l2
            if url:
                if cap is None or not cap.isOpened():
                    cap = cv2.VideoCapture(url)
                    if lane_id == 1:
                        self.cap_l1 = cap
                    else:
                        self.cap_l2 = cap

                ret, read_frame = cap.read()
                if ret and read_frame is not None:
                    frame = cv2.resize(read_frame, (320, 240))
                else:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    if lane_id == 1:
                        self.cap_l1 = None
                    else:
                        self.cap_l2 = None

        agent_online = self.agent_camera_online.get(lane_id)
        if self.use_simulation:
            frame_is_live = frame is not None
            self.camera_online[lane_id] = False
        elif agent_online is False:
            frame_is_live = False
            self.camera_online[lane_id] = False
        else:
            frame_is_live = frame is not None
            self.camera_online[lane_id] = frame is not None

        if frame is None:
            self.class_counts[lane_id] = {name: 0 for name in VEHICLE_CLASS_NAMES.values()}
            frame = np.full((240, 320, 3), (25, 28, 32), dtype=np.uint8)
            cv2.putText(frame, f"CARRIL {lane_id}: SIN WEBCAM USB", (15, 100),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 200), 1)
            cv2.putText(frame, "Esperando /dev/video de la maqueta...", (15, 130),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.40, (180, 180, 180), 1)

        count = 0
        annotated = frame.copy()
        lane_classes = {name: 0 for name in VEHICLE_CLASS_NAMES.values()}

        # Zona de espera (ROI) estándar para simulación: Y entre 60 y 175, X entre 60 y 260
        roi_y_min, roi_y_max = 60, 175
        roi_x_min, roi_x_max = 60, 260

        if self.use_simulation:
            # Modo Simulación Sintética
            if self.mode == "mog2":
                subtractor = self.subtractor_l1 if lane_id == 1 else self.subtractor_l2
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                blurred = cv2.GaussianBlur(gray, (5, 5), 0)
                fg_mask = subtractor.apply(blurred)
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                cleaned = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
                cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel)
                roi_mask = np.zeros_like(cleaned)
                roi_mask[roi_y_min:roi_y_max, roi_x_min:roi_x_max] = 255
                detected_in_roi = cv2.bitwise_and(cleaned, roi_mask)
                contours, _ = cv2.findContours(detected_in_roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                detected_boxes = []
                for cnt in contours:
                    area = cv2.contourArea(cnt)
                    if area > self.min_car_area:
                        x, y, w, h = cv2.boundingRect(cnt)
                        cars_in_contour = max(1, round(area / (self.min_car_area * 1.8)))
                        count += cars_in_contour
                        detected_boxes.append((x, y, w, h, cars_in_contour))
                for x, y, w, h, n in detected_boxes:
                    cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    label = f"Auto ({n})" if n > 1 else "Auto Juguete"
                    cv2.putText(annotated, label, (x, max(15, y - 5)), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1, cv2.LINE_AA)
            elif self.mode == "yolo" and self.yolo_model is not None:
                try:
                    results = self.yolo_model.predict(frame, conf=self.confidence_threshold, verbose=False)
                    for r in results:
                        for box in r.boxes:
                            cls_id = int(box.cls[0])
                            class_name = VEHICLE_CLASS_NAMES.get(cls_id)
                            if class_name is None:
                                continue
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            cx = (x1 + x2) // 2
                            cy = (y1 + y2) // 2
                            if roi_x_min <= cx <= roi_x_max and roi_y_min <= cy <= roi_y_max:
                                count += 1
                                lane_classes[class_name] += 1
                                cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 100, 0), 2)
                                conf = float(box.conf[0])
                                label = VEHICLE_CLASS_LABELS[class_name]
                                cv2.putText(annotated, f"{label} {conf:.2f}", (x1, max(15, y1 - 5)),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 100, 0), 1)
                except Exception as e:
                    print(f"[VisionTracker] Fallo de inferencia YOLOv8 en simulador: {e}")

        elif frame_is_live:
            # Modo Maqueta Física con Cámaras Reales: Lazos Virtuales + Fusión YOLO
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            spots = LANE_SPOTS.get(lane_id, [])
            spot_details = []

            for spot in spots:
                x1, y1, x2, y2 = spot["box"]
                patch = gray[y1:y2, x1:x2]
                if patch.size > 0:
                    edges = cv2.Canny(patch, 30, 90)
                    edge_pct = (float(np.sum(edges > 0)) / float(edges.size)) * 100.0
                    std_dev = float(np.std(patch))
                    is_occupied = (edge_pct >= 5.5) or (edge_pct >= 4.0 and std_dev >= 22.0)
                else:
                    edge_pct, std_dev, is_occupied = 0.0, 0.0, False
                spot_details.append((spot, is_occupied, edge_pct, std_dev))

            # Detección YOLOv8 si está habilitada
            yolo_boxes = []
            if self.mode == "yolo" and self.yolo_model is not None:
                try:
                    results = self.yolo_model.predict(frame, conf=0.20, verbose=False)
                    for r in results:
                        for box in r.boxes:
                            cls_id = int(box.cls[0])
                            class_name = VEHICLE_CLASS_NAMES.get(cls_id)
                            if class_name is None:
                                continue
                            bx1, by1, bx2, by2 = map(int, box.xyxy[0])
                            bcx = (bx1 + bx2) // 2
                            bcy = (by1 + by2) // 2
                            # Filtro de calzada
                            in_lane = (160 <= bcx <= 310 and 35 <= bcy <= 185) if lane_id == 1 else (130 <= bcx <= 285 and 20 <= bcy <= 185)
                            if in_lane:
                                conf = float(box.conf[0])
                                yolo_boxes.append((bx1, by1, bx2, by2, class_name, conf, bcx, bcy))
                except Exception as e:
                    print(f"[VisionTracker] Error YOLO en maqueta: {e}")

            # Integración de resultados por spot
            for spot, is_occupied, edge_pct, std_dev in spot_details:
                sx1, sy1, sx2, sy2 = spot["box"]
                # Buscar detecciones YOLO que caigan dentro del spot
                matched_yolo = [yb for yb in yolo_boxes if (sx1 - 15 <= yb[6] <= sx2 + 15 and sy1 - 15 <= yb[7] <= sy2 + 15)]

                if is_occupied or len(matched_yolo) > 0:
                    count += 1
                    lane_classes["car"] += 1
                    if matched_yolo:
                        yb = matched_yolo[0]
                        cv2.rectangle(annotated, (yb[0], yb[1]), (yb[2], yb[3]), (0, 255, 0), 2)
                        cv2.putText(annotated, f"Auto {yb[5]:.2f}", (yb[0], max(15, yb[1] - 4)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 0), 1)
                    else:
                        cv2.rectangle(annotated, (sx1, sy1), (sx2, sy2), (0, 255, 0), 2)
                        cv2.putText(annotated, f"Auto (Lazo)", (sx1, max(15, sy1 - 4)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 255, 0), 1)
                else:
                    cv2.rectangle(annotated, (sx1, sy1), (sx2, sy2), (90, 90, 90), 1)
                    cv2.putText(annotated, "Libre", (sx1 + 4, sy1 + 14),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (150, 150, 150), 1)

            # Detectar vehículos adicionales identificados por YOLO fuera de los lazos principales
            for yb in yolo_boxes:
                already_claimed = any(
                    (spot["box"][0] - 15 <= yb[6] <= spot["box"][2] + 15 and spot["box"][1] - 15 <= yb[7] <= spot["box"][3] + 15)
                    for spot, occ, _, _ in spot_details if occ
                )
                if not already_claimed:
                    count += 1
                    lane_classes["car"] += 1
                    cv2.rectangle(annotated, (yb[0], yb[1]), (yb[2], yb[3]), (0, 255, 0), 2)
                    cv2.putText(annotated, f"Auto {yb[5]:.2f}", (yb[0], max(15, yb[1] - 4)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 0), 1)

        self.class_counts[lane_id] = lane_classes

        # Overlay informativo en el cuadro de video
        cv2.rectangle(annotated, (5, 5), (155, 42), (20, 20, 20), -1)
        cv2.rectangle(annotated, (5, 5), (155, 42), (60, 60, 60), 1)
        
        mode_text = "Lazo + YOLO" if self.mode == "yolo" else "Lazo Virtual"
        cv2.putText(annotated, f"CARRIL {lane_id} | {mode_text}", (10, 20), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (220, 220, 220), 1)
        cv2.putText(annotated, f"DETECTADOS: {count}", (10, 36), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (50, 255, 50) if count >= 1 else (0, 200, 255), 1)

        return annotated, count

    def get_jpeg_frame(self, lane_id: int, light_state: str) -> Tuple[bytes, int]:
        """Devuelve el frame codificado en JPEG y el conteo de vehículos de forma thread-safe."""
        lock = self.lock_l1 if lane_id == 1 else self.lock_l2
        with lock:
            frame, count = self._process_lane_internal(lane_id, light_state)
            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret:
                jpeg_bytes = jpeg.tobytes()
                if lane_id == 1:
                    self.latest_jpeg_l1 = jpeg_bytes
                    self.latest_count_l1 = count
                    self.last_ts_l1 = time.time()
                else:
                    self.latest_jpeg_l2 = jpeg_bytes
                    self.latest_count_l2 = count
                    self.last_ts_l2 = time.time()
                return jpeg_bytes, count
            return b"", count

    def get_count_fast(self, lane_id: int, light_state: str) -> int:
        """Obtiene el conteo de vehículos de forma thread-safe aprovechando caché reciente."""
        now = time.time()
        last_t = self.last_ts_l1 if lane_id == 1 else self.last_ts_l2
        if (now - last_t) < 0.25:
            return self.latest_count_l1 if lane_id == 1 else self.latest_count_l2

        lock = self.lock_l1 if lane_id == 1 else self.lock_l2
        with lock:
            _, count = self._process_lane_internal(lane_id, light_state)
            if lane_id == 1:
                self.latest_count_l1 = count
                self.last_ts_l1 = time.time()
            else:
                self.latest_count_l2 = count
                self.last_ts_l2 = time.time()
            return count

    def get_summary(self) -> Dict[str, Any]:
        """Resume el motor YOLOv8, las clases de vehículos y el estado de cada webcam."""
        return {
            "mode": self.mode,
            "model": "yolov8n.pt",
            "model_loaded": self.yolo_model is not None,
            "classes": list(VEHICLE_CLASS_NAMES.values()),
            "counts": {
                "lane1": dict(self.class_counts[1]),
                "lane2": dict(self.class_counts[2]),
            },
            "cameras": {
                "lane1": self.camera_online[1],
                "lane2": self.camera_online[2],
            },
            "simulation": self.use_simulation,
        }

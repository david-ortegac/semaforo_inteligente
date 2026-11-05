# Semáforo inteligente en Raspberry Pi 400

Control adaptativo de un cruce vehicular de **dos carriles** en una **Raspberry Pi 400** montada sobre un **disco de estado sólido (SSD) conectado por USB**. Cada carril cuenta con una webcam USB y un semáforo de tres luces (Rojo, Amarillo, Verde) controlado por GPIO. Al encender, la Pi activa automáticamente su propio punto de acceso Wi-Fi y levanta los servicios mediante daemons del sistema para permitir acceso inmediato por SSH y visualización en el panel de control web.

La detección principal de vehículos se realiza en tiempo real mediante visión por computadora e inteligencia artificial con **YOLOv8** (modelo `yolov8n.pt`), identificando automóviles, autobuses, camiones y motocicletas en la zona virtual de espera (ROI). La sustracción de fondo (MOG2) queda disponible como modo alternativo/fallback.

---

## Objetivo y Especificaciones

| Componente / Parámetro | Configuración en Producción |
| :--- | :--- |
| **Placa** | Raspberry Pi 400 (Quad-Core Cortex-A72 @ 1.8 GHz, 4 GB RAM, GPIO 40 pines, 2× USB 3.0, 1× USB 2.0) |
| **Almacenamiento** | Disco SSD externo USB 3.0 (mayor tasa de I/O para el sistema, swap y pesos neuronales) |
| **Cámaras** | 2 webcams USB independientes (Carril 1 en USB 3.0, Carril 2 en USB 2.0 o Hub USB) |
| **Motor de Detección** | **YOLOv8** (Nano `yolov8n.pt`) en tiempo real para conteo de vehículos en la ROI de espera (Fallback: MOG2) |
| **Servicio de Arranque** | Daemon `systemd` (`semaforo_completo.service`) activo y habilitado al encender la Pi |
| **Punto de Acceso Wi-Fi** | Daemon nativo NetworkManager con autoconexión persistente (`Semaforo-Inteligente` / `semaforo`) |
| **Acceso SSH** | `ssh semaforo@semaforo.local` o `ssh semaforo@10.42.0.1` (Clave: `semaforo`) |
| **Panel Web** | `http://semaforo.local:8000` o `http://10.42.0.1:8000` |
| **Ciclo de Semáforo** | FSM adaptativa: Verde base de 10 s, extensible en vivo a 15 s por demanda, o corte a 5 s si carril opuesto está vacío |
| **Amarillo de Seguridad** | 2 s de transición obligatoria entre cambios de vía |

---

## Detección Vehicular con YOLOv8

El sistema procesa los fotogramas capturados por cada webcam aplicando inferencia con **YOLOv8-Nano**:

1. **Clases Detectadas:** Filtra específicamente las clases vehiculares del dataset COCO:
   - `car` (ID 2)
   - `motorcycle` (ID 3)
   - `bus` (ID 5)
   - `truck` (ID 7)
2. **Zona de Espera (ROI):** Para cada carril se delimita un polígono virtual de detección. El algoritmo evalúa el centroide de cada vehículo detectado; si se encuentra dentro de la zona de detención previa a la línea de parada, se suma al conteo del carril correspondiente.
3. **Optimización en CPU:** Se procesa a resolución optimizada (320×240) y con hilos desacoplados de captura y lectura mediante colas y bloqueos atómicos, permitiendo mantener la frecuencia de toma de decisiones del semáforo a 10 Hz.
4. **Fallback MOG2:** En caso de que el modelo neuronal no esté presente o se requiera reducir consumo de CPU, el sistema conmuta suavemente al análisis por sustracción de fondo adaptativa (MOG2).

---

## Reglas del semáforo

El ciclo no se salta: `VERDE carril 1` → `AMARILLO carril 1` → `VERDE carril 2` → `AMARILLO carril 2`. El otro carril permanece en rojo. Nunca hay dos verdes a la vez. Una cola de 2 o más carritos no abre el verde antes de tiempo: solo cambia la duración del verde que ya está encendido.

1. **Verde base: 10 s.**
2. **Verde de 15 s, en vivo.** Mientras el verde está abierto se comparan los dos conteos. Si el carril en verde queda con más vehículos que el otro, el objetivo de esa fase pasa a 15 s y ya no vuelve a 10 s. Si la diferencia aparece después de los 10 s, el verde sigue hasta 15 s.
3. **Verde de 5 s.** Si a los 5 s el carril contrario sigue en 0, se cierra el verde y entra el amarillo. Esta regla gana a la de 15 s: contrario vacío no alarga el verde.
4. **Amarillo: 2 s.** Siempre, antes del verde siguiente.
5. **Registro.** Cada cierre de verde guarda en SQLite carril, duración, causa, hora y conteos de los dos carriles.

Ejemplos, con el carril 1 ya en verde:

| Carril 1 | Carril 2 | Duración del verde actual |
| :---: | :---: | :--- |
| 3 | 0 | 5 s. El contrario está vacío |
| 4 | 1 | 15 s. El carril en verde tiene más carritos y el otro no está en 0 |
| 1 | 4 | 10 s. El carril en verde no supera al otro |
| 2 | 2 | 10 s |
| 0 | 0 | 5 s |

Si durante esos 10 s entran más carritos al carril en verde y superan al otro, esa misma fase pasa a 15 s.

---

## Hardware y Conexiones

```text
+-------------------------------------------------------------+
|                      Raspberry Pi 400                       |
|   USB 3.0 (x2)              USB 2.0 (x1)      GPIO 40 pines |
+---------+------------------------+------------------+-------+
          |                        |                  |
    +-----+------+                 |                  |
    |            |                 |                  |
SSD USB 3.0  Webcam USB 1    Webcam USB 2        6 LEDs + 220 Ω
 (Sistema)    (Carril 1)      (Carril 2)    Carril 1: GPIO 17, 27, 22
                                            Carril 2: GPIO 23, 24, 25
```

### Distribución de Puertos USB
* **USB 3.0 (Puerto Azul 1):** Disco de estado sólido (SSD) externo. Permite arranque rápido de Linux, alta tasa de lectura de pesos neuronales (`yolov8n.pt`) y manejo eficiente de memoria swap sin degradación de tarjeta SD.
* **USB 3.0 (Puerto Azul 2):** Webcam USB del **Carril 1**.
* **USB 2.0 (Puerto Negro):** Webcam USB del **Carril 2** (o conexión mediante HUB USB 3.0 si se requiere ancho de banda idéntico).

### Conexión de LEDs (GPIO)
Cada LED lleva una resistencia limitadora de **220 Ω o 330 Ω** en serie entre el pin GPIO y el ánodo. Los cátodos van conectados a GND.

| Señal | Carril | Pin físico | GPIO (BCM) |
| :--- | :---: | :---: | :---: |
| Rojo | 1 | 11 | 17 |
| Amarillo | 1 | 13 | 27 |
| Verde | 1 | 15 | 22 |
| GND | 1 | 6 o 9 | GND |
| Rojo | 2 | 16 | 23 |
| Amarillo | 2 | 18 | 24 |
| Verde | 2 | 22 | 25 |
| GND | 2 | 20 o 30 | GND |

---

## Servicios en Segundo Plano (Daemons)

El sistema opera de forma completamente desatendida y autónoma gracias a dos daemons:

### 1. Daemon de Aplicación: `semaforo_completo.service` (`systemd`)
Gestiona el ciclo de vida del agente de hardware y del servidor web:
- **Archivo de servicio:** `/etc/systemd/system/semaforo_completo.service`
- **Ruta de ejecución:** `/home/semaforo/semaforo/raspberry_pi/start_all.sh` (con symlink `/home/semaforo/semaforo_inteligente`)
- **Comandos de diagnóstico y administración:**
  ```bash
  # Ver estado del servicio
  sudo systemctl status semaforo_completo

  # Reiniciar servicio
  sudo systemctl restart semaforo_completo

  # Ver logs en tiempo real
  journalctl -u semaforo_completo -f
  ```

### 2. Daemon de Red y Hotspot (`NetworkManager`)
NetworkManager levanta automáticamente el punto de acceso en el arranque:
- **Perfil activo:** `Semaforo-Inteligente` en la interfaz `wlan0`.
- **SSID:** `Semaforo-Inteligente` (o `semaforo`)
- **IP Gateway:** `10.42.0.1`
- **Hostname mDNS:** `semaforo.local`

---

## Acceso al Sistema

### Conexión por SSH
Desde cualquier dispositivo conectado a la misma red local o al hotspot Wi-Fi de la Raspberry Pi:
```bash
ssh semaforo@semaforo.local
# O por IP directa:
ssh semaforo@10.42.0.1
```
* **Usuario:** `semaforo`
* **Contraseña:** `semaforo`

### Panel Web de Monitoreo
Abre en tu navegador:
* `http://semaforo.local:8000` o `http://10.42.0.1:8000`

---

## Instalación y Configuración en la Pi 400

En Raspberry Pi OS Bookworm (64 bits, instalado en el SSD USB):

```bash
# 1. Actualizar sistema y herramientas base
sudo apt update
sudo apt install -y python3-pip python3-opencv python3-gpiozero v4l-utils network-manager avahi-daemon openssh-server
sudo systemctl enable --now ssh

# 2. Descargar o actualizar proyecto
cd /home/semaforo
git clone https://github.com/<tu-usuario>/semaforo_inteligente.git semaforo
cd semaforo/raspberry_pi

# 3. Instalar dependencias Python (incluyendo YOLOv8)
pip install -r requirements_pi.txt --break-system-packages
pip install ultralytics --break-system-packages

# 4. Configurar punto de acceso Wi-Fi persistente
chmod +x configurar_hotspot.sh start_all.sh
./configurar_hotspot.sh

# 5. Instalar y habilitar el servicio daemon systemd
sudo cp semaforo_completo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now semaforo_completo.service
```

Comprobar que cada webcam USB se reconoce como un dispositivo V4L2 independiente:
```bash
v4l2-ctl --list-devices
```

---

## Estructura del Proyecto

```text
semaforo_inteligente/
├── raspberry_pi/
│   ├── dual_camera_agent.py       # Captura de 2 webcams USB, control GPIO de LEDs y MJPEG
│   ├── configurar_hotspot.sh      # Script de configuración del Hotspot NetworkManager
│   ├── start_all.sh               # Lanzador conjunto del agente y del servidor
│   ├── semaforo_completo.service  # Unidad systemd para autoinicio en boot
│   └── requirements_pi.txt        # Dependencias de hardware y servidor
├── server/
│   ├── app.py                     # API FastAPI, WebSocket de telemetría y streaming
│   ├── traffic_controller.py      # Máquina de estados finitos (FSM) de control adaptativo
│   ├── vision_tracker.py          # Inferencia YOLOv8 (yolov8n) y fallback MOG2
│   └── database.py                # Persistencia y trazabilidad histórica en SQLite
├── web/                           # Dashboard interactivo en tiempo real
└── tests/test_traffic_fsm.py      # Suite de pruebas unitarias
```

---

## Estado de Validación y Componentes Operativos

1. **Almacenamiento SSD USB:** Validado en funcionamiento sobre `/dev/sda2` en la Raspberry Pi 400.
2. **Daemons en ejecución:** Servicio `semaforo_completo.service` de `systemd` activo, autoinicio habilitado y `KillMode=control-group`.
3. **Detección con YOLOv8:** `torch 2.14.1+cpu` y `ultralytics` instalados en la Pi 400. Modelo `yolov8n.pt` verificado en inferencia directa sobre CPU y configurado como motor predeterminado (`detection_mode: yolo`).
4. **Captura dual independiente:** Separación total de captura de video en [dual_camera_agent.py](file:///home/david/Documentos/GitHub/semaforo_inteligente/raspberry_pi/dual_camera_agent.py). Carril 1 (`/dev/video0`) y Carril 2 (`/dev/video2`) operan en hilos independientes (`USBCameraWorker`) con reconexión automática y sin duplicación de fotogramas.
5. **Punto de Acceso Wi-Fi:** Hotspot NetworkManager activo con SSID `semaforo` y clave `semaforo` en interfaz `wlan0`.
6. **Estabilidad de sockets y descriptores:** Caché temporal de lecturas del sistema (temperatura de CPU e IP) implementada en [dual_camera_agent.py](file:///home/david/Documentos/GitHub/semaforo_inteligente/raspberry_pi/dual_camera_agent.py) y limpieza previa de puertos con `fuser` en [start_all.sh](file:///home/david/Documentos/GitHub/semaforo_inteligente/raspberry_pi/start_all.sh), eliminando los fallos `[Errno 24: Too many open files]` y `[Errno 98: Address already in use]`.

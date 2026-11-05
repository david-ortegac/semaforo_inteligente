# Raspberry Pi 400: Sistema Autónomo en SSD USB

Control integral de cruce vehicular de dos carriles con dos webcams USB y dos semáforos de 3 luces (6 LEDs en GPIO). El sistema corre sobre Raspberry Pi OS (Bookworm 64-bit) instalado directamente en un **disco SSD externo por USB 3.0**.

## Distribución de Puertos USB

La Raspberry Pi 400 cuenta con dos puertos USB 3.0 (azules) y un puerto USB 2.0 (negro):

1. **USB 3.0 (Puerto Azul 1):** Disco SSD externo (sistema operativo, swap y almacenamiento de pesos YOLOv8).
2. **USB 3.0 (Puerto Azul 2):** Webcam USB del Carril 1.
3. **USB 2.0 (Puerto Negro):** Webcam USB del Carril 2 (o conectar ambas webcams mediante HUB USB 3.0).

Comprobar detección de cámaras:
```bash
v4l2-ctl --list-devices
```

## Detección con YOLOv8

El conteo e identificación de vehículos utiliza **YOLOv8-Nano** (`yolov8n.pt`):
- Filtra clases COCO: carros (`car`), buses (`bus`), motos (`motorcycle`) y camiones (`truck`).
- Evalúa si el centroide de cada vehículo detectado cae en la ROI de espera antes de la línea de parada.
- Fallback automático a MOG2 si el modelo no está disponible.

## Conexiones GPIO (6 LEDs)

Resistencias de 220 Ω o 330 Ω en serie con el ánodo de cada LED. Cátodos a GND.

| Señal | Carril | Pin físico | GPIO (BCM) |
| :--- | :---: | :---: | :---: |
| Rojo | 1 | 11 | 17 |
| Amarillo | 1 | 13 | 27 |
| Verde | 1 | 15 | 22 |
| Rojo | 2 | 16 | 23 |
| Amarillo | 2 | 18 | 24 |
| Verde | 2 | 22 | 25 |

GND Carril 1: pin 6 o 9. GND Carril 2: pin 20 o 30.

## Daemons y Servicios Automáticos

El sistema arranca de forma 100% desatendida mediante dos daemons:

1. **Servicio systemd (`semaforo_completo.service`):**
   - Ejecuta `/home/semaforo/semaforo/raspberry_pi/start_all.sh`.
   - Inicia `dual_camera_agent.py` (puerto 8080) y `server/app.py` (puerto 8000).
   - Comandos útiles:
     ```bash
     sudo systemctl status semaforo_completo
     sudo systemctl restart semaforo_completo
     journalctl -u semaforo_completo -f
     ```

2. **Hotspot Wi-Fi (`NetworkManager`):**
   - Perfil activo: `Semaforo-Inteligente` en interfaz `wlan0`.
   - Autoconexión habilitada en boot (`connection.autoconnect yes`).
   - IP de la Pi: `10.42.0.1` / Hostname: `semaforo.local`.

## Acceso

- **SSH:** `ssh semaforo@semaforo.local` (o `ssh semaforo@10.42.0.1`), contraseña: `semaforo`.
- **Panel Web:** `http://semaforo.local:8000` o `http://10.42.0.1:8000`.

Para el funcionamiento detallado de la máquina de estados y las reglas adaptativas (10 s base, 15 s extendido, 5 s corte), consultar el [README principal](../README.md).


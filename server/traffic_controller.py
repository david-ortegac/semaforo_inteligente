import asyncio
import time
import requests
from typing import Dict, Any, Callable, Optional
from database import record_cycle

class TrafficController:
    """
    Controlador de la Máquina de Estados para Semáforos Inteligentes Adaptativos.
    
    Reglas implementadas:
    1. Verde base de 10 segundos.
    2. Si el carril en verde tiene más vehículos que el contrario (incluso si el contrario está en 0),
       esa misma fase se amplía a 18 segundos (EXTENDED_GREEN_TIME) para descongestionar el tráfico.
    3. A los 5 segundos (EMPTY_LANE_CHECK_TIME), si el carril activo en verde está vacío (0 vehículos),
       el verde cierra anticipadamente a amarillo para ceder el paso sin demoras.
    4. Amarillo de 2 segundos de seguridad antes de ceder el paso.
    5. Cada cierre de verde queda registrado con trazabilidad en SQLite.
    """

    def __init__(self, 
                 lane1_url: Optional[str] = None,
                 lane2_url: Optional[str] = None,
                 on_state_change: Optional[Callable[[Dict[str, Any]], None]] = None,
                 **kwargs):
        self.lane1_url = lane1_url or kwargs.get("esp32_lane1_url")
        self.lane2_url = lane2_url or kwargs.get("esp32_lane2_url")
        self.on_state_change = on_state_change

        # Estado inicial
        self.current_state = "LANE1_GREEN"
        self.active_lane = 1
        self.phase_start_time = time.time()
        self.is_extended = False
        self.empty_check_done = False
        self.is_running = False
        self.paused = False

        # Sensores / conteos actuales
        self.vehicles_lane1 = 0
        self.vehicles_lane2 = 0

        # Semáforos físicos (Rojo, Amarillo, Verde)
        self.lights = {
            "lane1": {"red": 0, "yellow": 0, "green": 1},
            "lane2": {"red": 1, "yellow": 0, "green": 0}
        }

        # Tiempos configurables (en segundos)
        self.BASE_GREEN_TIME = 10.0
        self.EXTENDED_GREEN_TIME = 18.0
        self.EMPTY_LANE_CHECK_TIME = 5.0
        self.YELLOW_TIME = 2.0

        self._task: Optional[asyncio.Task] = None
        self._last_hardware_push = 0.0
        self._last_hardware_error_log = 0.0

    def update_vehicle_counts(self, count_lane1: int, count_lane2: int):
        """Actualiza el conteo en tiempo real desde el motor de visión."""
        self.vehicles_lane1 = count_lane1
        self.vehicles_lane2 = count_lane2

    def get_telemetry(self) -> Dict[str, Any]:
        """Devuelve el estado completo actual para la interfaz web."""
        elapsed = time.time() - self.phase_start_time
        
        target_time = self.BASE_GREEN_TIME
        if "YELLOW" in self.current_state:
            target_time = self.YELLOW_TIME
        elif self.is_extended:
            target_time = self.EXTENDED_GREEN_TIME

        remaining = max(0.0, target_time - elapsed)

        return {
            "state": self.current_state,
            "active_lane": self.active_lane,
            "elapsed_seconds": round(elapsed, 1),
            "target_seconds": round(target_time, 1),
            "remaining_seconds": round(remaining, 1),
            "is_extended": self.is_extended,
            "paused": self.paused,
            "vehicles_lane1": self.vehicles_lane1,
            "vehicles_lane2": self.vehicles_lane2,
            "active_vehicles": self.vehicles_lane1 if self.active_lane == 1 else self.vehicles_lane2,
            "opposite_vehicles": self.vehicles_lane2 if self.active_lane == 1 else self.vehicles_lane1,
            "lights": self.lights,
            "timing": {
                "base_green": self.BASE_GREEN_TIME,
                "extended_green": self.EXTENDED_GREEN_TIME,
                "empty_check": self.EMPTY_LANE_CHECK_TIME,
                "yellow": self.YELLOW_TIME,
            },
        }

    async def start(self):
        """Inicia el bucle asíncrono de la máquina de estados."""
        if self.is_running:
            return
        self.is_running = True
        self.phase_start_time = time.time()
        self._apply_lights_for_state(self.current_state)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Detiene el bucle de control."""
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def pause(self, val: bool):
        """Pausa o reanuda la temporización."""
        self.paused = val

    def force_change(self):
        """Fuerza un cambio manual de carril inmediatamente."""
        if "GREEN" in self.current_state:
            self._transition_to_yellow("manual_override")

    def _apply_lights_for_state(self, state: str):
        """Actualiza las luces lógicas y despacha a los ESP32-CAM."""
        if state == "LANE1_GREEN":
            self.lights["lane1"] = {"red": 0, "yellow": 0, "green": 1}
            self.lights["lane2"] = {"red": 1, "yellow": 0, "green": 0}
        elif state == "LANE1_YELLOW":
            self.lights["lane1"] = {"red": 0, "yellow": 1, "green": 0}
            self.lights["lane2"] = {"red": 1, "yellow": 0, "green": 0}
        elif state == "LANE2_GREEN":
            self.lights["lane1"] = {"red": 1, "yellow": 0, "green": 0}
            self.lights["lane2"] = {"red": 0, "yellow": 0, "green": 1}
        elif state == "LANE2_YELLOW":
            self.lights["lane1"] = {"red": 1, "yellow": 0, "green": 0}
            self.lights["lane2"] = {"red": 0, "yellow": 1, "green": 0}

        # Despachar comando a los microcontroladores en segundo plano
        asyncio.create_task(self._send_hardware_commands())

        if self.on_state_change:
            try:
                self.on_state_change(self.get_telemetry())
            except Exception as e:
                print(f"[TrafficController] Error en callback de estado: {e}")

    async def _send_hardware_commands(self):
        """Envía comandos HTTP a los semáforos físicos sin bloquear la ejecución."""
        def _send(url, r, y, g):
            if not url:
                return
            try:
                endpoint = f"{url.rstrip('/')}/leds?r={r}&y={y}&g={g}"
                requests.get(endpoint, timeout=0.8)
            except Exception as error:
                now = time.time()
                if now - self._last_hardware_error_log > 10:
                    print(f"[TrafficController] No se pudieron actualizar los LEDs en {url}: {error}")
                    self._last_hardware_error_log = now

        l1 = self.lights["lane1"]
        l2 = self.lights["lane2"]
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _send, self.lane1_url, l1["red"], l1["yellow"], l1["green"])
        await loop.run_in_executor(None, _send, self.lane2_url, l2["red"], l2["yellow"], l2["green"])

    def _transition_to_yellow(self, cause: str):
        """Pasa de Verde a Amarillo y guarda el registro de trazabilidad."""
        duration = time.time() - self.phase_start_time
        lane = self.active_lane

        # Guardar en SQLite
        record_cycle(
            lane=lane,
            duration_seconds=duration,
            cause=cause,
            vehicles_lane1=self.vehicles_lane1,
            vehicles_lane2=self.vehicles_lane2
        )

        if self.current_state == "LANE1_GREEN":
            self.current_state = "LANE1_YELLOW"
        else:
            self.current_state = "LANE2_YELLOW"

        self.phase_start_time = time.time()
        self.is_extended = False
        self._apply_lights_for_state(self.current_state)

    def _transition_to_green(self):
        """Pasa de Amarillo al Verde del carril contrario."""
        if self.current_state == "LANE1_YELLOW":
            self.current_state = "LANE2_GREEN"
            self.active_lane = 2
        else:
            self.current_state = "LANE1_GREEN"
            self.active_lane = 1

        self.phase_start_time = time.time()
        self.is_extended = False
        self.empty_check_done = False
        self._apply_lights_for_state(self.current_state)

    async def _run_loop(self):
        """Bucle de reloj de alta frecuencia (10 Hz)."""
        while self.is_running:
            now = time.time()
            if now - self._last_hardware_push >= 1.0:
                self._last_hardware_push = now
                asyncio.create_task(self._send_hardware_commands())
            if not self.paused:
                elapsed = time.time() - self.phase_start_time
                active_vehicles = self.vehicles_lane1 if self.active_lane == 1 else self.vehicles_lane2
                opposite_vehicles = self.vehicles_lane2 if self.active_lane == 1 else self.vehicles_lane1

                if "GREEN" in self.current_state:
                    # 1. Chequeo de carril vacío a los 5 segundos:
                    # Si el carril que tiene verde no tiene vehículos (0), se corta anticipadamente
                    # para no hacer esperar al carril contrario.
                    if (not self.empty_check_done) and elapsed >= self.EMPTY_LANE_CHECK_TIME:
                        self.empty_check_done = True
                        if active_vehicles == 0:
                            self._transition_to_yellow("empty_lane_cut_5s")
                            await asyncio.sleep(0.1)
                            continue

                    # 2. Regla de prioridad y extensión por mayor demanda vehicular:
                    # Si el carril activo tiene más vehículos que el contrario (incluso si el contrario está en 0),
                    # se amplía la fase a tiempo extendido (18s) para descongestionar el carril.
                    if active_vehicles > opposite_vehicles:
                        self.is_extended = True

                    limit = self.EXTENDED_GREEN_TIME if self.is_extended else self.BASE_GREEN_TIME
                    if elapsed >= limit:
                        cause = f"extension_{int(self.EXTENDED_GREEN_TIME)}s" if self.is_extended else f"base_{int(self.BASE_GREEN_TIME)}s"
                        self._transition_to_yellow(cause)
                        await asyncio.sleep(0.1)
                        continue

                elif "YELLOW" in self.current_state:
                    # Fin de la fase de seguridad de amarillo
                    if elapsed >= self.YELLOW_TIME:
                        self._transition_to_green()
                        await asyncio.sleep(0.1)
                        continue

            await asyncio.sleep(0.1)

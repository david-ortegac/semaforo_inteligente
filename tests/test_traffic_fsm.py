import unittest
import os
import sys
import time
import asyncio

# Añadir directorio server al path
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "server"))
sys.path.insert(0, SERVER_DIR)

from database import init_db, record_cycle, get_history, get_stats, clear_history
from traffic_controller import TrafficController
from vision_tracker import VisionTracker

class TestTrafficSystem(unittest.TestCase):
    def setUp(self):
        self.test_db = os.path.join(os.path.dirname(__file__), "test_trace.db")
        init_db(self.test_db)
        clear_history(self.test_db)

    def tearDown(self):
        if os.path.exists(self.test_db):
            os.remove(self.test_db)

    def test_database_traceability(self):
        # 1. Registrar ciclos de prueba
        id1 = record_cycle(1, 15.0, "base_15s", 3, 1, self.test_db)
        id2 = record_cycle(2, 20.0, "extension_20s", 1, 2, self.test_db)
        id3 = record_cycle(1, 5.0, "empty_lane_cut_5s", 0, 4, self.test_db)

        self.assertGreater(id3, id2)
        self.assertGreater(id2, id1)

        # 2. Consultar historial
        hist = get_history(10, self.test_db)
        self.assertEqual(len(hist), 3)
        self.assertEqual(hist[0]["cause"], "empty_lane_cut_5s")
        self.assertEqual(hist[1]["cause"], "extension_20s")
        self.assertEqual(hist[2]["cause"], "base_15s")

        # 3. Consultar estadísticas
        stats = get_stats(self.test_db)
        self.assertEqual(stats["total_cycles"], 3)
        self.assertEqual(stats["cause_counts"]["base_15s"], 1)
        self.assertEqual(stats["cause_counts"]["extension_20s"], 1)
        self.assertEqual(stats["cause_counts"]["empty_lane_cut_5s"], 1)
        self.assertEqual(stats["avg_duration"], round((15.0 + 20.0 + 5.0) / 3, 1))

    def test_vision_tracker_simulation(self):
        tracker = VisionTracker(mode="mog2")
        frame, count = tracker.process_lane(1, "GREEN")
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (240, 320, 3))
        self.assertIsInstance(count, int)

        jpeg, count_jpeg = tracker.get_jpeg_frame(2, "RED")
        self.assertTrue(len(jpeg) > 0)
        self.assertIsInstance(count_jpeg, int)

    def test_traffic_controller_rules(self):
        async def run_fsm_test():
            ctrl = TrafficController()
            # Ajustar tiempos a escala reducida para pruebas veloces
            ctrl.BASE_GREEN_TIME = 0.3
            ctrl.EXTENDED_GREEN_TIME = 0.5
            ctrl.EMPTY_LANE_CHECK_TIME = 0.1
            ctrl.YELLOW_TIME = 0.05

            # Corte al llegar al chequeo si el carril contrario está en 0
            ctrl.update_vehicle_counts(3, 0)
            await ctrl.start()
            await asyncio.sleep(0.18)
            self.assertIn("YELLOW", ctrl.current_state)
            self.assertEqual(ctrl.vehicles_lane2, 0)

            await ctrl.stop()

        asyncio.run(run_fsm_test())

    def test_dual_camera_agent_endpoints(self):
        RPI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "raspberry_pi"))
        sys.path.insert(0, RPI_DIR)
        import dual_camera_agent

        # Test status()
        data = dual_camera_agent.status()
        self.assertIn("Raspberry Pi", data["device"])
        self.assertIn("lane1", data["lanes"])
        self.assertIn("lane2", data["lanes"])

        # Test control LEDs lane1
        res1 = dual_camera_agent.leds_lane1(r=0, y=0, g=1)
        self.assertEqual(res1["green"], 1)
        self.assertEqual(res1["red"], 0)

        # Test control LEDs lane2
        res2 = dual_camera_agent.leds_lane2(r=1, y=0, g=0)
        self.assertEqual(res2["red"], 1)
        self.assertEqual(res2["green"], 0)

        # Test index()
        html = dual_camera_agent.index()
        self.assertIn("Nodo Dual", html)

if __name__ == "__main__":
    unittest.main()



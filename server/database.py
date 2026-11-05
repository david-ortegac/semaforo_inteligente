import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "traffic_traceability.db")

def init_db(db_path: str = DB_PATH):
    """Inicializa la base de datos de trazabilidad SQLite."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS traffic_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            lane INTEGER NOT NULL,
            duration_seconds REAL NOT NULL,
            cause TEXT NOT NULL,
            vehicles_lane1 INTEGER NOT NULL,
            vehicles_lane2 INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def record_cycle(lane: int, duration_seconds: float, cause: str, 
                 vehicles_lane1: int, vehicles_lane2: int, 
                 db_path: str = DB_PATH) -> int:
    """Registra una transición de semáforo con su duración y causa."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO traffic_cycles (timestamp, lane, duration_seconds, cause, vehicles_lane1, vehicles_lane2)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (timestamp, lane, round(duration_seconds, 1), cause, vehicles_lane1, vehicles_lane2))
    cycle_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return cycle_id

def get_history(limit: int = 100, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Obtiene el historial cronológico inverso de cambios de semáforo."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, timestamp, lane, duration_seconds, cause, vehicles_lane1, vehicles_lane2
        FROM traffic_cycles
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    history = [dict(row) for row in rows]
    conn.close()
    return history

def get_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Calcula métricas agregadas de trazabilidad para el dashboard."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Total de ciclos
    cursor.execute("SELECT COUNT(*) FROM traffic_cycles")
    total_cycles = cursor.fetchone()[0] or 0
    
    # Promedio de duración general y por carril
    cursor.execute("SELECT AVG(duration_seconds) FROM traffic_cycles")
    avg_duration = round(cursor.fetchone()[0] or 0.0, 1)
    
    cursor.execute("SELECT AVG(duration_seconds) FROM traffic_cycles WHERE lane = 1")
    avg_l1 = round(cursor.fetchone()[0] or 0.0, 1)
    
    cursor.execute("SELECT AVG(duration_seconds) FROM traffic_cycles WHERE lane = 2")
    avg_l2 = round(cursor.fetchone()[0] or 0.0, 1)
    
    # Distribución por causas
    cursor.execute("SELECT cause, COUNT(*) FROM traffic_cycles GROUP BY cause")
    cause_rows = cursor.fetchall()
    cause_counts = {row[0]: row[1] for row in cause_rows}
    
    # Total de vehículos contabilizados en eventos
    cursor.execute("SELECT SUM(vehicles_lane1), SUM(vehicles_lane2) FROM traffic_cycles")
    sum_row = cursor.fetchone()
    v1_total = sum_row[0] or 0
    v2_total = sum_row[1] or 0

    conn.close()

    return {
        "total_cycles": total_cycles,
        "avg_duration": avg_duration,
        "avg_duration_lane1": avg_l1,
        "avg_duration_lane2": avg_l2,
        "cause_counts": {
            "base_10s": cause_counts.get("base_10s", 0),
            "extension_15s": cause_counts.get("extension_15s", 0),
            "empty_opposite_cut_5s": cause_counts.get("empty_opposite_cut_5s", 0),
            "base_15s": cause_counts.get("base_15s", 0),
            "extension_20s": cause_counts.get("extension_20s", 0),
            "empty_lane_cut_5s": cause_counts.get("empty_lane_cut_5s", 0),
            "manual_override": cause_counts.get("manual_override", 0),
        },
        "total_vehicles_lane1": v1_total,
        "total_vehicles_lane2": v2_total
    }

def clear_history(db_path: str = DB_PATH):
    """Limpia el historial de ciclos."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM traffic_cycles")
    conn.commit()
    conn.close()

# Inicializar tabla al importar
init_db()

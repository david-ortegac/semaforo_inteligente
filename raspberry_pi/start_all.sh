#!/bin/bash
# ==============================================================================
# SCRIPT DE INICIO INTEGRADO: AGENTE DUAL + SERVIDOR WEB (FRONTEND)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=================================================================="
echo "Iniciando Sistema Completo de Semáforo Inteligente en Raspberry Pi"
echo "=================================================================="

# 0. Liberar puertos si quedaron ocupados por un reinicio previo
fuser -k 8080/tcp 2>/dev/null || true
fuser -k 8000/tcp 2>/dev/null || true
sleep 1

# 1. Iniciar Agente Dual de Cámaras USB y LEDs en puerto 8080
echo "[1/2] Iniciando Agente Dual de 2 Webcams USB independientes y LEDs en puerto 8080..."
start_agent() {
  python3 "$SCRIPT_DIR/dual_camera_agent.py" --port 8080 --cam1-index 0 --cam2-index 2 &
  AGENT_PID=$!
}

start_server() {
  export LANE1_URL="http://127.0.0.1:8080/lane1"
  export LANE2_URL="http://127.0.0.1:8080/lane2"
  export DETECTION_MODE="yolo"
  python3 "$ROOT_DIR/server/app.py" &
  SERVER_PID=$!
}

cleanup() {
  kill "$AGENT_PID" "$SERVER_PID" 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM

echo "[1/2] Iniciando Agente Dual de 2 Webcams USB independientes y LEDs en puerto 8080..."
start_agent
sleep 2

echo "[2/2] Iniciando Servidor Central y Dashboard Web en puerto 8000..."
start_server

echo "=================================================================="
echo "Semáforo Inteligente OPERATIVO al 100%"
echo "Acceso al Dashboard Web:"
echo "  - Desde Hotspot: http://10.42.0.1:8000"
echo "  - Por mDNS:      http://semaforo.local:8000"
echo "=================================================================="

while true; do
  if ! kill -0 "$AGENT_PID" 2>/dev/null; then
    echo "[agente] El control GPIO se detuvo. Reiniciando LEDs y cámaras..."
    start_agent
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[servidor] El panel se detuvo. Reiniciando..."
    start_server
  fi
  sleep 2
done

#!/bin/bash
# ==============================================================================
# SCRIPT DE CONFIGURACIÓN DE HOSTNAME Y HOTSPOT WI-FI PARA RASPBERRY PI ZERO 2 W
# ==============================================================================

set -e

HOSTNAME_NAME="semaforo"
SSID_NAME="semaforo"
WIFI_PASS="semaforo"

echo "=================================================================="
echo "Configurando Raspberry Pi 400 para Punto de Acceso Autónomo"
echo "=================================================================="

# 1. Configurar Hostname
echo "[1/3] Asignando Hostname: $HOSTNAME_NAME (acceso vía http://$HOSTNAME_NAME.local)"
sudo hostnamectl set-hostname "$HOSTNAME_NAME"
sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$HOSTNAME_NAME/g" /etc/hosts 2>/dev/null || true

# 2. Configurar Hotspot Wi-Fi permanente con NetworkManager (Raspberry Pi OS Bookworm)
echo "[2/3] Configurando red Wi-Fi propia (Access Point):"
echo "      - Red (SSID): $SSID_NAME"
echo "      - Clave:      $WIFI_PASS"
echo "      - IP Gateway: 10.42.0.1"

# Eliminar hotspot previo si existía (Semaforo-Inteligente, semaforo o Hotspot)
sudo nmcli connection delete id "Semaforo-Inteligente" 2>/dev/null || true
sudo nmcli connection delete id "$SSID_NAME" 2>/dev/null || true
sudo nmcli connection delete id "Hotspot" 2>/dev/null || true

# Crear nuevo punto de acceso
sudo nmcli device wifi hotspot ifname wlan0 ssid "$SSID_NAME" password "$WIFI_PASS"
sudo nmcli connection modify "$SSID_NAME" connection.autoconnect yes
sudo nmcli connection modify "$SSID_NAME" connection.autoconnect-priority 100

echo "[3/3] Punto de acceso Wi-Fi creado exitosamente y configurado para iniciar en cada booteo."
echo "=================================================================="
echo "LISTO: Cuando enciendas la Raspberry Pi, busca la red '$SSID_NAME'"
echo "conéctate con la clave '$WIFI_PASS' y abre en tu navegador:"
echo "http://10.42.0.1:8000  o  http://semaforo.local:8000"
echo "=================================================================="

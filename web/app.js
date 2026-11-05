/**
 * ============================================================================
 * SMART TRAFFIC CONTROL - DASHBOARD CLIENT JAVASCRIPT
 * ============================================================================
 * Manejo de WebSockets en tiempo real, renderizado de luces LED 3D,
 * cronómetro radial, actualización de historial de trazabilidad y controles.
 * ============================================================================
 */

document.addEventListener("DOMContentLoaded", () => {
    // Referencias al DOM
    const wsStatusChip = document.getElementById("ws-status-chip");
    const wsStatusText = document.getElementById("ws-status-text");
    const visionStatusChip = document.getElementById("vision-status-chip");
    const visionStatusText = document.getElementById("vision-status-text");
    const ledStatusChip = document.getElementById("led-status-chip");
    const ledStatusText = document.getElementById("led-status-text");
    const liveClock = document.getElementById("live-clock");

    // Luces Carril 1
    const l1Red = document.getElementById("light-l1-red");
    const l1Yellow = document.getElementById("light-l1-yellow");
    const l1Green = document.getElementById("light-l1-green");
    const statusL1Label = document.getElementById("status-l1-label");
    const valCarsLane1 = document.getElementById("val-cars-lane1");

    // Luces Carril 2
    const l2Red = document.getElementById("light-l2-red");
    const l2Yellow = document.getElementById("light-l2-yellow");
    const l2Green = document.getElementById("light-l2-green");
    const statusL2Label = document.getElementById("status-l2-label");
    const valCarsLane2 = document.getElementById("val-cars-lane2");

    // Temporizador Central
    const timerProgressBar = document.getElementById("timer-progress-bar");
    const timerPhaseName = document.getElementById("timer-phase-name");
    const timerRemaining = document.getElementById("timer-remaining");
    const timerElapsed = document.getElementById("timer-elapsed");
    const ruleStatusBadge = document.getElementById("rule-status-badge");
    const ruleExplanationText = document.getElementById("rule-explanation-text");

    // Controles
    const btnForceChange = document.getElementById("btn-force-change");
    const btnTogglePause = document.getElementById("btn-toggle-pause");
    const toggleSim = document.getElementById("toggle-sim");
    const btnModeMog2 = document.getElementById("btn-mode-mog2");
    const btnModeYolo = document.getElementById("btn-mode-yolo");

    // KPIs
    const kpiTotalCycles = document.getElementById("kpi-total-cycles");
    const kpiAvgDuration = document.getElementById("kpi-avg-duration");
    const kpiExtCount = document.getElementById("kpi-ext-count");
    const kpiCutCount = document.getElementById("kpi-cut-count");
    const kpiCarsTotal = document.getElementById("kpi-cars-total");
    const classCar = document.getElementById("cls-car");
    const classMotorcycle = document.getElementById("cls-motorcycle");
    const classBus = document.getElementById("cls-bus");
    const classTruck = document.getElementById("cls-truck");
    const algoLane1 = document.getElementById("algo-lane-1");
    const algoLane2 = document.getElementById("algo-lane-2");
    const camStatus1 = document.getElementById("cam-status-1");
    const camStatus2 = document.getElementById("cam-status-2");

    // Tabla de Trazabilidad
    const traceTableBody = document.getElementById("trace-table-body");
    const btnDownloadCsv = document.getElementById("btn-download-csv");
    const btnClearHistory = document.getElementById("btn-clear-history");

    // Modal de Ajustes
    const settingsModal = document.getElementById("settings-modal");
    const btnOpenSettings = document.getElementById("btn-open-settings");
    const btnCloseModal = document.getElementById("btn-close-modal");
    const btnCancelSettings = document.getElementById("btn-cancel-settings");
    const btnSaveSettings = document.getElementById("btn-save-settings");
    const inputUrlLane1 = document.getElementById("input-url-lane1");
    const inputUrlLane2 = document.getElementById("input-url-lane2");

    // Perímetro del círculo radial: 2 * PI * 85 ≈ 534.07
    const CIRCLE_CIRCUMFERENCE = 534.07;
    let ws = null;
    let lastCycleCount = -1;
    let cachedHistoryData = [];

    // Reloj digital en vivo
    function updateClock() {
        const now = new Date();
        liveClock.textContent = now.toTimeString().split(" ")[0];
    }
    setInterval(updateClock, 1000);
    updateClock();

    // ==========================================
    // CONEXIÓN WEBSOCKET RESILIENTE
    // ==========================================
    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            wsStatusChip.style.background = "rgba(16, 185, 129, 0.1)";
            wsStatusChip.style.borderColor = "rgba(16, 185, 129, 0.25)";
            wsStatusChip.style.color = "var(--emerald-success)";
            wsStatusText.textContent = "SISTEMA EN LÍNEA";
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                renderTelemetry(data);
            } catch (err) {
                console.error("Error parseando telemetría WS:", err);
            }
        };

        ws.onclose = () => {
            wsStatusChip.style.background = "rgba(239, 68, 68, 0.1)";
            wsStatusChip.style.borderColor = "rgba(239, 68, 68, 0.25)";
            wsStatusChip.style.color = "#f87171";
            wsStatusText.textContent = "DESCONECTADO (REINTENTANDO...)";
            setTimeout(connectWebSocket, 2000);
        };

        ws.onerror = () => {
            ws.close();
        };
    }

    // ==========================================
    // RENDERIZADO DE TELEMETRÍA EN TIEMPO REAL
    // ==========================================
    function renderTelemetry(data) {
        // Actualizar Luces del Semáforo 1
        const l1 = data.lights.lane1;
        updateBulb(l1Red, l1.red);
        updateBulb(l1Yellow, l1.yellow);
        updateBulb(l1Green, l1.green);
        updateStatusLabel(statusL1Label, l1);

        // Actualizar Luces del Semáforo 2
        const l2 = data.lights.lane2;
        updateBulb(l2Red, l2.red);
        updateBulb(l2Yellow, l2.yellow);
        updateBulb(l2Green, l2.green);
        updateStatusLabel(statusL2Label, l2);

        // Actualizar Conteos de Autos
        valCarsLane1.textContent = data.vehicles_lane1;
        valCarsLane2.textContent = data.vehicles_lane2;

        // Actualizar Cronómetro Central
        const rem = data.remaining_seconds;
        const target = data.target_seconds;
        const elapsed = data.elapsed_seconds;

        timerRemaining.innerHTML = `${rem.toFixed(1)}<small>s</small>`;
        timerElapsed.textContent = `Transcurrido: ${elapsed.toFixed(1)}s / ${target.toFixed(1)}s`;

        // Nombre de la Fase y color del círculo radial
        if (data.state === "LANE1_GREEN") {
            timerPhaseName.textContent = "CARRIL 1 • VERDE";
            timerProgressBar.style.stroke = "var(--led-green-on)";
        } else if (data.state === "LANE1_YELLOW") {
            timerPhaseName.textContent = "CARRIL 1 • TRANSICIÓN AMARILLO";
            timerProgressBar.style.stroke = "var(--led-yellow-on)";
        } else if (data.state === "LANE2_GREEN") {
            timerPhaseName.textContent = "CARRIL 2 • VERDE";
            timerProgressBar.style.stroke = "var(--led-green-on)";
        } else if (data.state === "LANE2_YELLOW") {
            timerPhaseName.textContent = "CARRIL 2 • TRANSICIÓN AMARILLO";
            timerProgressBar.style.stroke = "var(--led-yellow-on)";
        }

        // Progreso circular
        const progressFraction = Math.max(0, Math.min(1, (target - rem) / target));
        const offset = CIRCLE_CIRCUMFERENCE * (1 - progressFraction);
        timerProgressBar.style.strokeDashoffset = offset;

        // Badge de Explicación de la Regla en Ejecución
        renderRuleNotice(data);

        // Botón de Pausa
        btnTogglePause.innerHTML = data.paused ? "▶️ Reanudar Temporizador" : "⏸️ Pausar Temporizador";
        btnTogglePause.style.color = data.paused ? "#fbbf24" : "var(--text-secondary)";

        // Toggle de Simulación
        if (toggleSim.checked !== data.use_simulation) {
            toggleSim.checked = data.use_simulation;
        }

        // Modos de Visión
        if (data.detection_mode === "mog2") {
            btnModeMog2.classList.add("active");
            btnModeYolo.classList.remove("active");
        } else {
            btnModeYolo.classList.add("active");
            btnModeMog2.classList.remove("active");
        }
        renderVision(data);
        renderLedLink(data);

        // Actualizar KPIs si cambiaron los ciclos
        if (data.stats) {
            kpiTotalCycles.textContent = data.stats.total_cycles;
            kpiAvgDuration.innerHTML = `${data.stats.avg_duration.toFixed(1)}<small>s</small>`;
            const causes = data.stats.cause_counts || {};
            kpiExtCount.textContent = (causes.extension_15s || 0) + (causes.extension_18s || 0) + (causes.extension_20s || 0);
            kpiCutCount.textContent = (causes.empty_opposite_cut_5s || 0) + (causes.empty_lane_cut_5s || 0);
            kpiCarsTotal.textContent = (data.stats.total_vehicles_lane1 + data.stats.total_vehicles_lane2) || 0;

            if (data.stats.total_cycles !== lastCycleCount) {
                lastCycleCount = data.stats.total_cycles;
                loadHistoryTable();
            }
        }
    }

    function updateBulb(element, state) {
        if (state) {
            element.classList.add("active");
        } else {
            element.classList.remove("active");
        }
    }

    function updateStatusLabel(labelElement, lights) {
        labelElement.className = "light-status-label";
        if (lights.green) {
            labelElement.classList.add("state-green");
            labelElement.textContent = "EN VERDE (PASO)";
        } else if (lights.yellow) {
            labelElement.classList.add("state-yellow");
            labelElement.textContent = "EN AMARILLO (PRECAUCIÓN)";
        } else {
            labelElement.classList.add("state-red");
            labelElement.textContent = "EN ROJO (DETENIDO)";
        }
    }

    function renderRuleNotice(data) {
        const state = data.state;
        const elapsed = data.elapsed_seconds;
        const timing = data.timing || { base_green: 10, extended_green: 15, empty_check: 5, yellow: 2 };
        const activeCars = data.active_vehicles ?? (data.active_lane === 1 ? data.vehicles_lane1 : data.vehicles_lane2);
        const oppositeCars = data.opposite_vehicles ?? (data.active_lane === 1 ? data.vehicles_lane2 : data.vehicles_lane1);

        if (state.includes("YELLOW")) {
            ruleStatusBadge.textContent = `SEGURIDAD: AMARILLO ${timing.yellow}s`;
            ruleStatusBadge.style.color = "var(--led-yellow-on)";
            ruleExplanationText.textContent = "Despejando el cruce antes de dar verde al otro carril.";
            return;
        }

        if (data.is_extended) {
            ruleStatusBadge.textContent = `VERDE AMPLIADO A ${timing.extended_green}s`;
            ruleStatusBadge.style.color = "#fbbf24";
            ruleExplanationText.textContent = `Este carril tiene ${activeCars} vehículos y el contrario ${oppositeCars}. Mayor demanda vehicular: fase ampliada a ${timing.extended_green}s.`;
            return;
        }

        if (elapsed < timing.empty_check) {
            ruleStatusBadge.textContent = `VERDE BASE ${timing.base_green}s · REVISIÓN A LOS ${timing.empty_check}s`;
            ruleStatusBadge.style.color = "var(--primary-cyan)";
            ruleExplanationText.textContent = activeCars === 0
                ? `Carril actual sin vehículos (0). Si continúa vacío a los ${timing.empty_check}s, cerrará anticipadamente para no demorar al otro carril.`
                : `Este carril: ${activeCars}. Contrario: ${oppositeCars}. El verde se ampliará a ${timing.extended_green}s si este carril supera al contrario.`;
            return;
        }

        ruleStatusBadge.textContent = `VERDE BASE ${timing.base_green}s`;
        ruleStatusBadge.style.color = "var(--primary-cyan)";
        ruleExplanationText.textContent = `Este carril (${activeCars}) no supera al contrario (${oppositeCars}). El verde cierra a los ${timing.base_green}s.`;
    }

    function renderLedLink(data) {
        const linked = data.gpio_ready === true;
        ledStatusText.textContent = linked ? "LEDS SIGUIENDO AL PANEL" : "LEDS SIN GPIO";
        ledStatusChip.style.color = linked ? "var(--emerald-success)" : "#f87171";
        ledStatusChip.style.borderColor = linked ? "rgba(16, 185, 129, 0.25)" : "rgba(239, 68, 68, 0.25)";
    }

    function renderVision(data) {
        const vision = data.vision || {};
        const modelReady = vision.model_loaded !== false && data.detection_mode !== "mog2";
        const usingYolo = data.detection_mode === "yolo" && vision.model_loaded !== false;
        visionStatusText.textContent = usingYolo ? "YOLOv8n ACTIVO" : "MOG2 (RESPALDO)";
        visionStatusChip.style.color = usingYolo ? "var(--emerald-success)" : "#fbbf24";
        visionStatusChip.style.borderColor = usingYolo ? "rgba(16, 185, 129, 0.25)" : "rgba(251, 191, 36, 0.35)";

        const counts = vision.counts || {};
        const lane1 = counts.lane1 || {};
        const lane2 = counts.lane2 || {};
        const sumClass = (name) => (lane1[name] || 0) + (lane2[name] || 0);
        classCar.textContent = sumClass("car");
        classMotorcycle.textContent = sumClass("motorcycle");
        classBus.textContent = sumClass("bus");
        classTruck.textContent = sumClass("truck");

        const algo = usingYolo
            ? "YOLOv8n · auto, moto, bus y camión en la ROI"
            : "MOG2 · respaldo por movimiento en la zona de espera";
        algoLane1.textContent = algo;
        algoLane2.textContent = algo;

        const cameras = vision.cameras || {};
        updateCameraStatus(camStatus1, cameras.lane1, data.use_simulation);
        updateCameraStatus(camStatus2, cameras.lane2, data.use_simulation);

        if (!modelReady && data.detection_mode === "yolo") {
            visionStatusText.textContent = "YOLOv8n NO CARGÓ";
            visionStatusChip.style.color = "#f87171";
        }
    }

    function updateCameraStatus(element, isOnline, isSimulation) {
        const dot = element.querySelector(".blink-dot");
        if (isSimulation) {
            element.lastChild.textContent = " SIMULACIÓN";
            if (dot) dot.style.background = "#38bdf8";
            return;
        }
        if (isOnline) {
            element.lastChild.textContent = " WEBCAM";
            if (dot) dot.style.background = "#10b981";
            return;
        }
        element.lastChild.textContent = " SIN WEBCAM";
        if (dot) dot.style.background = "#ef4444";
    }

    // ==========================================
    // CARGA DE TABLA DE TRAZABILIDAD
    // ==========================================
    async function loadHistoryTable() {
        try {
            const res = await fetch("/api/history?limit=40");
            const data = await res.json();
            cachedHistoryData = data;

            if (!data || data.length === 0) {
                traceTableBody.innerHTML = `<tr><td colspan="8" class="text-center empty-state">No hay registros de trazabilidad aún. El semáforo registrará cada cambio automáticamente.</td></tr>`;
                return;
            }

            let html = "";
            const causeLabels = {
                base_10s: ["badge-base", "Verde base 10s"],
                extension_15s: ["badge-extended", "Extensión a 15s"],
                extension_18s: ["badge-extended", "Extensión a 18s"],
                extension_20s: ["badge-extended", "Extensión a 20s"],
                empty_lane_cut_5s: ["badge-cut", "Corte a 5s (carril vacío)"],
                empty_opposite_cut_5s: ["badge-cut", "Corte a 5s (contrario vacío)"],
                base_15s: ["badge-base", "Verde base 15s (histórico)"],
                manual_override: ["badge-manual", "Cambio manual"],
            };

            data.forEach((row) => {
                const [badgeClass, causeLabel] = causeLabels[row.cause] || ["badge-base", row.cause];

                html += `
                    <tr>
                        <td><strong>#${row.id}</strong></td>
                        <td>${row.timestamp}</td>
                        <td><span class="lane-num">C${row.lane}</span> Carril ${row.lane}</td>
                        <td><strong>${row.duration_seconds.toFixed(1)}s</strong></td>
                        <td><span class="badge-cause ${badgeClass}">${causeLabel}</span></td>
                        <td>${row.vehicles_lane1}</td>
                        <td>${row.vehicles_lane2}</td>
                        <td><span style="color: var(--emerald-success)">COMPLETADO</span></td>
                    </tr>
                `;
            });
            traceTableBody.innerHTML = html;
        } catch (err) {
            console.error("Error cargando historial de trazabilidad:", err);
        }
    }

    // ==========================================
    // ACCIONES Y CONTROLES INTERACTIVOS
    // ==========================================
    btnForceChange.addEventListener("click", () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send("force_change");
        } else {
            fetch("/api/control/force_change", { method: "POST" });
        }
    });

    btnTogglePause.addEventListener("click", () => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send("toggle_pause");
        }
    });

    toggleSim.addEventListener("change", async () => {
        await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ use_simulation: toggleSim.checked })
        });
    });

    btnModeMog2.addEventListener("click", async () => {
        await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ detection_mode: "mog2" })
        });
    });

    btnModeYolo.addEventListener("click", async () => {
        await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ detection_mode: "yolo" })
        });
    });

    btnClearHistory.addEventListener("click", async () => {
        if (confirm("¿Estás seguro de que deseas vaciar el historial de trazabilidad?")) {
            await fetch("/api/control/clear_history", { method: "POST" });
            loadHistoryTable();
        }
    });

    // Exportar CSV
    btnDownloadCsv.addEventListener("click", () => {
        if (!cachedHistoryData || cachedHistoryData.length === 0) {
            alert("No hay datos para exportar.");
            return;
        }

        let csv = "ID,Timestamp,Carril,Duracion_Segundos,Causa,Vehiculos_Carril_1,Vehiculos_Carril_2\n";
        cachedHistoryData.forEach((r) => {
            csv += `${r.id},"${r.timestamp}",${r.lane},${r.duration_seconds},"${r.cause}",${r.vehicles_lane1},${r.vehicles_lane2}\n`;
        });

        const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `trazabilidad_semaforos_${new Date().toISOString().slice(0, 10)}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    });

    // Modal de Configuración
    btnOpenSettings.addEventListener("click", () => {
        settingsModal.classList.add("open");
    });

    btnCloseModal.addEventListener("click", () => {
        settingsModal.classList.remove("open");
    });

    btnCancelSettings.addEventListener("click", () => {
        settingsModal.classList.remove("open");
    });

    btnSaveSettings.addEventListener("click", async () => {
        const urlL1 = inputUrlLane1.value.trim() || null;
        const urlL2 = inputUrlLane2.value.trim() || null;

        await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                lane1_url: urlL1,
                lane2_url: urlL2
            })
        });

        settingsModal.classList.remove("open");
        alert("Cámaras aplicadas. YOLOv8 vuelve a leer los dos carriles.");
    });

    // Iniciar conexión y carga inicial
    connectWebSocket();
    loadHistoryTable();
});

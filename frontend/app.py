import asyncio
import base64
import io
import json
import os
import queue
import threading
from datetime import datetime, timezone

import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import websockets
from dash import Input, Output, State, callback, ctx, dcc, html, no_update

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
WS_URL = BACKEND.replace("http://", "ws://").replace("https://", "wss://")

PRESSURE_SAFE = 1.15
PRESSURE_WARN = 1.30
PRESSURE_CRIT = 1.50
COLOR_SAFE = "#22c55e"
COLOR_WARN = "#f59e0b"
COLOR_CRIT = "#ef4444"
COLOR_BG = "#0b1120"
COLOR_SURFACE = "#1a2332"
COLOR_BORDER = "#334155"
COLOR_TEXT = "#f1f5f9"
COLOR_MUTED = "#94a3b8"
COLOR_ACCENT = "#38bdf8"
COLOR_WHITE = "#ffffff"
COLOR_HELP = "#64748b"


class SimulationStream:
    """Background-thread WebSocket client. Thread-safe message queue."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, case_id: str, config: dict):
        self._q = queue.Queue()
        self._active = True
        self._thread = threading.Thread(target=self._run, args=(case_id, config), daemon=True)
        self._thread.start()

    def _run(self, case_id: str, config: dict):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._connect(case_id, config))

    async def _connect(self, case_id: str, config: dict):
        try:
            async with websockets.connect(f"{WS_URL}/ws/simulate/{case_id}") as ws:
                await ws.send(json.dumps(config))
                async for raw in ws:
                    self._q.put(json.loads(raw))
        except Exception as exc:
            self._q.put({"type": "error", "detail": str(exc)})
        finally:
            self._active = False

    def drain(self) -> list[dict]:
        msgs = []
        while not self._q.empty():
            try:
                msgs.append(self._q.get_nowait())
            except queue.Empty:
                break
        return msgs


stream = SimulationStream()
_prev_trace_cache: list[dict] | None = None


def fetch_json(path: str) -> dict | list:
    resp = requests.get(f"{BACKEND}{path}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _empty_figure(msg: str = "") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor=COLOR_SURFACE,
        plot_bgcolor=COLOR_SURFACE,
        font_color=COLOR_TEXT,
        margin=dict(l=8, r=8, t=8, b=8),
        xaxis=dict(showgrid=False, zeroline=False, visible=False),
        yaxis=dict(showgrid=False, zeroline=False, visible=False),
    )
    if msg:
        fig.add_annotation(
            text=msg,
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(color=COLOR_MUTED, size=14),
        )
    return fig


def _axis_style(fig: go.Figure, x_title="", y_title=""):
    fig.update_xaxes(
        gridcolor=COLOR_BORDER, zerolinecolor=COLOR_BORDER,
        title=x_title, title_font_color=COLOR_MUTED,
    )
    fig.update_yaxes(
        gridcolor=COLOR_BORDER, zerolinecolor=COLOR_BORDER,
        title=y_title, title_font_color=COLOR_MUTED,
    )


def _pressure_zone(pressure_mpa: float, p_init: float) -> tuple[str, str]:
    ratio = pressure_mpa / max(p_init, 0.1)
    if ratio > PRESSURE_CRIT:
        return "CRITICAL", COLOR_CRIT
    if ratio > PRESSURE_WARN:
        return "WARNING", COLOR_WARN
    if ratio > PRESSURE_SAFE:
        return "ELEVATED", COLOR_WARN
    return "SAFE", COLOR_SAFE


def build_heatmap_grid(pressure_mpa: float, plume_radius_m: float, rng: np.random.Generator) -> np.ndarray:
    size = 10
    grid = np.zeros((size, size), dtype=float)
    cx, cy = 4.5, 4.5
    sigma = max(plume_radius_m / 80.0, 0.5)
    for i in range(size):
        for j in range(size):
            dist = np.sqrt((i - cx) ** 2 + (j - cy) ** 2)
            grid[i, j] = pressure_mpa * np.exp(-(dist**2) / (2 * sigma**2))
    grid += rng.normal(0, pressure_mpa * 0.03, grid.shape)
    return np.clip(grid, 0, None)


# ---------------------------------------------------------------
# layout helpers
# ---------------------------------------------------------------

def _card(title: str, children, style_override: dict | None = None) -> html.Div:
    base = dict(
        background=COLOR_SURFACE,
        border=f"1px solid {COLOR_BORDER}",
        borderRadius="8px",
        padding="16px",
        marginBottom="12px",
    )
    if style_override:
        base.update(style_override)
    return html.Div(
        [
            html.Div(title, style=dict(fontSize="13px", fontWeight="600", color=COLOR_MUTED, marginBottom="10px", textTransform="uppercase", letterSpacing="0.5px")),
            *([children] if not isinstance(children, list) else children),
        ],
        style=base,
    )


def _stat(label: str, value: str, unit: str = "", help_text: str = "") -> html.Div:
    children = [
        html.Div(label, style=dict(fontSize="11px", color=COLOR_MUTED)),
        html.Div(
            [html.Span(value, style=dict(fontSize="20px", fontWeight="700", color=COLOR_TEXT, fontVariantNumeric="tabular-nums")),
             html.Span(f" {unit}", style=dict(fontSize="12px", color=COLOR_MUTED))] if unit
            else html.Span(value, style=dict(fontSize="20px", fontWeight="700", color=COLOR_TEXT, fontVariantNumeric="tabular-nums")),
        ),
    ]
    if help_text:
        children.append(html.Div(help_text, style=dict(fontSize="10px", color=COLOR_HELP, marginTop="2px", lineHeight="1.3")))
    return html.Div(children, style=dict(flex="1", minWidth="120px"))


def _help(text: str) -> html.Div:
    return html.Div(text, style=dict(fontSize="10px", color=COLOR_HELP, lineHeight="1.4", marginTop="3px"))


# ---------------------------------------------------------------
# app
# ---------------------------------------------------------------

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "CCS Realtime Injection Simulator"


@app.server.route("/health")
def frontend_health():
    return {"status": "ok"}

app.layout = html.Div(
    style=dict(
        display="flex", height="100vh", background=COLOR_BG,
        fontFamily="'Inter', -apple-system, BlinkMacSystemFont, sans-serif",
        color=COLOR_TEXT, overflow="hidden",
    ),
    children=[
        # ==================== SIDEBAR ====================
        html.Div(
            id="sidebar",
            style=dict(
                width="340px", minWidth="340px",
                background=COLOR_SURFACE,
                borderRight=f"1px solid {COLOR_BORDER}",
                padding="20px",
                overflowY="auto",
                display="flex", flexDirection="column", gap="12px",
            ),
            children=[
                html.Div(
                    [
                        html.H1("CCS Injection Simulator", style=dict(fontSize="20px", fontWeight="700", color=COLOR_WHITE, margin="0")),
                        html.Div("Real-Time Reservoir Monitor", style=dict(fontSize="13px", color=COLOR_ACCENT, fontWeight="500")),
                        _help("A tool that simulates CO₂ injection into underground rock formations to predict pressure changes, plume spread, and leakage risk over time."),
                    ],
                    style=dict(marginBottom="8px"),
                ),

                html.Div(
                    [
                        html.Div("HOW TO USE", style=dict(fontSize="13px", fontWeight="600", color=COLOR_ACCENT, textTransform="uppercase", letterSpacing="0.5px", marginBottom="10px")),
                        html.Div([
                            html.Div([
                                html.Span("1", style=dict(display="inline-block", width="20px", height="20px", borderRadius="10px", background=COLOR_ACCENT, color=COLOR_BG, fontSize="11px", fontWeight="700", textAlign="center", lineHeight="20px", marginRight="8px")),
                                html.Span("Pick a facility", style=dict(fontWeight="600", fontSize="12px", color=COLOR_WHITE)),
                                _help("Choose an injection site from the dropdown below. Each site has different underground rock properties."),
                            ], style=dict(marginBottom="10px")),
                            html.Div([
                                html.Span("2", style=dict(display="inline-block", width="20px", height="20px", borderRadius="10px", background=COLOR_ACCENT, color=COLOR_BG, fontSize="11px", fontWeight="700", textAlign="center", lineHeight="20px", marginRight="8px")),
                                html.Span("Adjust settings", style=dict(fontWeight="600", fontSize="12px", color=COLOR_WHITE)),
                                _help("Set how fast CO₂ is pumped in, how many days to simulate, and the daily chance of a leak."),
                            ], style=dict(marginBottom="10px")),
                            html.Div([
                                html.Span("3", style=dict(display="inline-block", width="20px", height="20px", borderRadius="10px", background=COLOR_ACCENT, color=COLOR_BG, fontSize="11px", fontWeight="700", textAlign="center", lineHeight="20px", marginRight="8px")),
                                html.Span("Start & watch", style=dict(fontWeight="600", fontSize="12px", color=COLOR_WHITE)),
                                _help("Click Start Simulation. Charts will animate in real time showing pressure, injection rate, plume spread, and any leaks."),
                            ], style=dict(marginBottom="10px")),
                            html.Div([
                                html.Span("4", style=dict(display="inline-block", width="20px", height="20px", borderRadius="10px", background=COLOR_ACCENT, color=COLOR_BG, fontSize="11px", fontWeight="700", textAlign="center", lineHeight="20px", marginRight="8px")),
                                html.Span("Review results", style=dict(fontWeight="600", fontSize="12px", color=COLOR_WHITE)),
                                _help("Compare with a previous run, export data as CSV, or reset to try different settings."),
                            ]),
                        ]),
                    ],
                    style=dict(
                        background=COLOR_SURFACE, border=f"1px solid {COLOR_BORDER}",
                        borderRadius="8px", padding="16px",
                    ),
                ),

                _card("Facility", [
                    dcc.Dropdown(
                        id="case-selector",
                        options=[],  # populated on load
                        value=None,
                        placeholder="Select a facility...",
                        clearable=False,
                        style=dict(background=COLOR_BG, color=COLOR_TEXT),
                    ),
                ]),

                html.Div(id="preset-info"),

                _card("Injection Rate", [
                    _help("How fast CO₂ is pumped underground. 1.0× = real-world rate from the dataset. 2.0× = double speed."),
                    dcc.Slider(
                        id="rate-slider",
                        min=0.5, max=2.0, step=0.1, value=1.0,
                        marks={0.5: "Half", 1.0: "Normal", 1.5: "1.5x", 2.0: "Double"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                    html.Div("Simulation Duration", style=dict(fontSize="12px", color=COLOR_MUTED, marginTop="16px", marginBottom="4px")),
                    _help("Number of days to simulate. Longer = more data. Maximum 366 days (1 year of data)."),
                    dcc.Slider(
                        id="duration-slider",
                        min=30, max=366, step=1, value=90,
                        marks={30: "30d", 90: "90d", 180: "180d", 270: "270d", 366: "366d"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),

                _card("Leak Probability", [
                    _help("The chance of a leak happening on any given day. 0% = no leaks. 5% = leaks happen often. Real CCS sites target near-zero leakage."),
                    dcc.Slider(
                        id="leak-slider",
                        min=0.0, max=0.05, step=0.001, value=0.003,
                        marks={0: "None", 0.01: "Low", 0.025: "Med", 0.05: "High"},
                        tooltip={"placement": "bottom", "always_visible": False},
                    ),
                ]),

                html.Div(
                    [
                        html.Div("PRESSURE SAFETY SCALE", style=dict(fontSize="13px", fontWeight="600", color=COLOR_MUTED, textTransform="uppercase", letterSpacing="0.5px", marginBottom="10px")),
                        _help("Pressure is compared to the initial (natural) reservoir pressure. Higher multipliers mean more risk of fracturing the rock."),
                        html.Div(style=dict(height="8px")),
                        html.Div([
                            html.Div([
                                html.Div([
                                    html.Div(style=dict(width="12px", height="12px", borderRadius="3px", background=COLOR_SAFE, display="inline-block", marginRight="6px")),
                                    html.Span("Normal", style=dict(fontSize="12px", fontWeight="600", color=COLOR_WHITE)),
                                ], style=dict(marginBottom="2px")),
                                _help("Pressure is safe. Rock stays sealed."),
                            ], style=dict(marginBottom="8px")),
                            html.Div([
                                html.Div([
                                    html.Div(style=dict(width="12px", height="12px", borderRadius="3px", background=COLOR_WARN, display="inline-block", marginRight="6px")),
                                    html.Span("Elevated", style=dict(fontSize="12px", fontWeight="600", color=COLOR_WHITE)),
                                ], style=dict(marginBottom="2px")),
                                _help("Pressure is rising. Monitor closely."),
                            ], style=dict(marginBottom="8px")),
                            html.Div([
                                html.Div([
                                    html.Div(style=dict(width="12px", height="12px", borderRadius="3px", background=COLOR_WARN, display="inline-block", marginRight="6px")),
                                    html.Span("Warning", style=dict(fontSize="12px", fontWeight="600", color=COLOR_WHITE)),
                                ], style=dict(marginBottom="2px")),
                                _help("Pressure entering risk zone. May need to slow injection."),
                            ], style=dict(marginBottom="8px")),
                            html.Div([
                                html.Div([
                                    html.Div(style=dict(width="12px", height="12px", borderRadius="3px", background=COLOR_CRIT, display="inline-block", marginRight="6px")),
                                    html.Span("Critical — DANGER", style=dict(fontSize="12px", fontWeight="600", color=COLOR_CRIT)),
                                ], style=dict(marginBottom="2px")),
                                _help("Rock could fracture. CO₂ could escape. Shut down immediately."),
                            ]),
                        ]),
                        html.Hr(style=dict(borderColor=COLOR_BORDER, margin="10px 0")),
                        html.Div([
                            html.Div(style=dict(width="12px", height="12px", borderRadius="3px", background=COLOR_CRIT, display="inline-block", marginRight="6px")),
                            html.Span("✕ Red X on chart = CO₂ leak detected", style=dict(fontSize="11px", color=COLOR_MUTED)),
                        ]),
                    ],
                    style=dict(
                        background=COLOR_SURFACE, border=f"1px solid {COLOR_BORDER}",
                        borderRadius="8px", padding="16px",
                    ),
                ),
                html.Div(
                    [
                        html.Button(
                            "Start Simulation",
                            id="start-btn",
                            n_clicks=0,
                            style=dict(
                                width="100%", padding="12px", fontSize="15px", fontWeight="600",
                                border="none", borderRadius="8px", cursor="pointer",
                                background=COLOR_ACCENT, color=COLOR_BG,
                                transition="all 0.15s",
                            ),
                        ),
                        html.Div(id="status-indicator", style=dict(marginTop="8px", textAlign="center", fontSize="12px")),
                    ]
                ),

                html.Div(id="post-sim-controls"),

                html.Div(id="summary-panel"),
            ],
        ),
        # ==================== MAIN ====================
        html.Div(
            style=dict(
                flex="1", padding="20px", overflowY="auto",
                display="flex", flexDirection="column", gap="12px",
            ),
            children=[
                html.Div(
                    style=dict(
                        display="flex", gap="12px", alignItems="center", flexWrap="wrap",
                        background=COLOR_SURFACE, border=f"1px solid {COLOR_BORDER}",
                        borderRadius="8px", padding="10px 16px", marginBottom="4px",
                    ),
                    children=[
                        html.Span("📊", style=dict(fontSize="20px")),
                        html.Span("Live Monitor", style=dict(fontSize="17px", fontWeight="700", color=COLOR_WHITE)),
                        html.Span("—", style=dict(color=COLOR_BORDER)),
                        html.Span("Each step = 1 day of injection. Pressure builds as more CO₂ goes in, then slowly bleeds away. Random leaks can occur.", style=dict(fontSize="11px", color=COLOR_HELP, lineHeight="1.4")),
                    ],
                ),
                html.Div(
                    style=dict(display="flex", gap="12px"),
                    children=[
                        html.Div(
                            dcc.Graph(id="chart-pressure", figure=_empty_figure("Pick a facility on the left and click Start to see pressure over time"), config={"displayModeBar": False}),
                            style=dict(flex="1", minHeight="340px"),
                        ),
                        html.Div(
                            dcc.Graph(id="chart-injection", figure=_empty_figure("Daily injection rate will appear here as a bar chart"), config={"displayModeBar": False}),
                            style=dict(flex="1", minHeight="340px"),
                        ),
                    ],
                ),
                html.Div(
                    style=dict(display="flex", gap="12px"),
                    children=[
                        html.Div(
                            dcc.Graph(id="chart-gauge", figure=_empty_figure("Pressure safety gauge will show here"), config={"displayModeBar": False}),
                            style=dict(flex="0 0 240px", minHeight="340px"),
                        ),
                        html.Div(
                            dcc.Graph(id="chart-cumulative", figure=_empty_figure("Total tonnes of CO₂ stored will build up here"), config={"displayModeBar": False}),
                            style=dict(flex="1", minHeight="340px"),
                        ),
                        html.Div(
                            id="alert-log",
                            style=dict(
                                flex="0 0 220px", minHeight="340px",
                                background=COLOR_SURFACE, border=f"1px solid {COLOR_BORDER}",
                                borderRadius="8px", padding="12px", overflowY="auto",
                            ),
                            children=[
                                html.Div("SAFETY ALERTS", style=dict(fontSize="13px", fontWeight="600", color=COLOR_WARN, textTransform="uppercase", letterSpacing="0.5px", marginBottom="8px")),
                                html.Div("Start a simulation to monitor for leaks and pressure warnings.", id="alert-list", style=dict(fontSize="12px", color=COLOR_HELP, lineHeight="1.5")),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    dcc.Graph(id="chart-heatmap", figure=_empty_figure("Underground pressure spread map will appear here"), config={"displayModeBar": False}),
                    style=dict(minHeight="380px"),
                ),
            ],
        ),
        # ==================== STORES ====================
        dcc.Store(id="store-trace", data=[]),
        dcc.Store(id="store-running", data=False),
        dcc.Store(id="store-prev-trace", data=[]),
        dcc.Store(id="store-preset", data={}),
        dcc.Store(id="store-alerts", data=[]),
        dcc.Store(id="store-completed", data=False),
        dcc.Interval(id="tick", interval=150, disabled=True),
    ],
)

# ---------------------------------------------------------------
# callbacks — load presets on page load
# ---------------------------------------------------------------


@app.callback(
    Output("case-selector", "options"),
    Output("case-selector", "value"),
    Input("case-selector", "id"),
)
def load_case_options(_):
    try:
        presets = fetch_json("/presets")
        options = [
            {
                "label": f"{p['case_id']} — {p['reservoir_type']} ({p['p_init_MPa']:.1f} MPa)",
                "value": p["case_id"],
            }
            for p in presets
        ]
        return options, options[0]["value"] if options else None
    except Exception:
        return [], None


# ---------------------------------------------------------------
# callbacks — preset info
# ---------------------------------------------------------------


@app.callback(
    Output("preset-info", "children"),
    Output("store-preset", "data"),
    Input("case-selector", "value"),
)
def show_preset(case_id: str):
    if not case_id:
        return html.Div("No facility selected", style=dict(color=COLOR_MUTED)), {}
    try:
        p = fetch_json("/presets")
        match = next((x for x in p if x["case_id"] == case_id), None)
        if not match:
            return html.Div("Facility not found", style=dict(color=COLOR_CRIT)), {}
    except Exception:
        return html.Div("Backend unreachable", style=dict(color=COLOR_CRIT)), {}

    return _card("Reservoir Properties", [
        _stat("Rock Type", match["reservoir_type"], "", "Basalt = dense volcanic rock. Saline aquifer = porous sandstone with saltwater. Depleted gas field = rock that already held natural gas."),
        _stat("Starting Pressure", f"{match['p_init_MPa']:.1f}", "MPa", "Natural pressure before any CO₂ is injected. As you pump CO₂ in, this rises."),
        _stat("Temperature", f"{match['temp_C']:.0f}", "°C", "Underground temperature. Affects CO₂ density and flow behavior."),
        _stat("CO₂ Capture", match["capture_tech"], "", "Technology used to separate CO₂ from power plant emissions."),
        html.Div(
            f"Planned annual injection: {match['total_injected_tonnes']:,.0f} tonnes of CO₂",
            style=dict(fontSize="11px", color=COLOR_HELP, marginTop="8px"),
        ),
    ]), match


# ---------------------------------------------------------------
# callbacks — start / stop simulation
# ---------------------------------------------------------------


@app.callback(
    Output("store-trace", "data"),
    Output("store-running", "data"),
    Output("store-alerts", "data", allow_duplicate=True),
    Output("store-completed", "data"),
    Output("store-prev-trace", "data", allow_duplicate=True),
    Output("tick", "disabled", allow_duplicate=True),
    Output("start-btn", "disabled", allow_duplicate=True),
    Output("status-indicator", "children", allow_duplicate=True),
    Output("status-indicator", "style", allow_duplicate=True),
    Input("start-btn", "n_clicks"),
    State("case-selector", "value"),
    State("rate-slider", "value"),
    State("duration-slider", "value"),
    State("leak-slider", "value"),
    State("store-running", "data"),
    prevent_initial_call=True,
)
def start_simulation(n_clicks, case_id, rate, days, leak_pct, running):
    if running:
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update

    config = {
        "days": days,
        "injection_rate_multiplier": rate,
        "leak_probability": leak_pct,
        "speed_multiplier": 8.0,
    }

    stream.start(case_id, config)

    status_style = dict(textAlign="center", fontSize="12px", fontWeight="600", marginTop="8px")

    return (
        [],
        True,
        [],
        False,
        no_update,
        False,
        True,
        html.Span("RUNNING — streaming live data...", style={**status_style, "color": COLOR_ACCENT}),
        status_style,
    )


# ---------------------------------------------------------------
# callbacks — tick (main update loop)
# ---------------------------------------------------------------


@app.callback(
    Output("store-trace", "data", allow_duplicate=True),
    Output("store-running", "data", allow_duplicate=True),
    Output("store-alerts", "data", allow_duplicate=True),
    Output("store-completed", "data", allow_duplicate=True),
    Output("store-prev-trace", "data", allow_duplicate=True),
    Output("tick", "disabled", allow_duplicate=True),
    Output("start-btn", "disabled", allow_duplicate=True),
    Output("status-indicator", "children", allow_duplicate=True),
    Output("status-indicator", "style", allow_duplicate=True),
    Output("chart-pressure", "figure", allow_duplicate=True),
    Output("chart-injection", "figure", allow_duplicate=True),
    Output("chart-gauge", "figure", allow_duplicate=True),
    Output("chart-cumulative", "figure", allow_duplicate=True),
    Output("chart-heatmap", "figure", allow_duplicate=True),
    Output("alert-list", "children", allow_duplicate=True),
    Output("summary-panel", "children", allow_duplicate=True),
    Input("tick", "n_intervals"),
    State("store-trace", "data"),
    State("store-running", "data"),
    State("store-alerts", "data"),
    State("store-completed", "data"),
    State("store-prev-trace", "data"),
    State("store-preset", "data"),
    prevent_initial_call=True,
)
def tick_update(_n, trace_data, running, alerts, completed, prev_trace, preset):
    if not running:
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update

    msgs = stream.drain()
    if not msgs:
        return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update

    p_init = preset.get("p_init_MPa", 20.0)
    rng = np.random.default_rng()
    new_alerts = list(alerts)

    for msg in msgs:
        if msg["type"] == "tick":
            trace_data.append(msg["state"])
            s = msg["state"]
            if s.get("leak_kg", 0) > 0:
                new_alerts.append(dict(
                    day=s["day"], text=f"LEAK: {s['leak_kg']:,.0f} kg detected at day {s['day']}", level="leak",
                ))
            zone, _ = _pressure_zone(s["pressure_MPa"], p_init)
            if zone in ("CRITICAL", "WARNING"):
                key = f"pressure-day-{s['day']}"
                if not any(a.get("key") == key for a in new_alerts):
                    new_alerts.append(dict(
                        day=s["day"], text=f"{zone}: {s['pressure_MPa']:.1f} MPa at day {s['day']}", level=zone.lower(), key=key,
                    ))
        elif msg["type"] == "done":
            summary = msg.get("summary", {})
            completed = True
            running = False
            prev_trace = list(trace_data)
        elif msg["type"] == "error":
            running = False
            return (
                trace_data, False, alerts, False, prev_trace, True, False,
                html.Span(f"ERROR: {msg['detail']}", style=dict(color=COLOR_CRIT, fontSize="12px", textAlign="center")),
                dict(textAlign="center", fontSize="12px", fontWeight="600", marginTop="8px"),
                no_update, no_update, no_update, no_update, no_update, no_update, no_update,
            )

    alerts = new_alerts
    summary = msg.get("summary", {}) if msgs and msgs[-1].get("type") == "done" else None

    # --- build figures ---
    df = pd.DataFrame(trace_data)
    days = df["day"].values
    pressure = df["pressure_MPa"].values
    injected = df["co2_injected"].values
    cumulative = df["co2_cumulative"].values
    plume = df["plume_radius_m"].values
    leak_flags = df["leak_kg"].values > 0

    # Pressure chart
    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(
        x=days, y=pressure, mode="lines+markers", line=dict(color=COLOR_ACCENT, width=2),
        marker=dict(size=2), name="Pressure",
    ))
    if any(leak_flags):
        fig_p.add_trace(go.Scatter(
            x=days[leak_flags], y=pressure[leak_flags],
            mode="markers", marker=dict(color=COLOR_CRIT, size=8, symbol="x"), name="Leak",
        ))
    for label, y_val, color in [
        ("P_init", p_init, COLOR_MUTED),
        ("Safe", p_init * PRESSURE_SAFE, COLOR_SAFE),
        ("Warn", p_init * PRESSURE_WARN, COLOR_WARN),
        ("Crit", p_init * PRESSURE_CRIT, COLOR_CRIT),
    ]:
        fig_p.add_hline(y=y_val, line_dash="dot", line_color=color, opacity=0.4,
                        annotation_text=label, annotation_position="top right",
                        annotation_font=dict(size=9, color=color))
    fig_p.update_layout(
        title=dict(text="<b>Reservoir Pressure</b> — As more CO₂ goes in, pressure goes up. Dotted lines show safety limits.", font=dict(size=12)),
        paper_bgcolor=COLOR_SURFACE, plot_bgcolor=COLOR_SURFACE,
        font_color=COLOR_TEXT, margin=dict(l=40, r=10, t=40, b=20),
        legend=dict(orientation="h", y=1.12, font=dict(size=11)),
    )
    _axis_style(fig_p, "Day", "Pressure (MPa)")

    # Injection chart
    fig_i = go.Figure()
    fig_i.add_trace(go.Bar(
        x=days, y=injected, marker_color=COLOR_ACCENT, marker_line_width=0, name="Daily Injection",
    ))
    fig_i.update_layout(
        title=dict(text="<b>CO₂ Injected Per Day</b> — How many tonnes are pumped underground each day.", font=dict(size=12)),
        paper_bgcolor=COLOR_SURFACE, plot_bgcolor=COLOR_SURFACE,
        font_color=COLOR_TEXT, margin=dict(l=40, r=10, t=40, b=20),
    )
    _axis_style(fig_i, "Day", "Tonnes / Day")

    # Gauge
    latest_p = pressure[-1] if len(pressure) > 0 else 0
    zone, zone_color = _pressure_zone(latest_p, p_init)
    gauge_max = max(p_init * PRESSURE_CRIT * 1.2, latest_p * 1.3, 10)
    fig_g = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=latest_p,
        number=dict(suffix=" MPa", font=dict(size=28, color=COLOR_TEXT)),
        delta=dict(reference=p_init, increasing=dict(color=COLOR_WARN), decreasing=dict(color=COLOR_SAFE)),
        title=dict(text=f"<b>Current Pressure</b><br>{zone}", font=dict(size=12, color=zone_color)),
        gauge=dict(
            axis=dict(range=[0, gauge_max], tickcolor=COLOR_TEXT),
            bar=dict(color=zone_color, thickness=0.15),
            bgcolor=COLOR_BG,
            borderwidth=0,
            steps=[
                dict(range=[0, p_init * PRESSURE_SAFE], color="rgba(34,197,94,0.15)"),
                dict(range=[p_init * PRESSURE_SAFE, p_init * PRESSURE_WARN], color="rgba(234,179,8,0.15)"),
                dict(range=[p_init * PRESSURE_WARN, gauge_max], color="rgba(239,68,68,0.15)"),
            ],
        ),
    ))
    fig_g.update_layout(
        paper_bgcolor=COLOR_SURFACE, font_color=COLOR_TEXT,
        margin=dict(l=20, r=20, t=50, b=10), height=340,
    )

    # Cumulative scatter
    fig_c = go.Figure()
    fig_c.add_trace(go.Scatter(
        x=days, y=cumulative, mode="lines",
        fill="tozeroy", fillcolor="rgba(56,189,248,0.1)",
        line=dict(color=COLOR_ACCENT, width=2), name="Cumulative",
    ))
    fig_c.update_layout(
        title=dict(text="<b>Cumulative CO₂ Stored</b> — Running total of all CO₂ pumped in so far.", font=dict(size=12)),
        paper_bgcolor=COLOR_SURFACE, plot_bgcolor=COLOR_SURFACE,
        font_color=COLOR_TEXT, margin=dict(l=40, r=10, t=40, b=20),
    )
    _axis_style(fig_c, "Day", "Tonnes")

    # Heatmap
    latest_plume = plume[-1] if len(plume) > 0 else 50
    grid = build_heatmap_grid(latest_p, latest_plume, rng)
    fig_h = go.Figure(go.Heatmap(
        z=grid, colorscale="Viridis", showscale=True,
        colorbar=dict(title="MPa", title_font_color=COLOR_TEXT, tickcolor=COLOR_TEXT),
    ))
    fig_h.update_layout(
        title=dict(text=f"<b>Underground Pressure Map</b> — How pressure spreads from the injection well outward. Plume radius = {latest_plume:.0f} meters", font=dict(size=12)),
        paper_bgcolor=COLOR_SURFACE, plot_bgcolor=COLOR_SURFACE,
        font_color=COLOR_TEXT, margin=dict(l=10, r=50, t=40, b=10),
        xaxis=dict(showticklabels=False, showgrid=False, title="Grid X", title_font_color=COLOR_MUTED),
        yaxis=dict(showticklabels=False, showgrid=False, title="Grid Y", title_font_color=COLOR_MUTED),
    )

    # Alert list
    alert_children = []
    for a in reversed(alerts[-20:]):
        is_leak = a["level"] in ("leak", "critical")
        color = COLOR_CRIT if is_leak else COLOR_WARN
        icon = "🔴" if is_leak else "🟡"
        alert_children.append(html.Div(
            [
                html.Span(f"{icon} ", style=dict(fontSize="11px")),
                html.Span(a["text"], style=dict(fontWeight="600" if is_leak else "400")),
            ],
            style=dict(padding="6px 0", borderBottom=f"1px solid {COLOR_BORDER}", fontSize="11px", lineHeight="1.5", color=COLOR_WHITE if is_leak else COLOR_TEXT),
        ))
    if not alert_children:
        alert_children = [html.Div("No issues detected yet. Start a simulation to see alerts appear here if pressure gets too high or a leak occurs.", style=dict(fontSize="12px", color=COLOR_HELP, lineHeight="1.5"))]

    # Summary panel
    summary_panel = no_update
    if completed and summary:
        summary_panel = _card("Simulation Results", [
            _stat("Days Simulated", str(summary["total_days"]), "days", "Total timesteps completed."),
            _stat("Highest Pressure", f"{summary['max_pressure_MPa']:.2f}", "MPa", "Peak pressure reached during the run."),
            _stat("Total CO₂ Stored", f"{summary['total_co2_injected_tonnes']:,.0f}", "tonnes", "Total amount of CO₂ pumped underground."),
            _stat("Plume Spread", f"{summary['final_plume_radius_m']:.0f}", "meters", "How far CO₂ has spread from the injection well."),
            _stat("Leak Events", str(summary["leak_event_count"]), "occurrences", "Number of days where a leak was detected."),
            _stat("Average Daily", f"{summary['avg_daily_injection_tonnes']:,.0f}", "t/day", "Average tonnes injected per day."),
        ])

    status_style = dict(textAlign="center", fontSize="12px", fontWeight="600", marginTop="8px")
    if completed:
        status = html.Span("SIMULATION FINISHED — review results below", style={**status_style, "color": COLOR_SAFE})
    else:
        status = html.Span("RUNNING — streaming live data...", style={**status_style, "color": COLOR_ACCENT})

    return (
        trace_data, running, alerts, completed, prev_trace,
        not running, not running,
        status, status_style,
        fig_p, fig_i, fig_g, fig_c, fig_h,
        alert_children if alert_children else [html.Div("No issues detected yet.", style=dict(fontSize="12px", color=COLOR_HELP))],
        summary_panel,
    )


# ---------------------------------------------------------------
# callbacks — post-simulation controls (compare, export, reset)
# ---------------------------------------------------------------


@app.callback(
    Output("post-sim-controls", "children"),
    Input("store-completed", "data"),
    Input("store-running", "data"),
)
def show_post_controls(completed, running):
    if running:
        return html.Div("Charts updating... watch the dashboard on the right.", style=dict(fontSize="12px", color=COLOR_HELP, textAlign="center"))
    if not completed:
        return None
    return html.Div(
        [
            html.Button("Compare with Previous Run", id="btn-compare", n_clicks=0, style=dict(
                width="100%", padding="8px", fontSize="13px", fontWeight="600",
                border="1px solid", borderColor=COLOR_BORDER, borderRadius="6px", cursor="pointer",
                background=COLOR_BG, color=COLOR_TEXT, marginBottom="8px",
            )),
            html.Button("Export CSV", id="btn-export", n_clicks=0, style=dict(
                width="100%", padding="8px", fontSize="13px", fontWeight="600",
                border="1px solid", borderColor=COLOR_BORDER, borderRadius="6px", cursor="pointer",
                background=COLOR_BG, color=COLOR_TEXT, marginBottom="8px",
            )),
            html.Button("Reset", id="btn-reset", n_clicks=0, style=dict(
                width="100%", padding="8px", fontSize="13px", fontWeight="600", border="none",
                borderRadius="6px", cursor="pointer", background="#1e293b", color=COLOR_MUTED,
            )),
        ],
        style=dict(marginTop="-6px"),
    )


@app.callback(
    Output("chart-pressure", "figure", allow_duplicate=True),
    Output("chart-injection", "figure", allow_duplicate=True),
    Output("chart-cumulative", "figure", allow_duplicate=True),
    Input("btn-compare", "n_clicks"),
    State("store-trace", "data"),
    State("store-prev-trace", "data"),
    prevent_initial_call=True,
)
def compare_runs(_n, trace_data, prev_trace):
    if not prev_trace:
        return no_update, no_update, no_update

    df_cur = pd.DataFrame(trace_data)
    df_prev = pd.DataFrame(prev_trace)

    fig_p = go.Figure()
    fig_p.add_trace(go.Scatter(x=df_prev["day"], y=df_prev["pressure_MPa"], mode="lines",
                                line=dict(color=COLOR_MUTED, width=2, dash="dot"), name="Previous"))
    fig_p.add_trace(go.Scatter(x=df_cur["day"], y=df_cur["pressure_MPa"], mode="lines",
                                line=dict(color=COLOR_ACCENT, width=2), name="Current"))
    fig_p.update_layout(title="Pressure Comparison", paper_bgcolor=COLOR_SURFACE, plot_bgcolor=COLOR_SURFACE,
                        font_color=COLOR_TEXT, margin=dict(l=40, r=10, t=40, b=20),
                        legend=dict(orientation="h", y=1.12))
    _axis_style(fig_p, "Day", "MPa")

    fig_i = go.Figure()
    fig_i.add_trace(go.Bar(x=df_prev["day"], y=df_prev["co2_injected"], marker_color=COLOR_MUTED, opacity=0.5, name="Previous"))
    fig_i.add_trace(go.Bar(x=df_cur["day"], y=df_cur["co2_injected"], marker_color=COLOR_ACCENT, name="Current"))
    fig_i.update_layout(title="Injection Rate Comparison", paper_bgcolor=COLOR_SURFACE, plot_bgcolor=COLOR_SURFACE,
                        font_color=COLOR_TEXT, margin=dict(l=40, r=10, t=40, b=20), barmode="overlay",
                        legend=dict(orientation="h", y=1.12))
    _axis_style(fig_i, "Day", "Tonnes/Day")

    fig_c = go.Figure()
    fig_c.add_trace(go.Scatter(x=df_prev["day"], y=df_prev["co2_cumulative"], mode="lines",
                                line=dict(color=COLOR_MUTED, width=2, dash="dot"), name="Previous"))
    fig_c.add_trace(go.Scatter(x=df_cur["day"], y=df_cur["co2_cumulative"], mode="lines",
                                line=dict(color=COLOR_ACCENT, width=2), name="Current"))
    fig_c.update_layout(title="Cumulative CO₂ Comparison", paper_bgcolor=COLOR_SURFACE, plot_bgcolor=COLOR_SURFACE,
                        font_color=COLOR_TEXT, margin=dict(l=40, r=10, t=40, b=20),
                        legend=dict(orientation="h", y=1.12))
    _axis_style(fig_c, "Day", "Tonnes")

    return fig_p, fig_i, fig_c


@app.callback(
    Output("btn-export", "children"),
    Input("btn-export", "n_clicks"),
    State("store-trace", "data"),
    State("store-preset", "data"),
    prevent_initial_call=True,
)
def export_csv(_n, trace_data, preset):
    if not trace_data:
        return "Export CSV"

    df = pd.DataFrame(trace_data)
    final = trace_data[-1]
    preset_summary = {
        "case_id": preset.get("case_id", ""),
        "reservoir_type": preset.get("reservoir_type", ""),
        "p_init_MPa": preset.get("p_init_MPa", ""),
        "temp_C": preset.get("temp_C", ""),
    }
    for k, v in preset_summary.items():
        df[f"preset_{k}"] = v

    csv_buf = io.StringIO()
    df.to_csv(csv_buf, index=False)
    csv_b64 = base64.b64encode(csv_buf.getvalue().encode()).decode()

    return html.A(
        "Download CSV",
        href=f"data:text/csv;base64,{csv_b64}",
        download=f"ccs_simulation_{preset.get('case_id','')}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv",
        style=dict(
            display="block", width="100%", padding="8px", fontSize="13px", fontWeight="600",
            borderRadius="6px", textAlign="center", textDecoration="none", cursor="pointer",
            background=COLOR_BG, color=COLOR_TEXT, border=f"1px solid {COLOR_BORDER}",
        ),
    )


@app.callback(
    Output("store-trace", "data", allow_duplicate=True),
    Output("store-running", "data", allow_duplicate=True),
    Output("store-alerts", "data", allow_duplicate=True),
    Output("store-completed", "data", allow_duplicate=True),
    Output("store-prev-trace", "data", allow_duplicate=True),
    Output("tick", "disabled", allow_duplicate=True),
    Output("start-btn", "disabled", allow_duplicate=True),
    Output("status-indicator", "children", allow_duplicate=True),
    Output("status-indicator", "style", allow_duplicate=True),
    Output("chart-pressure", "figure", allow_duplicate=True),
    Output("chart-injection", "figure", allow_duplicate=True),
    Output("chart-gauge", "figure", allow_duplicate=True),
    Output("chart-cumulative", "figure", allow_duplicate=True),
    Output("chart-heatmap", "figure", allow_duplicate=True),
    Output("alert-list", "children", allow_duplicate=True),
    Output("summary-panel", "children", allow_duplicate=True),
    Output("post-sim-controls", "children", allow_duplicate=True),
    Input("btn-reset", "n_clicks"),
    prevent_initial_call=True,
)
def reset_simulation(_n):
    empty_figs = {
        "chart-pressure": _empty_figure("Pick a facility on the left and click Start to see pressure over time"),
        "chart-injection": _empty_figure("Daily injection rate will appear here as a bar chart"),
        "chart-gauge": _empty_figure("Pressure safety gauge will show here"),
        "chart-cumulative": _empty_figure("Total tonnes of CO₂ stored will build up here"),
        "chart-heatmap": _empty_figure("Underground pressure spread map will appear here"),
    }
    return (
        [], False, [], False, [], True, False,
        html.Span("READY — adjust settings and click Start", style=dict(textAlign="center", fontSize="12px", fontWeight="600", color=COLOR_HELP)),
        dict(textAlign="center", fontSize="12px", fontWeight="600", marginTop="8px"),
        empty_figs["chart-pressure"],
        empty_figs["chart-injection"],
        empty_figs["chart-gauge"],
        empty_figs["chart-cumulative"],
        empty_figs["chart-heatmap"],
        html.Div("Start a simulation to monitor for leaks and pressure warnings.", style=dict(fontSize="12px", color=COLOR_HELP)),
        None,
        None,
    )


# ---------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------

if __name__ == "__main__":
    print("Frontend running at http://localhost:8050")
    app.run(host="0.0.0.0", port=8050, debug=False)

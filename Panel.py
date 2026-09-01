"""Panel Główny — orkiestrator strony głównej dashboardu v2.

Mobile-first: status pompy, COP/SCOP, temperatury, przycisk licznika.
Desktop: + wykres parametrów na dole.
"""
import streamlit as st
import plotly.express as px
import pandas as pd
from datetime import datetime, timedelta

from app.ui.styles import inject_css, render_status_badge, render_temp_bar, render_scop_box
from app.ui.helpers import (
    cached_energy,
    load_latest_status,
    get_pump_status,
    get_temp_value,
)
from app.ui.labels import METRICS, scop_delta
from app.config import (
    PARAM_INFO, get_param_label,
    DEFAULT_COS_PHI, DEFAULT_STANDBY_POWER_W, DEFAULT_ACTIVE_POWER_W,
    DEFAULT_HIDDEN_POWER_W, DEFAULT_SENSOR_FACTOR,
)
from app.core.energy import scop_from_result


# --- Konfiguracja strony ---
st.set_page_config(
    page_title="Pompa Ciepła — Monitor",
    layout="wide",
    page_icon="🔥",
)
inject_css()


# --- Sidebar ---
with st.sidebar:
    st.markdown("### ⚙️ Ustawienia")

    selected_range = st.selectbox("Zakres SCOP:", [
        "Dzisiaj", "3 dni", "7 dni", "30 dni", "90 dni",
    ], index=0)

    with st.expander("🔧 Kalibracja"):
        cos_phi = st.number_input("cos φ", value=DEFAULT_COS_PHI, min_value=0.8, max_value=1.0, step=0.01)
        standby_w = st.number_input("Standby (widoczny) [W]", value=DEFAULT_STANDBY_POWER_W, step=5.0)
        active_w = st.number_input("Active (widoczny) [W]", value=DEFAULT_ACTIVE_POWER_W, step=5.0)
        hidden_w = st.number_input("Hidden power [W]", value=DEFAULT_HIDDEN_POWER_W, step=5.0,
                                   help="Stały pobór niewidoczny w czujniku (~20W). Kalibrowany z licznika.")
        sensor_f = st.number_input("Sensor factor", value=DEFAULT_SENSOR_FACTOR, step=0.01,
                                   help="Korekcja proporcjonalna czujnika. 0.98 = telemetria zawyża ~2% vs licznik.")

    with st.expander("ℹ️ About / Help"):
        st.markdown("""
        **Tuya Heat Pump Monitor v2**

        Silnik obliczeniowy: `compute_energy()`
        - Energia z surowych danych (bez resample)
        - SCOP liczony jedną funkcją `compute_scop()` (realny, z odliczeniem defrostu)
        - Ta sama wartość na każdej stronie
        """)
        st.page_link("pages/3_Wiedza.py", label="📚 Baza Wiedzy")


# --- Obliczenie dat ---
now = datetime.now()
range_days_map = {"Dzisiaj": 0, "3 dni": 3, "7 dni": 7, "30 dni": 30, "90 dni": 90}
days_back = range_days_map[selected_range]

if days_back == 0:
    date_from = now.strftime("%Y-%m-%d")
else:
    date_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
date_to = None

cal_params = dict(
    cos_phi=cos_phi, standby_power_w=standby_w, active_power_w=active_w,
    hidden_power_w=hidden_w, sensor_factor=sensor_f,
)


# --- Dane na żywo ---
status = load_latest_status()
pump_label, pump_color, pump_emoji = get_pump_status(status)


# --- Obliczenie energii (cache 60s) ---
# Jedno wywołanie (total) — SCOP CO/CWU/total liczymy przez compute_scop() z tego samego wyniku.
energy = cached_energy(date_from=date_from, date_to=date_to, **cal_params)

scop_total = scop_from_result(energy, scope="total", kind="real")
scop_co = scop_from_result(energy, scope="co", kind="real")
scop_cwu = scop_from_result(energy, scope="cwu", kind="real")


# === LAYOUT ===

# --- Header: tytuł + badge statusu w jednej linii ---
st.markdown(
    f'<div style="display:flex;align-items:center;gap:1rem;margin-top:0.5rem;margin-bottom:0.5rem;">'
    f'<h3 style="margin:0;padding:0;">🔥 Pompa Ciepła</h3>'
    f'<span class="pump-status" style="background:{pump_color}22;color:{pump_color};'
    f'border:2px solid {pump_color};padding:0.4rem 1rem;">{pump_emoji} {pump_label}</span>'
    f'</div>',
    unsafe_allow_html=True,
)

# --- COP chwilowy + SCOP + Energia w jednym rzędzie ---
cop_val = status.get("comp_freq", {}).get("val_num", 0) or 0
p_el_raw = ((status.get("ac_vol", {}).get("val_num", 0) or 0)
            * ((status.get("ac_curr", {}).get("val_num", 0) or 0) / 10) * cos_phi)
flow = (status.get("flow_rate", {}).get("val_num", 0) or 0) / 10
t_out = get_temp_value(status, "out_water_temp") or 0
t_in = get_temp_value(status, "in_water_temp") or 0
p_th_raw = flow * 4.186 * (t_out - t_in) / 3.6 * 1000
cop_instant = p_th_raw / p_el_raw if p_el_raw > 100 and p_th_raw > 0 else 0

c1, c2, c3, c4 = st.columns(4)
with c1:
    cop_display = f"{cop_instant:.2f}" if cop_instant > 0.5 else "—"
    st.metric(METRICS["cop_instant"]["label"], cop_display,
              help=METRICS["cop_instant"]["help"])
with c2:
    scop_display = f"{scop_total:.2f}" if scop_total > 0 else "—"
    delta, delta_color = scop_delta(scop_total)
    st.metric(f"SCOP {selected_range}", scop_display, delta=delta, delta_color=delta_color,
              help=METRICS["scop_range"]["help"])
with c3:
    st.metric(METRICS["e_el_short"]["label"], f"{energy.e_el_total:.1f} kWh",
              help=METRICS["e_el_short"]["help"])
with c4:
    st.metric(METRICS["e_th_short"]["label"], f"{energy.e_th_total:.1f} kWh",
              help=METRICS["e_th_short"]["help"])


# --- Temperatury + SCOP box w dwóch kolumnach ---
col_temp, col_scop = st.columns([3, 2])

with col_temp:
    t_supply = get_temp_value(status, "out_water_temp")
    t_set_co = get_temp_value(status, "heat_temp_set") or get_temp_value(status, "idr_temp_set")
    t_cwu = get_temp_value(status, "tank_temp")
    t_set_cwu = get_temp_value(status, "hot_water_temp_set")

    render_temp_bar("Zasilanie CO", t_supply, "temp-bar-co")
    render_temp_bar("🎯 Nastawa CO", t_set_co, "temp-bar-co", is_setpoint=True)
    render_temp_bar("Woda CWU", t_cwu, "temp-bar-cwu")
    render_temp_bar("🎯 Nastawa CWU", t_set_cwu, "temp-bar-cwu", is_setpoint=True)

with col_scop:
    render_scop_box(
        scop_co=scop_co if energy.e_th_co >= 1.0 else 0,
        scop_cwu=scop_cwu if energy.e_th_cwu >= 1.0 else 0,
        scop_total=scop_total,
        label=f"SCOP {selected_range}",
    )
    # Mini przycisk licznika pod SCOP boxem
    st.markdown("")
    if st.button("⚡ Wpisz stan licznika"):
        st.switch_page("pages/2_Licznik.py")


# --- Wykres parametrów (desktop) ---
st.markdown("---")
st.subheader("📈 Przebieg parametrów")


@st.cache_data(ttl=60)
def _load_chart_data(date_from: str, device_id: str = "bf874f7ae72aca1fc23op0") -> pd.DataFrame:
    """Surowe dane do wykresu (resample do wizualizacji, NIE do obliczeń)."""
    import sqlite3
    from app.config import DB_FILE, DEFAULT_TIME_OFFSET_HOURS
    try:
        conn = sqlite3.connect(DB_FILE)
        off = DEFAULT_TIME_OFFSET_HOURS
        query = f"""
            SELECT datetime(timestamp, 'unixepoch', '{off:+d} hours') as czas,
                   code, val_num
            FROM telemetry
            WHERE device_id = ? AND timestamp >= strftime('%s', ?, '{-off:+d} hours')
            ORDER BY timestamp
        """
        df = pd.read_sql_query(query, conn, params=(device_id, date_from))
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


chart_df = _load_chart_data(date_from)
if not chart_df.empty:
    all_codes = chart_df["code"].unique().tolist()
    default_temps = [c for c in ["tank_temp", "in_water_temp", "out_water_temp", "heat_temp_set", "amb_temp"]
                     if c in all_codes]

    selected = st.multiselect(
        "Parametry:", options=all_codes, default=default_temps,
        format_func=get_param_label,
    )

    if selected:
        plot_df = chart_df[chart_df["code"].isin(selected) & chart_df["val_num"].notnull()].copy()
        plot_df["Parametr"] = plot_df["code"].map(lambda c: PARAM_INFO.get(c, {}).get("label", c))

        fig = px.line(
            plot_df, x="czas", y="val_num", color="Parametr",
            labels={"czas": "Czas", "val_num": "Wartość"},
        )
        fig.update_layout(
            template="plotly_dark",
            hovermode="x unified",
            xaxis_title="Czas",
            yaxis_title="Wartość",
            legend=dict(orientation="h", yanchor="bottom", y=-0.3),
            height=400,
            margin=dict(t=10, b=60),
        )
        st.plotly_chart(fig, width="stretch")
else:
    st.info("Brak danych w wybranym zakresie.")

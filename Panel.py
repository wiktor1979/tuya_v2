"""Panel Główny — orkiestrator strony głównej dashboardu v2.

Mobile-first: status pompy, COP/SCOP, temperatury, przycisk licznika.
Desktop: + wykres parametrów na dole.
"""
import streamlit as st
import plotly.express as px
import pandas as pd
from datetime import datetime, timedelta

from app.ui.styles import inject_css, render_status_badge, render_temp_bar, render_scop_box, render_about, render_temp_bar_setpoint
from app.ui.helpers import (
    load_latest_status,
    get_pump_status,
    get_temp_value,
)
from app.ui.labels import METRICS
from app.config import (
    PARAM_INFO, get_param_label,
    DEFAULT_COS_PHI, DEFAULT_STANDBY_POWER_W, DEFAULT_ACTIVE_POWER_W,
    DEFAULT_HIDDEN_POWER_W, DEFAULT_SENSOR_FACTOR,
)
from app.core.energy import scop_from_result, compute_energy


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

    render_about()


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


# --- Przycisk odśwież + adaptacyjny interwał auto-refresh (jak v1) ---
_status_probe = load_latest_status()
_pump_running = get_pump_status(_status_probe)[0] not in ("Postój", "AWARIA")
_refresh_sec = 60 if _pump_running else 300

if st.button("🔄 Odśwież dane"):
    st.rerun()


@st.fragment(run_every=_refresh_sec)
def render_live():
    """Sekcja danych na żywo — odświeżana automatycznie co _refresh_sec.

    Pompa pracuje → co 60s, postój → co 300s. Poza fragmentem: sidebar,
    nagłówek, wykres parametrów (własny cache/multiselect).
    """
    # --- Dane na żywo ---
    status = load_latest_status()
    pump_label, pump_color, pump_emoji = get_pump_status(status)

    # Status odświeżania z timestampem
    running = pump_label not in ("Postój", "AWARIA")
    icon = "🟢" if running else "⚪"
    txt = "Pompa pracuje — odświeżanie co 1 min" if running else "Pompa stoi — odświeżanie co 5 min"
    st.caption(f"{icon} {txt} · Odświeżono: {datetime.now().strftime('%H:%M:%S')}")

    # --- Obliczenie energii ---
    # Wołamy compute_energy() BEZPOŚREDNIO (nie cached_energy) — @st.cache_data
    # wewnątrz @st.fragment miewa problem z serializacją zwrotu (EnergyResult).
    # We fragmencie odświeżanym co 60s cache i tak nie daje korzyści.
    # SCOP CO/CWU/total liczymy przez compute_scop() z tego samego wyniku.
    energy = compute_energy(date_from=date_from, date_to=date_to, **cal_params)

    scop_total = scop_from_result(energy, scope="total", kind="real")
    scop_co = scop_from_result(energy, scope="co", kind="real")
    scop_cwu = scop_from_result(energy, scope="cwu", kind="real")

    # --- Header: tytuł + badge statusu w jednej linii ---
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:1rem;margin-top:0.5rem;margin-bottom:0.5rem;">'
        f'<h3 style="margin:0;padding:0;">🔥 Pompa Ciepła</h3>'
        f'<span class="pump-status" style="background:{pump_color}22;color:{pump_color};'
        f'border:2px solid {pump_color};padding:0.4rem 1rem;">{pump_emoji} {pump_label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # --- COP chwilowy (do metryki) ---
    cop_val = status.get("comp_freq", {}).get("val_num", 0) or 0
    p_el_raw = ((status.get("ac_vol", {}).get("val_num", 0) or 0)
                * ((status.get("ac_curr", {}).get("val_num", 0) or 0) / 10) * cos_phi)
    flow = (status.get("flow_rate", {}).get("val_num", 0) or 0) / 10
    t_out = get_temp_value(status, "out_water_temp") or 0
    t_in = get_temp_value(status, "in_water_temp") or 0
    p_th_raw = flow * 4.186 * (t_out - t_in) / 3.6 * 1000
    cop_instant = p_th_raw / p_el_raw if p_el_raw > 100 and p_th_raw > 0 else 0

    # --- Górny rząd: lewo = SCOP box, prawo = 3 metryki (COP/Energia/Ciepło) ---
    col_scop, col_metrics = st.columns([2, 3])
    with col_scop:
        render_scop_box(
            scop_co=scop_co if energy.e_th_co >= 1.0 else 0,
            scop_cwu=scop_cwu if energy.e_th_cwu >= 1.0 else 0,
            scop_total=scop_total,
            label=f"SCOP {selected_range}",
        )
    with col_metrics:
        cop_display = f"{cop_instant:.2f}" if cop_instant > 0.5 else "—"
        st.metric(METRICS["cop_instant"]["label"], cop_display,
                  help=METRICS["cop_instant"]["help"])
        st.metric(METRICS["e_el_short"]["label"], f"{energy.e_el_total:.1f} kWh",
                  help=METRICS["e_el_short"]["help"])
        st.metric(METRICS["e_th_short"]["label"], f"{energy.e_th_total:.1f} kWh",
                  help=METRICS["e_th_short"]["help"])

    # --- Temperatury CO / CWU (wartość + marker nastawy) — pełna szerokość ---
    t_supply = get_temp_value(status, "out_water_temp")
    t_set_co = get_temp_value(status, "heat_temp_set") or get_temp_value(status, "idr_temp_set")
    t_cwu = get_temp_value(status, "tank_temp")
    t_set_cwu = get_temp_value(status, "hot_water_temp_set")

    render_temp_bar_setpoint("🔥 CO", t_supply, t_set_co, "temp-bar-co", max_temp=55.0, min_temp=15.0)
    render_temp_bar_setpoint("🚿 CWU", t_cwu, t_set_cwu, "temp-bar-cwu", max_temp=60.0, min_temp=15.0)

    if st.button("⚡ Wpisz stan licznika"):
        st.switch_page("pages/4_Licznik.py")


render_live()


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

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
    get_pump_activity,
    PUMP_ACTIVITY_COLORS,
    PUMP_ACTIVITY_HEATING,
    PUMP_ACTIVITY_RUNNING,
    PUMP_ACTIVITY_OFF,
    get_temp_value,
    load_calibration,
    get_selected_pump,
    render_pump_selector,
    get_live_heating_mode,
    pump_supports_cwu,
    weather_daily_for_range,
    get_chart_params,
    persist_chart_params,
)
from app.ui.labels import METRICS
from app.config import (
    PARAM_INFO, get_param_label, HEAT_PUMP_DEV_ID,
    list_pumps, get_pump,
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

    # --- Wybór pompy (trwały: URL + session_state + localStorage przeglądarki) ---
    selected_pump = render_pump_selector()
    _sel_id = selected_pump["id"]
    sel_device_id = selected_pump["device_id"]
    sel_meter_id = selected_pump["meter_id"]
    sel_thermo_id = selected_pump.get("thermo_id")

    selected_range = st.selectbox("Zakres SCOP:", [
        "Dzisiaj", "3 dni", "7 dni", "30 dni", "90 dni",
    ], index=0)

    cal_params = load_calibration()

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


# --- Adaptacyjny interwał auto-refresh (jak v1) ---
# Aktywny (grzeje LUB pompa wody pracuje) → 60s, postój → 300s.
_status_probe = load_latest_status(device_id=sel_device_id)
_pump_active = get_pump_activity(_status_probe) != PUMP_ACTIVITY_OFF
_refresh_sec = 60 if _pump_active else 300


@st.fragment(run_every=_refresh_sec)
def render_live():
    """Sekcja danych na żywo — odświeżana automatycznie co _refresh_sec.

    Pompa pracuje → co 60s, postój → co 300s. Poza fragmentem: sidebar,
    nagłówek, wykres parametrów (własny cache/multiselect).
    """
    # --- Dane na żywo ---
    status = load_latest_status(device_id=sel_device_id)

    # Stan agregatu — 3 stany (kanonicznie przez get_pump_activity):
    #   heating = grzeje (sprężarka ON), running = działa (pompa wody ON), off = nie działa.
    activity = get_pump_activity(status)
    running = activity != PUMP_ACTIVITY_OFF  # „pompa pracuje" (heating lub running)

    # Co pompa grzeje TERAZ: 'co'/'cwu'/None (None = obieg wody/postój/defrost).
    # Steruje podświetleniem sekcji CO/CWU w kafelku SCOP i nagłówków pasków temperatur.
    active_mode = get_live_heating_mode(status)

    # Czy pompa obsługuje CWU (czujnik zasobnika podłączony — tank_temp >= 0).
    # Pompa bez CWU (tank_temp < 0) → ukrywamy pasek CWU i rozbicie CWU na kafelku SCOP.
    has_cwu = pump_supports_cwu(status)

    # --- Obliczenie energii ---
    # Wołamy compute_energy() BEZPOŚREDNIO (nie cached_energy) — @st.cache_data
    # wewnątrz @st.fragment miewa problem z serializacją zwrotu (EnergyResult).
    # We fragmencie odświeżanym co 60s cache i tak nie daje korzyści.
    # SCOP CO/CWU/total liczymy przez compute_scop() z tego samego wyniku.
    from app.config import SERVER_TIMEZONE_OFFSET as _tz
    _weather_daily = weather_daily_for_range(date_from, date_to, _tz)
    energy = compute_energy(date_from=date_from, date_to=date_to, device_id=sel_device_id,
                            weather_daily=_weather_daily, **cal_params)

    scop_total = scop_from_result(energy, scope="total", kind="real")
    scop_co = scop_from_result(energy, scope="co", kind="real")
    scop_cwu = scop_from_result(energy, scope="cwu", kind="real")

    # --- Header jako PRZYCISK odświeżania; tło sygnalizuje stan pompy (3 stany) ---
    #   grzeje (sprężarka ON) → pomarańczowo-czerwony (ogień),
    #   działa (pompa wody ON, sprężarka OFF) → zielony,
    #   nie działa → szary.
    if activity == PUMP_ACTIVITY_HEATING:
        hdr_bg = "linear-gradient(90deg,#E67E22,#e94560)"
        hdr_fg = "#fff"
        accent = "#E67E22"
    elif activity == PUMP_ACTIVITY_RUNNING:
        hdr_bg = "linear-gradient(90deg,#27AE60,#2ECC71)"
        hdr_fg = "#fff"
        accent = "#2ECC71"
    else:
        hdr_bg = "#3a3f4b"
        hdr_fg = "#bbb"
        accent = "#7a8090"
    st.markdown(
        f"""<style>
        /* Wspólne: oba przyciski w nagłówku tej samej wysokości i wyrównane */
        .st-key-pump_header button {{
            font-size: 1.05rem !important;
            font-weight: 700 !important;
            padding: 0.5rem 1rem !important;
            width: 100% !important;
            line-height: 1.3 !important;
            transition: background 0.3s ease, border-color 0.3s ease;
        }}
        /* Przycisk odświeżania — kolorowe tło zależne od stanu pompy */
        .st-key-pump_header_btn button {{
            background: {hdr_bg} !important;
            color: {hdr_fg} !important;
            border: none !important;
        }}
        /* Przycisk Bilans — bez wypełnienia, tylko ramka w kolorze akcentu stanu */
        .st-key-pump_header_bilans_btn button {{
            background: transparent !important;
            color: {accent} !important;
            border: 2px solid {accent} !important;
        }}
        .st-key-pump_header_bilans_btn button:hover {{
            background: {accent}22 !important;
            color: {accent} !important;
            border-color: {accent} !important;
        }}
        </style>""",
        unsafe_allow_html=True,
    )
    with st.container(key="pump_header"):
        col_refresh, col_bilans = st.columns([1, 1])
        now_txt = datetime.now().strftime("%H:%M:%S")
        with col_refresh:
            if st.button(f"🔥 {selected_pump['name']} · {now_txt}", key="pump_header_btn", help="Kliknij, aby odświeżyć teraz"):
                st.rerun()
        with col_bilans:
            if st.button("📊 Bilans", key="pump_header_bilans_btn", help="Przejdź do bilansu i SCOP"):
                st.switch_page("pages/1_Bilans.py")

    # --- COP chwilowy (do metryki) ---
    cop_val = status.get("comp_freq", {}).get("val_num", 0) or 0
    p_el_raw = ((status.get("ac_vol", {}).get("val_num", 0) or 0)
                * ((status.get("ac_curr", {}).get("val_num", 0) or 0) / 10) * cal_params["cos_phi"])
    flow = (status.get("flow_rate", {}).get("val_num", 0) or 0) / 10
    t_out = get_temp_value(status, "out_water_temp") or 0
    t_in = get_temp_value(status, "in_water_temp") or 0
    p_th_raw = flow * 4.186 * (t_out - t_in) / 3.6 * 1000
    cop_instant = p_th_raw / p_el_raw if p_el_raw > 100 and p_th_raw > 0 else 0

    # --- Układ responsywny: bazowo SCOP (lewo) + metryki (prawo) w 2 kolumnach
    #     — dobre na desktop. Na wąskim ekranie CSS (.st-key-panel_top) przestawia
    #     ten blok w pion: SCOP pełna szer. → metryki pod spodem. ---
    with st.container(key="panel_top"):
        col_scop, col_metrics = st.columns([2, 3])
        with col_scop:
            render_scop_box(
                scop_co=scop_co if energy.e_th_co >= 1.0 else 0,
                scop_cwu=scop_cwu if energy.e_th_cwu >= 1.0 else 0,
                scop_total=scop_total,
                label=f"SCOP {selected_range}",
                running=running,
                active_mode=active_mode,
                show_cwu=has_cwu,
            )
        with col_metrics:
            # COP chwilowy — pełna szerokość kolumny metryk
            cop_display = f"{cop_instant:.2f}" if cop_instant > 0.5 else "—"
            st.metric(METRICS["cop_instant"]["label"], cop_display,
                      help=METRICS["cop_instant"]["help"])

            # Chwilowe moce (kW) — pobór prądu i moc cieplna pompy, obok siebie
            p_el_kw = p_el_raw / 1000
            p_th_kw = p_th_raw / 1000
            p_el_display = f"{p_el_kw:.2f} kW" if p_el_raw > 100 else "—"
            p_th_display = f"{p_th_kw:.2f} kW" if (p_el_raw > 100 and p_th_raw > 0) else "—"
            col_pel, col_pth = st.columns(2)
            with col_pel:
                st.metric(METRICS["p_el_instant"]["label"], p_el_display,
                          help=METRICS["p_el_instant"]["help"])
            with col_pth:
                st.metric(METRICS["p_th_instant"]["label"], p_th_display,
                          help=METRICS["p_th_instant"]["help"])

            # Energia / Ciepło okresowe — obok siebie (2 kolumny)
            col_eel, col_eth = st.columns(2)
            with col_eel:
                st.metric(METRICS["e_el_short"]["label"], f"{energy.e_el_total:.1f} kWh",
                          help=METRICS["e_el_short"]["help"])
            with col_eth:
                st.metric(METRICS["e_th_short"]["label"], f"{energy.e_th_total:.1f} kWh",
                          help=METRICS["e_th_short"]["help"])

    # --- Temperatury CO / CWU (wartość + marker nastawy) — pełna szerokość ---
    t_supply = get_temp_value(status, "out_water_temp")
    t_set_co = get_temp_value(status, "heat_temp_set") or get_temp_value(status, "idr_temp_set")
    t_cwu = get_temp_value(status, "tank_temp")
    t_set_cwu = get_temp_value(status, "hot_water_temp_set")

    # Wspólna skala dla obu barów (15–60°C), aby ta sama nastawa była w tym samym
    # miejscu i paski były porównywalne wprost (różne skale myliły — 35°C wypadało indziej).
    # Aktywny tryb (grzeje teraz) → label pogrubiony + „grzeje" w kolorze trybu.
    co_label = (
        '🔥 <b style="color:#2196F3;">CO · grzeje</b>' if active_mode == "co" else "🔥 CO"
    )
    cwu_label = (
        '🚿 <b style="color:#E67E22;">CWU · grzeje</b>' if active_mode == "cwu" else "🚿 CWU"
    )
    render_temp_bar_setpoint(co_label, t_supply, t_set_co, "temp-bar-co", max_temp=60.0, min_temp=15.0)
    # Pasek CWU tylko gdy pompa obsługuje CWU (czujnik zasobnika podłączony).
    if has_cwu:
        render_temp_bar_setpoint(cwu_label, t_cwu, t_set_cwu, "temp-bar-cwu", max_temp=60.0, min_temp=15.0)


render_live()


# --- Wykres parametrów (desktop) ---
st.markdown("---")
st.subheader("📈 Przebieg parametrów")


@st.cache_data(ttl=60)
def _load_chart_data(
    date_from: str,
    device_id: str = HEAT_PUMP_DEV_ID,
    thermo_id: str = None,
) -> pd.DataFrame:
    """Surowe dane do wykresu (resample do wizualizacji, NIE do obliczeń).

    Jeśli pompa ma powiązany termometr (thermo_id), dociąga też jego temperaturę
    pokojową jako kod 'va_temperature'. Duplikat 'temp_current' jest pomijany
    (ten sam pomiar pod innym kodem DP)."""
    import sqlite3
    from app.config import DB_FILE, SERVER_TIMEZONE_OFFSET
    try:
        conn = sqlite3.connect(DB_FILE)
        off = SERVER_TIMEZONE_OFFSET
        query = f"""
            SELECT datetime(timestamp, 'unixepoch', '{off:+d} hours') as czas,
                   code, val_num
            FROM telemetry
            WHERE device_id = ? AND timestamp >= strftime('%s', ?, '{-off:+d} hours')
            ORDER BY timestamp
        """
        df = pd.read_sql_query(query, conn, params=(device_id, date_from))

        # Termometr powiązany z pompą — tylko va_temperature (kanoniczny; temp_current
        # to duplikat, pomijany, by nie dublować linii na wykresie).
        if thermo_id:
            thermo_query = f"""
                SELECT datetime(timestamp, 'unixepoch', '{off:+d} hours') as czas,
                       code, val_num
                FROM telemetry
                WHERE device_id = ? AND code = 'va_temperature'
                  AND timestamp >= strftime('%s', ?, '{-off:+d} hours')
                ORDER BY timestamp
            """
            thermo_df = pd.read_sql_query(thermo_query, conn, params=(thermo_id, date_from))
            if not thermo_df.empty:
                df = pd.concat([df, thermo_df], ignore_index=True)

        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


chart_df = _load_chart_data(date_from, device_id=sel_device_id, thermo_id=sel_thermo_id)
if not chart_df.empty:
    all_codes = chart_df["code"].unique().tolist()
    default_temps = [c for c in ["tank_temp", "in_water_temp", "out_water_temp", "heat_temp_set", "amb_temp"]
                     if c in all_codes]

    # Konfiguracja wykresu zapamiętana w localStorage (per urządzenie, wspólna dla pomp).
    # Zapisany układ ograniczamy do kodów realnie obecnych w danych (nieaktualne pomijamy).
    saved_params = [c for c in get_chart_params(default_temps) if c in all_codes]
    initial = saved_params if saved_params else default_temps

    selected = st.multiselect(
        "Parametry:", options=all_codes, default=initial,
        format_func=get_param_label,
    )

    # Zapis ręczny — dopiero po kliknięciu przycisku (przypadkowa zmiana nie nadpisuje układu).
    if st.button("💾 Zapisz układ wykresu"):
        persist_chart_params(selected)
        st.toast("Zapisano układ wykresu na tym urządzeniu.")

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

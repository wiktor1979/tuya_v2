"""Podstrona: Analiza Parametrów — Hydraulika/ΔT, Sprężarka, Defrost, Krzywa Grzewcza.

Przepisane z v1 na architekturę v2:
- v1 używał process_telemetry() (pivot z resample) — v2 używa load_analiza_pivot()
  (pivot z surowych danych, TYLKO do wizualizacji/diagnostyki).
- Energia/SCOP NIE są tu liczone — od tego jest compute_energy() (strona Bilans).
- Zakładka COP z v1 pominięta (SCOP jest na stronie Bilans).
"""
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.ui.styles import inject_css
from app.ui.analiza_helpers import load_analiza_pivot
from app.ui import tab_heating_curve
from app.services.database import get_weather_data

st.set_page_config(page_title="Analiza Parametrów — Pompa Ciepła", layout="wide", page_icon="🔬")
inject_css()

st.markdown('<h3 style="margin:0;padding:0.2rem 0;">🔬 Analiza Parametrów</h3>', unsafe_allow_html=True)

# --- Sidebar: zakres ---
with st.sidebar:
    st.markdown("### ⚙️ Ustawienia")
    selected_range = st.selectbox("Zakres:", ["Dzisiaj", "3 dni", "7 dni", "30 dni", "90 dni"], index=2)
    if st.button("🔄 Odśwież dane"):
        st.cache_data.clear()
        st.rerun()

range_hours = {"Dzisiaj": 24, "3 dni": 72, "7 dni": 168, "30 dni": 720, "90 dni": 2160}
hours_back = range_hours[selected_range]

# --- Dane ---
df_pivot = load_analiza_pivot(hours_back=hours_back)
df_pivot_all = load_analiza_pivot(hours_back=0, all_time=True)

# Pogoda (dla krzywej grzewczej)
weather_df_analysis = pd.DataFrame()
try:
    weather_data = get_weather_data(days=max(30, hours_back // 24 + 1))
    if weather_data and len(weather_data) > 0:
        col_names = ['id', 'timestamp', 'temperature', 'humidity', 'windspeed', 'precipitation',
                     'latitude', 'longitude', 'direct_radiation', 'diffuse_radiation']
        weather_df_analysis = pd.DataFrame(weather_data, columns=col_names[:len(weather_data[0])])
except Exception:
    pass


def safe_col(df: pd.DataFrame, col: str) -> bool:
    return col in df.columns and df[col].notna().any()


def kpi_with_status(label: str, value, unit: str = "", norm_min=None, norm_max=None, fmt: str = ".1f"):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        st.metric(label, "—")
        return
    formatted = f"{value:{fmt}}{unit}"
    delta_text = None
    if norm_min is not None and norm_max is not None:
        if norm_min <= value <= norm_max:
            delta_text = f"✓ Norma ({norm_min}-{norm_max}{unit})"
        elif value < norm_min:
            delta_text = f"↓ Poniżej normy ({norm_min}{unit})"
        else:
            delta_text = f"↑ Powyżej normy ({norm_max}{unit})"
    delta_color = "normal" if delta_text and "✓" in delta_text else "inverse"
    st.metric(label, formatted, delta=delta_text, delta_color=delta_color)


if df_pivot is None or df_pivot.empty:
    st.warning("⚠️ Brak danych w wybranym zakresie czasu. Zmień zakres w panelu bocznym.")
    st.stop()

tab_hydr, tab_comp, tab_defr, tab_curve = st.tabs([
    "💧 Hydraulika i ΔT",
    "⚙️ Sprężarka i Taktowanie",
    "❄️ Defrost i Obieg Chłodniczy",
    "📈 Krzywa Grzewcza",
])


# ==============================================================================
# TAB 1: HYDRAULIKA I ΔT
# ==============================================================================
with tab_hydr:
    st.subheader("💧 Hydraulika i Wymiana Ciepła")

    col1, col2, col3, col4 = st.columns(4)
    co_mask = df_pivot["Tryb"] == "CO"
    cwu_mask = df_pivot["Tryb"] == "CWU"

    with col1:
        dt_co = df_pivot.loc[co_mask, "delta_t"].dropna().iloc[-1] if co_mask.any() and safe_col(df_pivot, "delta_t") else None
        kpi_with_status("ΔT aktualny (CO)", dt_co, "°C", 3.0, 7.0)
    with col2:
        dt_cwu = df_pivot.loc[cwu_mask, "delta_t"].dropna().iloc[-1] if cwu_mask.any() and safe_col(df_pivot, "delta_t") else None
        kpi_with_status("ΔT aktualny (CWU)", dt_cwu, "°C", 5.0, 10.0)
    with col3:
        flow_last = df_pivot["flow_m3h"].dropna().iloc[-1] * 1000 / 60 if safe_col(df_pivot, "flow_m3h") else None
        kpi_with_status("Przepływ", flow_last, " l/min", 5.0, 25.0)
    with col4:
        current_mode = df_pivot["Tryb"].iloc[-1] if "Tryb" in df_pivot.columns else "—"
        st.metric("Tryb aktualny", current_mode)

    st.divider()

    st.markdown("##### 🌡️ ΔT w czasie — z zakresami prawidłowymi")
    if safe_col(df_pivot, "delta_t"):
        fig_dt = go.Figure()
        fig_dt.add_trace(go.Scatter(
            x=df_pivot["czas"], y=df_pivot["delta_t"], mode="lines", name="ΔT (°C)",
            line=dict(color="#FF9800", width=2), fill="tozeroy", fillcolor="rgba(255,152,0,0.08)",
        ))
        fig_dt.add_hrect(y0=3, y1=7, fillcolor="rgba(76,175,80,0.05)", line=dict(width=0),
                         annotation_text="Norma CO (3-7°C)", annotation_position="top left")
        dt_max = df_pivot["delta_t"].max()
        fig_dt.update_layout(
            yaxis_title="ΔT [°C]", height=350, margin=dict(t=20, b=40), template="plotly_dark",
            xaxis_title="Czas", yaxis=dict(range=[0, max(15, dt_max * 1.2 if dt_max > 0 else 15)]),
        )
        st.plotly_chart(fig_dt, width="stretch")
    else:
        st.info("Brak danych ΔT.")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### 💧 Przepływ w czasie")
        if safe_col(df_pivot, "flow_m3h"):
            flow_lmin = df_pivot["flow_m3h"] * 1000 / 60
            fig_flow = go.Figure()
            fig_flow.add_trace(go.Scatter(
                x=df_pivot["czas"], y=flow_lmin, mode="lines", name="Przepływ (l/min)",
                line=dict(color="#2196F3", width=2), fill="tozeroy", fillcolor="rgba(33,150,243,0.08)",
            ))
            fig_flow.update_layout(yaxis_title="Przepływ [l/min]", height=320, margin=dict(t=20, b=40),
                                   template="plotly_dark", xaxis_title="Czas")
            st.plotly_chart(fig_flow, width="stretch")
        else:
            st.info("Brak danych przepływu.")

    with col_b:
        st.markdown("##### 📉 Korelacja ΔT vs Przepływ")
        if safe_col(df_pivot, "delta_t") and safe_col(df_pivot, "flow_m3h"):
            flow_lmin_col = df_pivot["flow_m3h"] * 1000 / 60
            mask_valid = df_pivot["delta_t"].notna() & flow_lmin_col.notna() & (flow_lmin_col > 0)
            if mask_valid.any():
                scatter_df = pd.DataFrame({
                    "Przepływ (l/min)": flow_lmin_col[mask_valid],
                    "ΔT (°C)": df_pivot.loc[mask_valid, "delta_t"],
                    "Tryb": df_pivot.loc[mask_valid, "Tryb"],
                })
                fig_scatter_dt = px.scatter(
                    scatter_df, x="Przepływ (l/min)", y="ΔT (°C)", color="Tryb",
                    color_discrete_map={"CO": "#FF9800", "CWU": "#9C27B0"}, opacity=0.5, trendline="ols",
                )
                fig_scatter_dt.update_layout(height=320, margin=dict(t=20, b=40), template="plotly_dark",
                                             xaxis_title="Przepływ [l/min]", yaxis_title="ΔT [°C]")
                st.plotly_chart(fig_scatter_dt, width="stretch")
            else:
                st.info("Za mało danych do korelacji.")
        else:
            st.info("Brak danych do korelacji ΔT/przepływ.")

    st.markdown("##### ⚠️ Alerty hydrauliki")
    if safe_col(df_pivot, "delta_t"):
        alert_dt = df_pivot[(df_pivot["delta_t"] > 8) & (df_pivot["Tryb"] == "CO") & df_pivot["delta_t"].notna()]
        if not alert_dt.empty:
            for _, row in alert_dt.tail(3).iterrows():
                st.warning(
                    f"⚠️ {row['czas'].strftime('%d.%m %H:%M')} — ΔT = {row['delta_t']:.1f}°C w CO "
                    f"(max 7°C). Możliwy niedostateczny przepływ — sprawdź filtr siatkowy."
                )
        else:
            st.success("✅ ΔT w normie dla trybu CO.")


# ==============================================================================
# TAB 2: SPRĘŻARKA I TAKTOWANIE
# ==============================================================================
with tab_comp:
    st.subheader("⚙️ Stabilność i Żywotność Sprężarki")

    if safe_col(df_pivot, "work_period") and safe_col(df_pivot, "comp_on"):
        work_periods = df_pivot[df_pivot["comp_on"] == 1].groupby("work_period")["dt_hours"].sum() * 60
        # pomiń "cykl 0" (przed pierwszym startem)
        work_periods = work_periods[work_periods.index > 0]
        avg_cycle_min = work_periods.mean() if len(work_periods) > 0 else None
        num_starts = len(work_periods)
    else:
        work_periods = pd.Series(dtype=float)
        avg_cycle_min = None
        num_starts = 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_with_status("Średni czas cyklu", avg_cycle_min, " min", 30, 120)
    with col2:
        total_hours = (df_pivot["czas"].max() - df_pivot["czas"].min()).total_seconds() / 3600 if len(df_pivot) > 1 else 1
        starts_per_day = num_starts / max(total_hours / 24, 0.01)
        kpi_with_status("Starty / dobę", starts_per_day, "", 0, 15, fmt=".0f")
    with col3:
        comp_freq_last = df_pivot["comp_freq"].dropna().iloc[-1] if safe_col(df_pivot, "comp_freq") else None
        st.metric("Częstotliwość spr.", f"{comp_freq_last:.0f} Hz" if comp_freq_last else "—")
    with col4:
        disc_last = df_pivot["disc_temp"].dropna().iloc[-1] if safe_col(df_pivot, "disc_temp") else None
        kpi_with_status("Temp. tłoczenia", disc_last, "°C", 40, 90)

    st.divider()

    st.markdown("##### ⏱️ Cykle pracy sprężarki")
    if safe_col(df_pivot, "comp_on"):
        fig_timeline = go.Figure()
        fig_timeline.add_trace(go.Scatter(
            x=df_pivot["czas"], y=df_pivot["comp_on"], mode="lines", name="Sprężarka ON/OFF",
            line=dict(color="#4CAF50", width=1), fill="tozeroy", fillcolor="rgba(76,175,80,0.3)",
        ))
        fig_timeline.update_layout(
            yaxis=dict(tickvals=[0, 1], ticktext=["OFF", "ON"], range=[-0.1, 1.2]),
            height=150, margin=dict(t=10, b=30, l=50, r=20), template="plotly_dark",
            xaxis_title="Czas", showlegend=False,
        )
        st.plotly_chart(fig_timeline, width="stretch")
    else:
        st.info("Brak danych o pracy sprężarki.")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### 📊 Histogram długości cykli pracy")
        if len(work_periods) > 2:
            fig_hist = go.Figure()
            fig_hist.add_trace(go.Histogram(x=work_periods.values, nbinsx=15,
                                            marker_color="rgba(33,150,243,0.7)", name="Cykle"))
            fig_hist.add_vline(x=30, line_dash="dash", line_color="rgba(255,152,0,0.8)", annotation_text="Min. 30 min")
            fig_hist.add_vline(x=60, line_dash="dash", line_color="rgba(76,175,80,0.8)", annotation_text="Idealne 60 min")
            fig_hist.update_layout(xaxis_title="Czas cyklu [min]", yaxis_title="Liczba cykli",
                                   height=320, margin=dict(t=20, b=40), template="plotly_dark")
            st.plotly_chart(fig_hist, width="stretch")
        else:
            st.info("Za mało cykli do histogramu.")

    with col_b:
        st.markdown("##### 📈 Częstotliwość sprężarki (modulacja)")
        if safe_col(df_pivot, "comp_freq"):
            fig_freq = go.Figure()
            fig_freq.add_trace(go.Scatter(
                x=df_pivot["czas"], y=df_pivot["comp_freq"], mode="lines", name="Częstotliwość (Hz)",
                line=dict(color="#9C27B0", width=2), fill="tozeroy", fillcolor="rgba(156,39,176,0.08)",
            ))
            fig_freq.update_layout(yaxis_title="Częstotliwość [Hz]", xaxis_title="Czas",
                                   height=320, margin=dict(t=20, b=40), template="plotly_dark")
            st.plotly_chart(fig_freq, width="stretch")
        else:
            st.info("Brak danych częstotliwości sprężarki.")

    st.markdown("##### ⚠️ Alerty taktowania")
    if avg_cycle_min is not None and avg_cycle_min < 30:
        st.error(
            f"🚨 Średni czas cyklu = {avg_cycle_min:.0f} min (minimum 30 min). "
            "Sprężarka taktuje — sprawdź krzywą grzewczą i bufor ciepła."
        )
    elif starts_per_day > 15:
        st.warning(f"⚠️ {starts_per_day:.0f} startów/dobę (próg 15). Rozważ obniżenie nastaw lub zwiększenie histerezy.")
    else:
        st.success(
            f"✅ Praca sprężarki stabilna — śr. cykl {avg_cycle_min:.0f} min, {starts_per_day:.0f} startów/dobę."
            if avg_cycle_min else "✅ Brak alertów taktowania."
        )


# ==============================================================================
# TAB 3: DEFROST I OBIEG CHŁODNICZY
# ==============================================================================
with tab_defr:
    st.subheader("❄️ Odszranianie i Obieg Chłodniczy")

    if safe_col(df_pivot, "defrost_start") and safe_col(df_pivot, "defrost_num"):
        defrost_starts_idx = df_pivot[df_pivot["defrost_start"] == 1].index.tolist()
        defrost_durations = []
        defrost_intervals = []
        for i, start_idx in enumerate(defrost_starts_idx):
            remaining = df_pivot.loc[start_idx:, "defrost_num"]
            end_mask = remaining == 0
            if end_mask.any():
                end_idx = end_mask.idxmax()
                duration_min = (df_pivot.loc[end_idx, "czas"] - df_pivot.loc[start_idx, "czas"]).total_seconds() / 60
                defrost_durations.append(duration_min)
            if i > 0:
                interval_min = (df_pivot.loc[start_idx, "czas"] - df_pivot.loc[defrost_starts_idx[i - 1], "czas"]).total_seconds() / 60
                defrost_intervals.append(interval_min)
        num_defrosts = len(defrost_starts_idx)
        avg_defrost_duration = np.mean(defrost_durations) if defrost_durations else None
        avg_defrost_interval = np.mean(defrost_intervals) if defrost_intervals else None
    else:
        num_defrosts = 0
        avg_defrost_duration = None
        avg_defrost_interval = None
        defrost_durations = []
        defrost_intervals = []
        defrost_starts_idx = []

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Defrosty (okres)", f"{num_defrosts}")
    with col2:
        kpi_with_status("Śr. czas defrostu", avg_defrost_duration, " min", 3.0, 8.0)
    with col3:
        kpi_with_status("Śr. odstęp", avg_defrost_interval, " min", 45, 180)
    with col4:
        eev_last = df_pivot["m_eev"].dropna().iloc[-1] if safe_col(df_pivot, "m_eev") else None
        st.metric("Zawór EEV (m_eev)", f"{eev_last:.0f} kroków" if eev_last else "—")

    st.divider()

    st.markdown("##### ❄️ Cykle defrost w czasie")
    if safe_col(df_pivot, "defrost_num"):
        fig_defrost_tl = go.Figure()
        fig_defrost_tl.add_trace(go.Scatter(
            x=df_pivot["czas"], y=df_pivot["defrost_num"], mode="lines", name="Defrost (aktywny=1)",
            line=dict(color="#00BCD4", width=2), fill="tozeroy", fillcolor="rgba(0,188,212,0.3)",
        ))
        fig_defrost_tl.update_layout(
            yaxis=dict(tickvals=[0, 1], ticktext=["OFF", "DEFROST"], range=[-0.1, 1.3]),
            height=150, margin=dict(t=10, b=30, l=50, r=20), template="plotly_dark",
            xaxis_title="Czas", showlegend=False,
        )
        st.plotly_chart(fig_defrost_tl, width="stretch")
    else:
        st.info("Brak danych o defrostach.")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### 🌡️ Defrost vs Temperatura zewnętrzna")
        if defrost_starts_idx and safe_col(df_pivot, "amb_temp"):
            scatter_data = []
            for i, idx in enumerate(defrost_starts_idx):
                amb = df_pivot.loc[idx, "amb_temp"] if pd.notna(df_pivot.loc[idx, "amb_temp"]) else None
                dur = defrost_durations[i] if i < len(defrost_durations) else None
                if amb is not None and dur is not None:
                    scatter_data.append({"Temp. zewn. (°C)": amb, "Czas trwania (min)": dur})
            if scatter_data:
                sdf = pd.DataFrame(scatter_data)
                fig_def_temp = px.scatter(
                    sdf, x="Temp. zewn. (°C)", y="Czas trwania (min)",
                    color_discrete_sequence=["#00BCD4"], size="Czas trwania (min)", size_max=15,
                )
                fig_def_temp.add_vrect(x0=-3, x1=5, fillcolor="rgba(0,188,212,0.05)", line_width=0,
                                       annotation_text="Strefa typowych defrostów")
                fig_def_temp.update_layout(height=320, margin=dict(t=20, b=40), template="plotly_dark",
                                           xaxis_title="Temperatura zewnętrzna [°C]", yaxis_title="Czas trwania defrostu [min]")
                st.plotly_chart(fig_def_temp, width="stretch")
            else:
                st.info("Brak danych do wykresu scatter defrost/temp.")
        else:
            st.info("Za mało danych defrost.")

    with col_b:
        st.markdown("##### ⏱️ Odstępy między cyklami defrost")
        if defrost_intervals:
            fig_intervals = go.Figure()
            colors = ["#f44336" if v < 45 else "#FF9800" if v < 60 else "#00BCD4" for v in defrost_intervals]
            fig_intervals.add_trace(go.Bar(x=list(range(1, len(defrost_intervals) + 1)),
                                           y=defrost_intervals, marker_color=colors, name="Odstęp (min)"))
            fig_intervals.add_hline(y=45, line_dash="dash", line_color="rgba(255,152,0,0.7)", annotation_text="Min. 45 min")
            fig_intervals.update_layout(xaxis_title="Nr cyklu", yaxis_title="Odstęp [min]",
                                        height=320, margin=dict(t=20, b=40), template="plotly_dark")
            st.plotly_chart(fig_intervals, width="stretch")
        else:
            st.info("Za mało cykli defrost do analizy odstępów.")

    col_c, col_d = st.columns(2)
    with col_c:
        st.markdown("##### 🔧 Zawór rozprężny EEV w czasie")
        if safe_col(df_pivot, "m_eev") or safe_col(df_pivot, "a_eev"):
            fig_eev = go.Figure()
            if safe_col(df_pivot, "m_eev"):
                fig_eev.add_trace(go.Scatter(x=df_pivot["czas"], y=df_pivot["m_eev"], mode="lines",
                                             name="m_eev (główny)", line=dict(color="#FF9800", width=2)))
            if safe_col(df_pivot, "a_eev"):
                fig_eev.add_trace(go.Scatter(x=df_pivot["czas"], y=df_pivot["a_eev"], mode="lines",
                                             name="a_eev (dodatkowy)", line=dict(color="#9C27B0", width=2), yaxis="y2"))
            fig_eev.update_layout(
                yaxis=dict(title="m_eev [kroki]"), yaxis2=dict(title="a_eev [kroki]", overlaying="y", side="right"),
                height=320, margin=dict(t=20, b=40), template="plotly_dark", xaxis_title="Czas",
            )
            st.plotly_chart(fig_eev, width="stretch")
        else:
            st.info("Brak danych EEV.")

    with col_d:
        st.markdown("##### 🌀 Wentylator DC Fan 1")
        if safe_col(df_pivot, "dc_fan1"):
            fig_fan = go.Figure()
            fig_fan.add_trace(go.Scatter(x=df_pivot["czas"], y=df_pivot["dc_fan1"], mode="lines",
                                         name="DC Fan 1 (RPM)", line=dict(color="#4CAF50", width=2),
                                         fill="tozeroy", fillcolor="rgba(76,175,80,0.1)"))
            fig_fan.update_layout(yaxis_title="Obroty [RPM]", xaxis_title="Czas",
                                  height=320, margin=dict(t=20, b=40), template="plotly_dark")
            st.plotly_chart(fig_fan, width="stretch")
        else:
            st.info("Brak danych DC Fan 1.")

    st.markdown("##### ⚠️ Alerty defrost")
    alerts_fired = False
    if defrost_intervals:
        short_intervals = [v for v in defrost_intervals if v < 30]
        if short_intervals:
            st.error(
                f"🚨 Znaleziono {len(short_intervals)} defrostów z odstępem < 30 min. "
                "Możliwy problem: zablokowany parownik, uszkodzony wentylator lub niedobór czynnika."
            )
            alerts_fired = True
    if defrost_durations:
        long_defrosts = [v for v in defrost_durations if v > 10]
        if long_defrosts:
            st.warning(
                f"⚠️ {len(long_defrosts)} defrostów trwało dłużej niż 10 min (norma 3-8 min). "
                "Sprawdź wentylator parownika i poziom czynnika."
            )
            alerts_fired = True
    if not alerts_fired:
        st.success("✅ Cykle defrost w normie — czasy i odstępy prawidłowe (lub brak defrostów).")


# ==============================================================================
# TAB 4: KRZYWA GRZEWCZA
# ==============================================================================
with tab_curve:
    tab_heating_curve.render(
        df_pivot=df_pivot_all,
        weather_df=weather_df_analysis if not weather_df_analysis.empty else None,
    )

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

from app.ui.styles import inject_css, render_about
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
    render_about()

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

    # Tryb w ostatniej próbce — dla niego ΔT jest "aktualny"
    current_mode = df_pivot["Tryb"].iloc[-1] if "Tryb" in df_pivot.columns else "—"
    # Normy ΔT zależą od trybu: CO 3-7°C, CWU 5-10°C
    dt_norm = (3.0, 7.0) if current_mode == "CO" else (5.0, 10.0)
    mode_mask = co_mask if current_mode == "CO" else cwu_mask

    with col1:
        dt_now = (
            df_pivot.loc[mode_mask, "delta_t"].dropna().iloc[-1]
            if mode_mask.any() and safe_col(df_pivot, "delta_t") else None
        )
        kpi_with_status(f"ΔT aktualny ({current_mode})", dt_now, "°C", dt_norm[0], dt_norm[1])
    with col2:
        # ΔT średni w bieżącym trybie za wybrany okres (kontekst do porównania)
        dt_avg = (
            df_pivot.loc[mode_mask, "delta_t"].dropna().mean()
            if mode_mask.any() and safe_col(df_pivot, "delta_t") else None
        )
        kpi_with_status(f"ΔT średni ({current_mode})", dt_avg, "°C", dt_norm[0], dt_norm[1])
    with col3:
        flow_last = df_pivot["flow_m3h"].dropna().iloc[-1] * 1000 / 60 if safe_col(df_pivot, "flow_m3h") else None
        kpi_with_status("Przepływ", flow_last, " l/min", 5.0, 25.0)
    with col4:
        st.metric("Tryb aktualny", current_mode)

    st.divider()

    st.markdown("##### 🌡️ ΔT w czasie — z zakresami prawidłowymi")
    if safe_col(df_pivot, "delta_t"):
        fig_dt = go.Figure()
        fig_dt.add_trace(go.Scatter(
            x=df_pivot["czas"], y=df_pivot["delta_t"], mode="lines", name="ΔT (°C)",
            line=dict(color="#FF9800", width=2), fill="tozeroy", fillcolor="rgba(255,152,0,0.08)",
        ))
        # Strefa normy zgodna z bieżącym trybem (te same progi co kafelek ΔT aktualny)
        fig_dt.add_hrect(y0=dt_norm[0], y1=dt_norm[1], fillcolor="rgba(76,175,80,0.05)", line=dict(width=0),
                         annotation_text=f"Norma {current_mode} ({dt_norm[0]:.0f}-{dt_norm[1]:.0f}°C)",
                         annotation_position="top left")
        dt_max = df_pivot["delta_t"].max()
        fig_dt.update_layout(
            yaxis_title="ΔT [°C]", height=350, margin=dict(t=20, b=40), template="plotly_dark",
            xaxis_title="Czas", yaxis=dict(range=[0, max(15, dt_max * 1.2 if dt_max > 0 else 15)]),
        )
        st.plotly_chart(fig_dt, width="stretch")
        st.caption(
            f"Strefa normy odpowiada aktualnemu trybowi (**{current_mode}**). "
            "CO: 3-7°C, CWU: 5-10°C. Wykres pokazuje ΔT dla obu trybów łącznie."
        )
    else:
        st.info("Brak danych ΔT.")

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("##### 💧 Przepływ i moc generowana w czasie")
        if safe_col(df_pivot, "flow_m3h"):
            flow_lmin = df_pivot["flow_m3h"] * 1000 / 60
            fig_flow = go.Figure()
            fig_flow.add_trace(go.Scatter(
                x=df_pivot["czas"], y=flow_lmin, mode="lines", name="Przepływ (l/min)",
                line=dict(color="#2196F3", width=2), fill="tozeroy", fillcolor="rgba(33,150,243,0.08)",
            ))
            # Moc cieplna generowana (P_th) na drugiej osi Y
            if safe_col(df_pivot, "P_th_kw"):
                fig_flow.add_trace(go.Scatter(
                    x=df_pivot["czas"], y=df_pivot["P_th_kw"], mode="lines",
                    name="Moc generowana (kW)",
                    line=dict(color="#E74C3C", width=2), yaxis="y2",
                ))
            fig_flow.update_layout(
                yaxis=dict(title="Przepływ [l/min]", side="left"),
                yaxis2=dict(title="Moc cieplna [kW]", side="right", overlaying="y", showgrid=False),
                height=320, margin=dict(t=20, b=40), template="plotly_dark", xaxis_title="Czas",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            )
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

    st.caption(
        "Taktowanie oceniamy **tylko dla cykli CO** (ogrzewanie) i **per doba** — "
        "krótkie cykle CWU (grzanie zasobnika) są normalne i nie są taktowaniem."
    )

    # --- Analiza cykli: czas + dominujący tryb każdego cyklu ---
    cycles = []  # (work_period, minuty, tryb, data_lokalna)
    if safe_col(df_pivot, "work_period") and safe_col(df_pivot, "comp_on"):
        on = df_pivot[df_pivot["comp_on"] == 1]
        for wpid, grp in on.groupby("work_period"):
            if wpid == 0:
                continue
            mins = grp["dt_hours"].sum() * 60
            if mins <= 0:
                continue
            tryb = grp["Tryb"].mode().iloc[0] if not grp["Tryb"].mode().empty else "?"
            dzien = grp["czas"].dt.date.mode().iloc[0]
            cycles.append((wpid, mins, tryb, dzien))

    cyc_df = pd.DataFrame(cycles, columns=["wp", "min", "tryb", "dzien"]) if cycles else pd.DataFrame(columns=["wp", "min", "tryb", "dzien"])
    co_cycles = cyc_df[cyc_df["tryb"] == "CO"]
    cwu_cycles = cyc_df[cyc_df["tryb"] == "CWU"]

    # Mediana czasu cyklu CO (odporna na pojedyncze krótkie cykle)
    median_co = co_cycles["min"].median() if not co_cycles.empty else None
    median_cwu = cwu_cycles["min"].median() if not cwu_cycles.empty else None

    # Starty CO per doba — główny wskaźnik taktowania
    starts_co_per_day = co_cycles.groupby("dzien").size() if not co_cycles.empty else pd.Series(dtype=int)
    max_starts_co_day = int(starts_co_per_day.max()) if not starts_co_per_day.empty else 0
    days_taktuje = starts_co_per_day[starts_co_per_day > 15] if not starts_co_per_day.empty else pd.Series(dtype=int)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        # Mediana czasu cyklu CO (nie średnia — odporna na outliery)
        if median_co is not None:
            kpi_with_status("Mediana cyklu CO", median_co, " min", 30, 120)
        else:
            st.metric("Mediana cyklu CO", "—", help="Brak cykli CO w okresie (latem pompa robi CWU).")
    with col2:
        # Max starty CO na dobę — wskaźnik taktowania
        if not starts_co_per_day.empty:
            kpi_with_status("Max starty CO/dobę", float(max_starts_co_day), "", 0, 15, fmt=".0f")
        else:
            st.metric("Max starty CO/dobę", "—")
    with col3:
        comp_freq_last = df_pivot["comp_freq"].dropna().iloc[-1] if safe_col(df_pivot, "comp_freq") else None
        st.metric("Częstotliwość spr.", f"{comp_freq_last:.0f} Hz" if comp_freq_last else "—")
    with col4:
        # Informacyjnie: mediana cyklu CWU (bez oceny — CWU nie taktuje)
        st.metric("Mediana cyklu CWU", f"{median_cwu:.0f} min" if median_cwu is not None else "—",
                  help="Informacyjnie. Krótkie cykle CWU są normalne (grzanie zasobnika do nastawy).")

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
        st.markdown("##### 📊 Histogram długości cykli (CO vs CWU)")
        if not cyc_df.empty and len(cyc_df) > 2:
            fig_hist = go.Figure()
            if not co_cycles.empty:
                fig_hist.add_trace(go.Histogram(x=co_cycles["min"].values, nbinsx=15,
                                                marker_color="rgba(33,150,243,0.7)", name="CO"))
            if not cwu_cycles.empty:
                fig_hist.add_trace(go.Histogram(x=cwu_cycles["min"].values, nbinsx=15,
                                                marker_color="rgba(230,126,34,0.6)", name="CWU"))
            fig_hist.add_vline(x=30, line_dash="dash", line_color="rgba(255,152,0,0.8)", annotation_text="Min. 30 min (CO)")
            fig_hist.update_layout(xaxis_title="Czas cyklu [min]", yaxis_title="Liczba cykli",
                                   height=320, margin=dict(t=20, b=40), template="plotly_dark",
                                   barmode="overlay", legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig_hist, width="stretch")
        else:
            st.info("Za mało cykli do histogramu.")

    with col_b:
        st.markdown("##### 📈 Starty CO na dobę (wskaźnik taktowania)")
        if not starts_co_per_day.empty:
            sdf = starts_co_per_day.reset_index()
            sdf.columns = ["dzien", "starty"]
            colors = ["#f44336" if v > 15 else "#4CAF50" for v in sdf["starty"]]
            fig_starts = go.Figure()
            fig_starts.add_trace(go.Bar(x=sdf["dzien"].astype(str), y=sdf["starty"],
                                        marker_color=colors, name="Starty CO/dobę"))
            fig_starts.add_hline(y=15, line_dash="dash", line_color="rgba(255,152,0,0.8)",
                                 annotation_text="Próg taktowania (15/dobę)")
            fig_starts.update_layout(xaxis_title="Dzień", yaxis_title="Starty CO",
                                     height=320, margin=dict(t=20, b=40), template="plotly_dark")
            st.plotly_chart(fig_starts, width="stretch")
        else:
            st.info("Brak cykli CO w okresie (latem pompa pracuje na CWU — taktowanie nieoceniane).")

    # Częstotliwość sprężarki (modulacja) — pełna szerokość pod spodem
    st.markdown("##### 📈 Częstotliwość sprężarki (modulacja)")
    if safe_col(df_pivot, "comp_freq"):
        fig_freq = go.Figure()
        fig_freq.add_trace(go.Scatter(
            x=df_pivot["czas"], y=df_pivot["comp_freq"], mode="lines", name="Częstotliwość (Hz)",
            line=dict(color="#9C27B0", width=2), fill="tozeroy", fillcolor="rgba(156,39,176,0.08)",
        ))
        fig_freq.update_layout(yaxis_title="Częstotliwość [Hz]", xaxis_title="Czas",
                               height=300, margin=dict(t=20, b=40), template="plotly_dark")
        st.plotly_chart(fig_freq, width="stretch")
    else:
        st.info("Brak danych częstotliwości sprężarki.")

    st.markdown("##### ⚠️ Alerty taktowania")
    if co_cycles.empty:
        st.info(
            "ℹ️ Brak cykli CO w wybranym okresie — taktowanie nieoceniane. "
            "Latem pompa pracuje głównie na CWU (krótkie cykle CWU są normalne)."
        )
    elif not days_taktuje.empty:
        # Konkretne doby z taktowaniem (>15 startów CO/dobę)
        dni_str = ", ".join(f"{d} ({int(n)} startów)" for d, n in days_taktuje.items())
        st.error(
            f"🚨 Taktowanie w dniach: {dni_str} (próg 15 startów CO/dobę). "
            "Sprężarka za często się załącza — sprawdź krzywą grzewczą, bufor ciepła i histerezę."
        )
    elif median_co is not None and median_co < 30:
        st.warning(
            f"⚠️ Mediana cyklu CO = {median_co:.0f} min (zalecane ≥30 min). "
            "Cykle CO krótsze niż optymalne — rozważ korektę krzywej grzewczej lub bufor."
        )
    else:
        st.success(
            f"✅ Praca CO stabilna — mediana cyklu {median_co:.0f} min, "
            f"max {max_starts_co_day} startów CO/dobę (próg 15)."
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

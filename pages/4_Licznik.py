"""Podstrona: Fizyczny Licznik Energii — formularz wpisu + kalibracja."""
import sqlite3
import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta

from app.ui.styles import inject_css, render_about
from app.config import (
    MANUAL_METER_DEV_ID, ENERGY_METER_DEV_ID, HEAT_PUMP_DEV_ID,
    DB_FILE, SERVER_TIMEZONE_OFFSET,
)
from db import save_manual_energy_reading
from app.ui.helpers import cached_energy
from app.core.physics import compute_p_el_w_array
from app.services.database import load_calibration

st.set_page_config(page_title="Licznik Energii", layout="wide", page_icon="⚡")
inject_css()


@st.cache_data(ttl=60)
def load_power_comparison(date_from: str, date_to: str = None) -> pd.DataFrame:
    """Buduje dwie serie mocy [kW] do wykresu porównawczego (TYLKO wizualizacja).

    - Licznik: cur_power (skala ×0.1 W) -> kW. Fizyczny pomiar CAŁEJ pompy.
    - Pompa (model): compute_p_el_w z ac_vol/ac_curr + kalibracja standby/active/
      sensor_factor + hidden_power_w (stały pobór 24/7). Zgodne z silnikiem energii.

    Zakres dat (date_from/date_to 'YYYY-MM-DD') przeliczany na epoch UTC identycznie
    jak w energy._resolve_time_range (data lokalna - offset). Obie serie resamplowane
    do wspólnej siatki 1 min (średnia). Zwraca DataFrame z indeksem czasowym (lokalnym)
    i kolumnami: 'Licznik [kW]', 'Pompa (model) [kW]'. Puste serie tam, gdzie brak danych.
    """
    off = SERVER_TIMEZONE_OFFSET
    offset_sec = off * 3600

    # Konwersja dat -> epoch UTC (jak w silniku: unikamy datetime.timestamp() na Windows)
    dt = datetime.strptime(date_from, "%Y-%m-%d")
    ts_from = int((dt - datetime(1970, 1, 1)).total_seconds()) - offset_sec
    if date_to is None:
        ts_to = int(datetime.now(timezone.utc).timestamp())
    else:
        dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        ts_to = int((dt_to - datetime(1970, 1, 1)).total_seconds()) - offset_sec

    try:
        conn = sqlite3.connect(DB_FILE)
        meter = pd.read_sql_query(
            """SELECT timestamp, val_num FROM telemetry
               WHERE device_id = ? AND code = 'cur_power'
                 AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp""",
            conn, params=(ENERGY_METER_DEV_ID, ts_from, ts_to),
        )
        pump = pd.read_sql_query(
            """SELECT timestamp, code, val_num FROM telemetry
               WHERE device_id = ? AND code IN ('ac_vol','ac_curr')
                 AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp""",
            conn, params=(HEAT_PUMP_DEV_ID, ts_from, ts_to),
        )
        conn.close()
    except Exception:
        return pd.DataFrame()

    frames = []

    # --- Seria licznika ---
    if not meter.empty:
        m = meter.copy()
        m["czas"] = pd.to_datetime(m["timestamp"] + offset_sec, unit="s")
        # cur_power ×0.1 W -> kW. Surowe punkty (bez resample) — wykres schodkowy ZOH.
        m = m.set_index("czas")["val_num"].astype(float) / 10.0 / 1000.0
        m.name = "Licznik [kW]"
        frames.append(m)

    # --- Seria pompy (model z kalibracją) ---
    # Uwaga: model pompy oblicza P_el Z KALIBRACJĄ:
    #   - sensor_factor: korekcja proporcjonalna czujnika prądu (sprężarka)
    #   - standby_power_w: stały pobór w postojach (pompa obiegowa, elektronika)
    #   - active_power_w: dodatek podczas pracy (pompa obiegowa + wentylator)
    #   - hidden_power_w: stały pobór 24/7 niewidoczny w czujniku
    # Licznik (cur_power) mierzy CAŁĄ pompę — to jest prawda fizyczna.
    # Różnica między serią "Licznik" a "Pompa (model)" to obwód pomocniczy
    # (pompa obiegowa + wentylator + elektronika).
    if not pump.empty:
        piv = pump.pivot_table(index="timestamp", columns="code",
                               values="val_num", aggfunc="first").sort_index().ffill()
        if "ac_vol" in piv.columns and "ac_curr" in piv.columns:
            cal = load_calibration()
            p_el_w = compute_p_el_w_array(
                ac_vol=piv["ac_vol"].fillna(0).to_numpy(),
                ac_curr_raw=piv["ac_curr"].fillna(0).to_numpy(),
                cos_phi=cal["cos_phi"],
                standby_power_w=cal["standby_power_w"],
                active_power_w=cal["active_power_w"],
                sensor_factor=cal["sensor_factor"],
            )
            # hidden_power_w: stały pobór 24/7 niewidoczny w czujniku (jak w compute_energy)
            p_el_w = p_el_w + cal["hidden_power_w"]
            piv = piv.reset_index()
            piv["czas"] = pd.to_datetime(piv["timestamp"] + offset_sec, unit="s")
            s = pd.Series(p_el_w / 1000.0, index=piv["czas"])
            s.name = "Pompa (model) [kW]"
            frames.append(s)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(
        [f[~f.index.duplicated(keep="last")] for f in frames],
        axis=1, sort=True,
    ).sort_index()
    # ZOH: każda seria trzyma ostatnią zaraportowaną wartość aż do kolejnego punktu.
    df = df.ffill()
    return df


with st.sidebar:
    st.markdown("### ⚙️ Ustawienia")
    _selected_range = st.selectbox("Zakres SCOP:", [
        "Dzisiaj", "3 dni", "7 dni", "30 dni", "90 dni",
    ], index=0)
    if st.button("🔄 Odśwież dane"):
        st.cache_data.clear()
        st.rerun()
    render_about()

# --- Obliczenie dat (jak Panel/Bilans) ---
_now = datetime.now()
_range_days_map = {"Dzisiaj": 0, "3 dni": 3, "7 dni": 7, "30 dni": 30, "90 dni": 90}
_days_back = _range_days_map[_selected_range]
if _days_back == 0:
    _date_from = _now.strftime("%Y-%m-%d")
else:
    _date_from = (_now - timedelta(days=_days_back)).strftime("%Y-%m-%d")

st.markdown('<h3 style="margin:0;padding:0.2rem 0;">⚡ Fizyczny Licznik Energii</h3>', unsafe_allow_html=True)
st.caption(f"Odczyty rejestrowane pod ID: `{MANUAL_METER_DEV_ID}`")


# --- Wykres porównawczy mocy: licznik vs model pompy ---
st.subheader("📈 Moc: licznik energii vs pobór pompy")
st.caption(
    f"Licznik `{ENERGY_METER_DEV_ID}` (pomiar) vs model z telemetrii pompy "
    f"`{HEAT_PUMP_DEV_ID}` (kalibracja). Zakres z panelu bocznego · surowe próbki, "
    f"wykres schodkowy (wartość trzyma się do kolejnego raportu)."
)

df_power = load_power_comparison(date_from=_date_from, date_to=None)

if df_power.empty:
    st.info("Brak danych mocy w wybranym zakresie.")
else:
    fig = go.Figure()
    if "Licznik [kW]" in df_power.columns and df_power["Licznik [kW]"].notna().any():
        fig.add_trace(go.Scatter(
            x=df_power.index, y=df_power["Licznik [kW]"],
            name="Licznik [kW]", mode="lines",
            line=dict(color="#e63946", width=2, shape="hv"),
            connectgaps=True,
        ))
    if "Pompa (model) [kW]" in df_power.columns and df_power["Pompa (model) [kW]"].notna().any():
        fig.add_trace(go.Scatter(
            x=df_power.index, y=df_power["Pompa (model) [kW]"],
            name="Pompa (model) [kW]", mode="lines",
            line=dict(color="#1d3557", width=2, shape="hv"),
            connectgaps=True,
        ))
    fig.update_layout(
        height=380,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title=None, yaxis_title="Moc [kW]",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

    # Krótkie podsumowanie liczbowe (średnie w oknie, tam gdzie są dane)
    cols = st.columns(2)
    if "Licznik [kW]" in df_power.columns:
        avg_m = df_power["Licznik [kW]"].mean()
        cols[0].metric("Śr. moc — licznik", f"{avg_m*1000:.0f} W" if pd.notna(avg_m) else "—")
    if "Pompa (model) [kW]" in df_power.columns:
        avg_p = df_power["Pompa (model) [kW]"].mean()
        cols[1].metric("Śr. moc — pompa (model)", f"{avg_p*1000:.0f} W" if pd.notna(avg_p) else "—")

st.markdown("---")


# --- Formularz dodawania ---
st.subheader("➕ Dodaj odczyt licznika")

with st.form("form_add_meter", clear_on_submit=True):
    now_dt = datetime.now()
    col_d, col_t = st.columns(2)
    with col_d:
        add_date = st.date_input("Data odczytu", value=now_dt.date())
    with col_t:
        add_time = st.time_input("Godzina", value=now_dt.time())

    add_val_str = st.text_input(
        "Stan licznika [kWh]",
        placeholder="np. 12450.5",
    )

    btn_add = st.form_submit_button("💾 Zapisz")

    if btn_add:
        clean = add_val_str.strip().replace(",", ".")
        if not clean:
            st.error("Pole stanu licznika nie może być puste!")
        else:
            try:
                val_float = float(clean)
                # Uzytkownik wpisuje czas LOKALNY; zamieniamy na epoch UTC.
                # lokalny = UTC + offset  =>  epoch_utc = combine(local) - offset*3600
                local_dt = datetime.combine(add_date, add_time, tzinfo=timezone.utc)
                ts_val = int(local_dt.timestamp()) - SERVER_TIMEZONE_OFFSET * 3600
                success, msg = save_manual_energy_reading(val_float, ts_val)
                if success:
                    st.success(msg)
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(msg)
            except ValueError:
                st.error("Niepoprawna wartość liczbowa.")


# --- Historia odczytów ---
st.markdown("---")
st.subheader("📋 Historia odczytów")

try:
    import sqlite3
    from app.config import DB_FILE, SERVER_TIMEZONE_OFFSET
    conn = sqlite3.connect(DB_FILE)
    off = SERVER_TIMEZONE_OFFSET
    df = pd.read_sql_query(f"""
        SELECT id, timestamp,
               datetime(timestamp, 'unixepoch', '{off:+d} hours') as czas,
               val_num as stan_kwh
        FROM telemetry
        WHERE device_id = ? AND code = 'energy_kwh'
        ORDER BY timestamp DESC
    """, conn, params=(MANUAL_METER_DEV_ID,))
    conn.close()

    if df.empty:
        st.info("Brak odczytów. Wprowadź pierwszy odczyt powyżej.")
    else:
        # Oblicz zużycie między odczytami
        df_sorted = df.sort_values("timestamp").reset_index(drop=True)
        df_sorted["Zużycie [kWh]"] = df_sorted["stan_kwh"].diff()
        df_sorted["Okres [h]"] = df_sorted["timestamp"].diff() / 3600.0

        display = df_sorted[["czas", "stan_kwh", "Zużycie [kWh]", "Okres [h]"]].copy()
        display.columns = ["Data i godzina", "Stan [kWh]", "Zużycie [kWh]", "Okres [h]"]
        display["Stan [kWh]"] = display["Stan [kWh]"].round(2)
        display["Zużycie [kWh]"] = display["Zużycie [kWh]"].round(2)
        display["Okres [h]"] = display["Okres [h]"].round(1)

        st.dataframe(
            display.sort_values("Data i godzina", ascending=False),
            hide_index=True,
            width="stretch",
        )

except Exception as e:
    st.error(f"Błąd ładowania historii: {e}")

"""Podstrona: Fizyczny Licznik Energii — formularz wpisu + kalibracja."""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone, timedelta

from app.ui.styles import inject_css, render_about
from app.config import MANUAL_METER_DEV_ID, DEFAULT_TIME_OFFSET_HOURS
from db import save_manual_energy_reading
from app.ui.helpers import cached_energy

st.set_page_config(page_title="Licznik Energii", layout="wide", page_icon="⚡")
inject_css()

with st.sidebar:
    render_about()

st.markdown('<h3 style="margin:0;padding:0.2rem 0;">⚡ Fizyczny Licznik Energii</h3>', unsafe_allow_html=True)
st.caption(f"Odczyty rejestrowane pod ID: `{MANUAL_METER_DEV_ID}`")


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
                ts_val = int(local_dt.timestamp()) - DEFAULT_TIME_OFFSET_HOURS * 3600
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
    from app.config import DB_FILE, DEFAULT_TIME_OFFSET_HOURS
    conn = sqlite3.connect(DB_FILE)
    off = DEFAULT_TIME_OFFSET_HOURS
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

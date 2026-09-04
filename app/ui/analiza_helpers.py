"""Helper dla strony Analiza — buduje pivot 'v1-compatible' z surowych danych v2.

v1 miał gotowy df_pivot z process_telemetry() (z resample). v2 liczy energię
z surowych danych, ale strona Analiza potrzebuje pivotu z policzonymi kolumnami
(COP, delta_t, Tryb, comp_on, cykle) do WIZUALIZACJI i DIAGNOSTYKI.

WAŻNE:
- Ten pivot służy TYLKO do wykresów i diagnostyki (COP chwilowy, ΔT, cykle).
- NIE liczy energii/SCOP — od tego jest compute_energy().
- Zgodnie z zasadą v2: resample/pivot tylko do wizualizacji, energia zawsze z surowych.
- COP/p_el obliczane SUROWO (bez sensor_factor/standby/active/hidden) — to jest
  "COP surowy" (tylko sprężarka), nie "SCOP z kalibracją". Różnica: pompa obiegowa,
  wentylator, elektronika są niewidoczne w czujniku prądu, ale widoczne w liczniku.
  SCOP (z compute_energy) to poprawiony model CAŁEJ pompy.
"""
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st

from app.config import (
    DB_FILE, HEAT_PUMP_DEV_ID, SERVER_TIMEZONE_OFFSET,
    COMP_FREQ_ON_THRESHOLD, CWU_VALVE_THRESHOLD, TEMP_CODES,
    DEFAULT_COS_PHI,
)

# Kody potrzebne stronie Analiza (więcej niż ENERGY_CODES — diagnostyka mechaniczna)
ANALIZA_CODES: tuple[str, ...] = (
    "ac_vol", "ac_curr", "comp_freq", "flow_rate",
    "out_water_temp", "in_water_temp", "amb_temp",
    "valve", "defrost", "heat_temp_set", "idr_temp_set",
    "disc_temp", "back_temp", "m_eev", "a_eev", "dc_fan1", "zone_select",
)

BOOL_MAP = {"True": 1.0, "true": 1.0, "1": 1.0, "1.0": 1.0,
            "False": 0.0, "false": 0.0, "0": 0.0, "0.0": 0.0}


@st.cache_data(ttl=60)
def load_analiza_pivot(
    hours_back: int,
    all_time: bool = False,
    db_file: str = DB_FILE,
    device_id: str = HEAT_PUMP_DEV_ID,
    time_offset_hours: int = SERVER_TIMEZONE_OFFSET,
    cos_phi: float = DEFAULT_COS_PHI,
) -> pd.DataFrame:
    """Ładuje surowe dane i buduje pivot v1-compatible do wizualizacji/diagnostyki.

    Args:
        hours_back: Ile godzin wstecz (ignorowane gdy all_time=True).
        all_time: True = całe dane.
        cos_phi: do COP chwilowego (P_th/P_el).

    Returns:
        DataFrame z kolumnami: czas, amb_temp, comp_freq, out_water_temp,
        in_water_temp, delta_t, flow_m3h, COP, Tryb, heat_temp_set, disc_temp,
        back_temp, m_eev, a_eev, dc_fan1, zone_select, comp_on, work_period,
        defrost_num, defrost_start, dt_hours.
        Pusty DataFrame gdy brak danych.
    """
    try:
        conn = sqlite3.connect(db_file)
        codes_str = ",".join(f"'{c}'" for c in ANALIZA_CODES)
        if all_time:
            where_time = ""
            params: tuple = (device_id,)
        else:
            where_time = "AND timestamp >= strftime('%s','now', ?)"
            params = (device_id, f"-{int(hours_back)} hours")

        query = f"""
            SELECT timestamp, code, val_num, val_str
            FROM telemetry
            WHERE device_id = ? AND code IN ({codes_str}) {where_time}
            ORDER BY timestamp
        """
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    # valve/defrost/zone_select bywają jako val_str ("True"/"False") — konwersja na val_num
    str_mask = df["val_num"].isna() & df["val_str"].notna()
    if str_mask.any():
        df.loc[str_mask, "val_num"] = df.loc[str_mask, "val_str"].map(
            lambda s: BOOL_MAP.get(str(s).strip(), np.nan)
        )

    # Pivot long -> wide
    piv = df.pivot_table(index="timestamp", columns="code", values="val_num", aggfunc="first")
    piv = piv.sort_index().ffill()

    # Upewnij się że wszystkie kolumny istnieją
    for c in ANALIZA_CODES:
        if c not in piv.columns:
            piv[c] = np.nan

    # Czas lokalny
    piv = piv.reset_index()
    piv["czas"] = pd.to_datetime(piv["timestamp"] + time_offset_hours * 3600, unit="s")

    # Skale: flow_rate ×0.1 -> m3/h. Temperatury JUŻ /10 przez collector (nie dzielić).
    piv["flow_m3h"] = piv["flow_rate"] / 10.0 if "flow_rate" in piv.columns else np.nan

    # Historyczne nastawy >100 = niedzielone (heat_temp_set/idr_temp_set)
    for c in ("heat_temp_set", "idr_temp_set"):
        if c in piv.columns:
            piv[c] = piv[c].where(piv[c].isna() | (piv[c] <= 100), piv[c] / 10.0)

    # ΔT (zasilanie - powrót)
    piv["delta_t"] = piv["out_water_temp"] - piv["in_water_temp"]

    # Moce chwilowe -> COP chwilowy (tylko do wizualizacji)
    curr_a = piv["ac_curr"].fillna(0) / 10.0          # ×0.1 A
    p_el_w = piv["ac_vol"].fillna(0) * curr_a * cos_phi
    # P_th = flow[m3/h] * 4186 * dT / 3600 [W]  (cp wody 4186 J/kgK, 1 m3=1000 kg)
    p_th_w = piv["flow_m3h"].fillna(0) * 1000.0 * 4.186 * piv["delta_t"] / 3.6
    piv["COP"] = np.where((p_el_w > 100) & (p_th_w > 0), p_th_w / p_el_w, np.nan)
    # Moce w kW (do wykresów). P_th ujemne podczas defrostu (ΔT<0) — fizycznie poprawne.
    piv["P_th_kw"] = p_th_w / 1000.0
    piv["P_el_kw"] = p_el_w / 1000.0

    # Tryb: CWU gdy valve >= próg, inaczej CO
    valve = piv["valve"].fillna(0)
    piv["Tryb"] = np.where(valve >= CWU_VALVE_THRESHOLD, "CWU", "CO")

    # Sprężarka ON/OFF + numeracja cykli pracy (work_period)
    piv["comp_on"] = (piv["comp_freq"].fillna(0) > COMP_FREQ_ON_THRESHOLD).astype(int)
    # nowy cykl = przejście 0->1
    starts = (piv["comp_on"] == 1) & (piv["comp_on"].shift(1, fill_value=0) == 0)
    piv["work_period"] = starts.cumsum()

    # Defrost: flaga + numeracja + start
    piv["defrost_num"] = (piv["defrost"].fillna(0) >= 0.5).astype(int)
    piv["defrost_start"] = (
        (piv["defrost_num"] == 1) & (piv["defrost_num"].shift(1, fill_value=0) == 0)
    ).astype(int)

    # dt_hours per interwał (do sumowania czasu pracy)
    piv["dt_hours"] = piv["timestamp"].diff().fillna(0) / 3600.0
    # ucinaj przerwy (gap) — jak w silniku (>360s nie liczymy jako ciągłość)
    piv.loc[piv["dt_hours"] > 360 / 3600.0, "dt_hours"] = 0.0

    return piv

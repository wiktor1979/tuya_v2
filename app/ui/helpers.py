"""Helpery UI — cache, ładowanie statusu na żywo, formatowanie."""
import sqlite3
from typing import Optional

import pandas as pd
import streamlit as st

from app.config import (
    DB_FILE, ENERGY_CODES, HEAT_PUMP_DEV_ID,
    DEFAULT_COS_PHI, DEFAULT_STANDBY_POWER_W, DEFAULT_ACTIVE_POWER_W,
    DEFAULT_HIDDEN_POWER_W, DEFAULT_SENSOR_FACTOR, DEFAULT_TIME_OFFSET_HOURS,
)
from app.core.energy import compute_energy
from app.core.models import EnergyResult


@st.cache_data(ttl=60)
def cached_energy(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    mode: str = "total",
    daily_breakdown: bool = False,
    time_offset_hours: int = DEFAULT_TIME_OFFSET_HOURS,
    cos_phi: float = DEFAULT_COS_PHI,
    standby_power_w: float = DEFAULT_STANDBY_POWER_W,
    active_power_w: float = DEFAULT_ACTIVE_POWER_W,
    hidden_power_w: float = DEFAULT_HIDDEN_POWER_W,
    sensor_factor: float = DEFAULT_SENSOR_FACTOR,
) -> EnergyResult:
    """Wrapper z cache na compute_energy(). Używany przez wszystkie strony UI.

    TTL=60s — obliczenie odpala się raz na minutę, potem instant.
    """
    return compute_energy(
        date_from=date_from,
        date_to=date_to,
        mode=mode,
        daily_breakdown=daily_breakdown,
        time_offset_hours=time_offset_hours,
        cos_phi=cos_phi,
        standby_power_w=standby_power_w,
        active_power_w=active_power_w,
        hidden_power_w=hidden_power_w,
        sensor_factor=sensor_factor,
    )


def load_latest_status(db_file: str = DB_FILE, device_id: str = HEAT_PUMP_DEV_ID) -> dict:
    """Pobiera ostatni znany stan każdego parametru pompy.

    Returns:
        Dict code -> {"val_num": float, "val_str": str, "timestamp": int}.
        Puste jeśli brak danych.
    """
    try:
        conn = sqlite3.connect(db_file)
        query = """
            SELECT code, val_num, val_str, MAX(timestamp) as timestamp
            FROM telemetry
            WHERE device_id = ?
            GROUP BY code
        """
        df = pd.read_sql_query(query, conn, params=(device_id,))
        conn.close()

        result = {}
        for _, row in df.iterrows():
            result[row["code"]] = {
                "val_num": row["val_num"],
                "val_str": row["val_str"],
                "timestamp": row["timestamp"],
            }
        return result
    except Exception:
        return {}


def get_pump_status(status: dict) -> tuple[str, str, str]:
    """Określa status pompy na podstawie ostatnich wartości.

    Returns:
        (label, color, emoji) — np. ("CO — Grzeje", "#2196F3", "🔥")
    """
    comp_freq = status.get("comp_freq", {}).get("val_num", 0) or 0
    valve = status.get("valve", {}).get("val_num", 0) or 0
    defrost = status.get("defrost", {}).get("val_num", 0) or 0
    fault = status.get("fault", {}).get("val_num", 0) or 0

    if fault and fault > 0:
        return "AWARIA", "#e94560", "🚨"
    if defrost and defrost >= 0.5:
        return "Defrost", "#00BCD4", "❄️"
    if comp_freq > 5:
        if valve >= 0.5:
            return "CWU — Podgrzewa wodę", "#E67E22", "🚿"
        else:
            return "CO — Grzeje", "#2196F3", "🔥"
    return "Postój", "#555555", "⏸"


def get_temp_value(status: dict, code: str) -> Optional[float]:
    """Pobiera temperaturę z ostatniego statusu. Zwraca None jeśli brak."""
    entry = status.get(code)
    if entry and entry["val_num"] is not None:
        val = entry["val_num"]
        # Korekcja historycznych danych (surowe > 100 = niedzielone)
        if val > 100:
            val = val / 10.0
        return val
    return None


def format_temp(val: Optional[float], unit: str = "°C") -> str:
    """Formatuje temperaturę. 'N/A' jeśli None."""
    if val is None:
        return "N/A"
    return f"{val:.1f} {unit}"

"""Helpery UI — cache, ładowanie statusu na żywo, formatowanie."""
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import streamlit as st

from app.config import (
    DB_FILE, ENERGY_CODES, HEAT_PUMP_DEV_ID, ENERGY_METER_DEV_ID,
    DEFAULT_COS_PHI, DEFAULT_STANDBY_POWER_W, DEFAULT_ACTIVE_POWER_W,
    DEFAULT_HIDDEN_POWER_W, DEFAULT_SENSOR_FACTOR, SERVER_TIMEZONE_OFFSET,
)
from app.core.energy import compute_energy
from app.core.models import EnergyResult
from app.services.database import load_calibration


@st.cache_data(ttl=60)
def cached_energy(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    mode: str = "total",
    daily_breakdown: bool = False,
    time_offset_hours: int = SERVER_TIMEZONE_OFFSET,
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


@st.cache_data(ttl=60)
def cached_meter_energy(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    time_offset_hours: int = SERVER_TIMEZONE_OFFSET,
    db_file: str = DB_FILE,
) -> float:
    """Energia pobrana wg fizycznego licznika [kWh] w zadanym zakresie.

    Źródłem jest add_ele — przyrost energii raportowany przez licznik Tuya.
    Skala potwierdzona empirycznie (2026-09-04): 1 jednostka = 1 Wh (×0.001 kWh).
    Zużycie = suma przyrostów w oknie. add_ele całkuje sam licznik, więc jest
    odporne na dziury w telemetrii (w przeciwieństwie do ZOH z cur_power).

    Bez deduplikacji — collector deduplikuje add_ele przy zapisie, a baza jest
    już wyczyszczona z historycznych par (patrz decyzje projektowe 2026-09-04).
    Zwraca 0.0 przy braku danych.
    """
    offset_sec = time_offset_hours * 3600

    # Data lokalna -> epoch UTC (spójnie z energy._resolve_time_range: local - offset).
    if date_from is None:
        ts_from = 0
    else:
        dt = datetime.strptime(date_from, "%Y-%m-%d")
        ts_from = int((dt - datetime(1970, 1, 1)).total_seconds()) - offset_sec
    if date_to is None:
        ts_to = int(datetime.now(timezone.utc).timestamp())
    else:
        dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        ts_to = int((dt_to - datetime(1970, 1, 1)).total_seconds()) - offset_sec

    try:
        conn = sqlite3.connect(db_file)
        df = pd.read_sql_query(
            """SELECT val_num FROM telemetry
               WHERE device_id = ? AND code = 'add_ele'
                 AND timestamp >= ? AND timestamp <= ?""",
            conn, params=(ENERGY_METER_DEV_ID, ts_from, ts_to),
        )
        conn.close()
    except Exception:
        return 0.0

    if df.empty:
        return 0.0

    # add_ele w Wh (×0.001 kWh). Suma przyrostów = zużycie w oknie.
    wh = float(df["val_num"].fillna(0).sum())
    return wh / 1000.0  # Wh -> kWh


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


def _flag_value(status: dict, code: str) -> float:
    """Zwraca wartość flagi binarnej jako float (0/1).

    Flagi binarne (valve, defrost, fault_flag...) sĄ zapisywane jako val_str
    ("True"/"False"), a NIE val_num (który jest wtedy None). Ta funkcja czyta
    val_str z konwersją, z fallbackiem na val_num dla danych liczbowych.
    """
    entry = status.get(code)
    if not entry:
        return 0.0
    vs = entry.get("val_str")
    # val_str bywa pandas nan (float) zamiast None — traktuj jak brak
    is_missing = vs is None or (isinstance(vs, float) and vs != vs)
    if not is_missing:
        s = str(vs).strip().lower()
        if s in ("true", "1", "1.0", "on"):
            return 1.0
        if s in ("false", "0", "0.0", "off", "nan"):
            return 0.0
        # val_str numeryczny (np. zone_select)
        try:
            f = float(vs)
            return 0.0 if f != f else f  # nan -> 0
        except (ValueError, TypeError):
            return 0.0
    vn = entry.get("val_num")
    if vn is None or (isinstance(vn, float) and vn != vn):  # None lub nan
        return 0.0
    return float(vn)


def get_pump_status(status: dict) -> tuple[str, str, str]:
    """Określa status pompy na podstawie ostatnich wartości.

    Returns:
        (label, color, emoji) — np. ("CO — Grzeje", "#2196F3", "🔥")
    """
    comp_freq = status.get("comp_freq", {}).get("val_num", 0) or 0
    # valve/defrost/fault sĄ zapisywane jako val_str ("True"/"False"), nie val_num!
    valve = _flag_value(status, "valve")
    defrost = _flag_value(status, "defrost")
    fault = _flag_value(status, "fault_flag") or _flag_value(status, "fault")

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

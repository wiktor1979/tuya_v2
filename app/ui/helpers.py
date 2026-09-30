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
    FLOW_RATE_ON_THRESHOLD, COMP_FREQ_ON_THRESHOLD, CWU_TANK_DIFF_ON, COMBINED_WORK_MODE,
    DHW_WORK_MODES, get_pump, list_pumps, DEFAULT_PUMP_ID,
)
from app.core.energy import compute_energy
from app.core.models import EnergyResult
from app.core.physics import is_pump_running
from app.services.database import load_calibration


# Klucze pamięci wyboru pompy.
# - query_params (?pump=): przeżywa odświeżenie (F5) i współdzielenie linku.
# - session_state: przeżywa nawigację między stronami w obrębie sesji.
# - localStorage (przeglądarka): przeżywa ZAMKNIĘCIE przeglądarki, PER URZĄDZENIE.
#   Wybór jest lokalny dla każdej przeglądarki — dwie osoby na różnych sprzętach
#   mają NIEZALEŻNE ustawienie (świadomie NIE trzymamy tego globalnie w bazie).
_SS_PUMP_KEY = "_selected_pump_id"
_LS_PUMP_KEY = "tuya_selected_pump"


def _get_local_storage():
    """Zwraca instancję LocalStorage (leniwie, cache w session_state).

    Import lokalny — rdzeń/testy nie wymagają pakietu streamlit-local-storage.
    Zwraca None, jeśli komponent niedostępny (np. tryb bez UI).
    """
    if "_local_storage" in st.session_state:
        return st.session_state["_local_storage"]
    try:
        from streamlit_local_storage import LocalStorage
        ls = LocalStorage()
    except Exception:
        ls = None
    st.session_state["_local_storage"] = ls
    return ls


def _valid_pump_id(pump_id: Optional[str]) -> Optional[str]:
    """Zwraca pump_id, jeśli istnieje na liście pomp; inaczej None."""
    if not pump_id:
        return None
    ids = {p["id"] for p in list_pumps()}
    return pump_id if pump_id in ids else None


def persist_pump_choice(pump_id: str) -> None:
    """Zapisuje wybór pompy do WSZYSTKICH trzech warstw pamięci.

    Kolejność zapisu: session_state, query_params (URL), localStorage (przeglądarka).
    Dzięki temu wybór przeżywa: nawigację, odświeżenie oraz zamknięcie przeglądarki
    (per urządzenie).
    """
    st.session_state[_SS_PUMP_KEY] = pump_id
    if st.query_params.get("pump") != pump_id:
        st.query_params["pump"] = pump_id
    ls = _get_local_storage()
    if ls is not None:
        try:
            if ls.getItem(_LS_PUMP_KEY) != pump_id:
                ls.setItem(_LS_PUMP_KEY, pump_id, key="ls_set_pump")
        except Exception:
            pass


def get_selected_pump() -> dict:
    """Zwraca wybraną pompę z trwałą pamięcią wyboru.

    Kolejność odczytu (pierwsze niepuste wygrywa):
        1. query_params (?pump=)      — F5, współdzielenie linku
        2. session_state              — nawigacja między stronami
        3. localStorage (przeglądarka) — przetrwanie zamknięcia przeglądarki (per urządzenie)
        4. DEFAULT_PUMP_ID            — fallback

    Po ustaleniu wyboru synchronizuje go do session_state i URL, aby kolejne
    strony (które mogą zgubić query param przy nawigacji) miały skąd go odczytać.

    Returns:
        Dict pompy: {id, name, device_id, meter_id}.
    """
    # 1. URL
    try:
        pump_id = _valid_pump_id(st.query_params.get("pump"))
    except Exception:
        pump_id = None

    # 2. session_state
    if pump_id is None:
        pump_id = _valid_pump_id(st.session_state.get(_SS_PUMP_KEY))

    # 3. localStorage (przeglądarka)
    if pump_id is None:
        ls = _get_local_storage()
        if ls is not None:
            try:
                pump_id = _valid_pump_id(ls.getItem(_LS_PUMP_KEY))
            except Exception:
                pump_id = None

    # 4. fallback
    if pump_id is None:
        pump_id = DEFAULT_PUMP_ID

    # Synchronizacja wstecz: session_state + URL (nie zapisujemy do localStorage
    # tutaj — zapis robi persist_pump_choice() przy realnym wyborze użytkownika,
    # żeby nie nadpisywać wartości, zanim komponent LS zdąży ją wczytać).
    st.session_state[_SS_PUMP_KEY] = pump_id
    try:
        if st.query_params.get("pump") != pump_id:
            st.query_params["pump"] = pump_id
    except Exception:
        pass

    return get_pump(pump_id)


def render_pump_selector() -> dict:
    """Renderuje selectbox wyboru pompy w sidebarze i zwraca wybraną pompę.

    Wspólny komponent dla wszystkich stron — jedno miejsce z logiką trwałości
    (URL + session_state + localStorage). Selectbox pokazuje się tylko gdy
    skonfigurowano więcej niż jedną pompę.

    Returns:
        Dict wybranej pompy: {id, name, device_id, meter_id}.
    """
    pumps = list_pumps()
    pump_ids = [p["id"] for p in pumps]
    pump_names = {p["id"]: p["name"] for p in pumps}
    current = get_selected_pump()
    idx = pump_ids.index(current["id"]) if current["id"] in pump_ids else 0

    if len(pumps) > 1:
        sel_id = st.selectbox(
            "Pompa:", pump_ids, index=idx,
            format_func=lambda pid: pump_names.get(pid, pid),
            key="pump_select",
        )
        if sel_id != current["id"]:
            persist_pump_choice(sel_id)
            st.rerun()
        return get_pump(sel_id)

    return current


def weather_daily_for_range(
    date_from: Optional[str],
    date_to: Optional[str],
    time_offset_hours: int,
) -> dict:
    """Średnie dobowe temperatury (pogoda) dla zakresu — do liczenia HDD w compute_energy().

    Konwersja dat na epoch UTC wg konwencji projektu (bez datetime.timestamp() —
    problemy na Windows): (dt - 1970-01-01).total_seconds() minus offset.
    czas lokalny = UTC + offset. date_from/date_to są EKSKLUZYWNE (jak w silniku).
    Puste (brak dat all-time lub brak danych) → {} → HDD spadnie na fallback amb_temp.
    """
    from datetime import datetime as _dt
    from app.services.database import get_weather_daily_avg

    if date_from is None:
        return {}
    offset_sec = time_offset_hours * 3600
    ts_from = int((_dt.strptime(date_from, "%Y-%m-%d") - _dt(1970, 1, 1)).total_seconds()) - offset_sec
    if date_to is None:
        ts_to = int(datetime.now(timezone.utc).timestamp())
    else:
        ts_to = int((_dt.strptime(date_to, "%Y-%m-%d") - _dt(1970, 1, 1)).total_seconds()) - offset_sec
    try:
        return get_weather_daily_avg(ts_from, ts_to, time_offset_hours)
    except Exception:
        return {}


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
    device_id: str = HEAT_PUMP_DEV_ID,
) -> EnergyResult:
    """Wrapper z cache na compute_energy(). Używany przez wszystkie strony UI.

    TTL=60s — obliczenie odpala się raz na minutę, potem instant.

    HDD liczony z danych pogodowych (weather_daily) — wspólne źródło dla obu pomp,
    porównywalny między pompami (czujnik amb_temp jednostki ma offset montażowy).
    """
    weather_daily = weather_daily_for_range(date_from, date_to, time_offset_hours)
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
        device_id=device_id,
        weather_daily=weather_daily,
    )


@st.cache_data(ttl=60)
def cached_meter_energy(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    time_offset_hours: int = SERVER_TIMEZONE_OFFSET,
    db_file: str = DB_FILE,
    meter_id: Optional[str] = ENERGY_METER_DEV_ID,
) -> float:
    """Energia pobrana wg fizycznego licznika [kWh] w zadanym zakresie.

    Źródłem jest add_ele — przyrost energii raportowany przez licznik Tuya.
    Skala potwierdzona empirycznie (2026-09-04): 1 jednostka = 1 Wh (×0.001 kWh).
    Zużycie = suma przyrostów w oknie. add_ele całkuje sam licznik, więc jest
    odporne na dziury w telemetrii (w przeciwieństwie do ZOH z cur_power).

    Bez deduplikacji — collector deduplikuje add_ele przy zapisie, a baza jest
    już wyczyszczona z historycznych par (patrz decyzje projektowe 2026-09-04).

    Args:
        meter_id: device_id licznika dla wybranej pompy. None = pompa bez licznika
            → zwraca 0.0 (wywołujący powinien odróżnić "brak licznika" od "0 kWh").

    Zwraca 0.0 przy braku licznika lub braku danych.
    """
    if meter_id is None:
        return 0.0

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
            conn, params=(meter_id, ts_from, ts_to),
        )
        conn.close()
    except Exception:
        return 0.0

    if df.empty:
        return 0.0

    # add_ele w Wh (×0.001 kWh). Suma przyrostów = zużycie w oknie.
    wh = float(df["val_num"].fillna(0).sum())
    return wh / 1000.0  # Wh -> kWh


@st.cache_data(ttl=60)
def cached_meter_energy_daily(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    time_offset_hours: int = SERVER_TIMEZONE_OFFSET,
    db_file: str = DB_FILE,
    meter_id: Optional[str] = ENERGY_METER_DEV_ID,
) -> dict:
    """Dzienne zużycie z fizycznego licznika [kWh] per doba LOKALNA.

    To samo źródło i skala co cached_meter_energy (add_ele, 1 jednostka = 1 Wh),
    tylko rozbite na doby. Grupowanie po dobie lokalnej = data(timestamp+offset).
    Używane w tabeli dziennej (Bilans), spójne z boxem "Prąd pobrany (licznik)".

    Args:
        meter_id: device_id licznika dla wybranej pompy. None = pompa bez licznika → {}.

    Returns:
        Dict {data_iso 'YYYY-MM-DD': kwh}. Puste przy braku licznika lub danych.
    """
    if meter_id is None:
        return {}

    offset_sec = time_offset_hours * 3600

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
        # Doba lokalna: przesuwamy timestamp o offset i bierzemy datę (UTC epoch).
        df = pd.read_sql_query(
            """SELECT date(timestamp + ?, 'unixepoch') AS day,
                      SUM(val_num) AS wh
               FROM telemetry
               WHERE device_id = ? AND code = 'add_ele'
                 AND timestamp >= ? AND timestamp <= ?
               GROUP BY day""",
            conn, params=(offset_sec, meter_id, ts_from, ts_to),
        )
        conn.close()
    except Exception:
        return {}

    if df.empty:
        return {}

    # Wh -> kWh
    return {
        row["day"]: float(row["wh"] or 0.0) / 1000.0
        for _, row in df.iterrows()
        if row["day"] is not None
    }


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


def _live_is_cwu(work_mode: Optional[str], status: dict) -> bool:
    """Czy pompa grzeje aktualnie CWU (na żywo, z jednego snapshotu statusu).

    Klasyfikacja spójna z silnikiem (patrz _classify_cwu_mask w energy.py):
        - 'hot_water'      → CWU,
        - 'heat'           → CO,
        - 'heat_hot_water' → CWU gdy (hot_water_temp_set − tank_temp) > CWU_TANK_DIFF_ON,
                             inaczej CO.
    Bez historii (snapshot) stosujemy próg wejścia CWU_TANK_DIFF_ON.

    Args:
        work_mode: Wartość work_mode (enum tekstowy) lub None.
        status: Słownik ostatnich odczytów (do tank_temp / hot_water_temp_set).

    Returns:
        True gdy CWU, False gdy CO (lub gdy tryb nieznany → domyślnie CO).
    """
    if not work_mode:
        return False
    if work_mode == COMBINED_WORK_MODE:
        tank = get_temp_value(status, "tank_temp")
        hw_set = get_temp_value(status, "hot_water_temp_set")
        if tank is None or hw_set is None:
            return False  # brak danych zasobnika → domyślnie CO
        return (hw_set - tank) > CWU_TANK_DIFF_ON
    return work_mode in DHW_WORK_MODES


def get_pump_status(status: dict) -> tuple[str, str, str]:
    """Określa status pompy na podstawie ostatnich wartości.

    Returns:
        (label, color, emoji) — np. ("CO — Grzeje", "#2196F3", "🔥")
    """
    comp_freq = status.get("comp_freq", {}).get("val_num", 0) or 0
    flow_rate = status.get("flow_rate", {}).get("val_num", 0) or 0
    defrost = _flag_value(status, "defrost")
    fault = _flag_value(status, "fault_flag") or _flag_value(status, "fault")

    # Tryb pracy z work_mode (DP 109) — enum tekstowy w val_str.
    work_mode = (status.get("work_mode", {}) or {}).get("val_str")

    if fault and fault > 0:
        return "AWARIA", "#e94560", "🚨"
    if defrost and defrost >= 0.5:
        return "Defrost", "#00BCD4", "❄️"
    if comp_freq > 5:
        if _live_is_cwu(work_mode, status):
            return "CWU — Podgrzewa wodę", "#E67E22", "🚿"
        else:
            return "CO — Grzeje", "#2196F3", "🔥"
    # Sprężarka stoi, ale pompa wody pracuje — faza obiegu kontrolnego/dobiegu
    # (pompa wody rusza przed sprężarką i pracuje po jej zatrzymaniu).
    if is_pump_running(flow_rate, FLOW_RATE_ON_THRESHOLD):
        return "Obieg wody", "#8BC34A", "💧"
    return "Postój", "#555555", "⏸"


# Trzy stany aktywności agregatu (jedno źródło prawdy dla wizualizacji na Panelu).
# Kolory dobrane pod ciemny motyw i spójne z render_scop_box / tłem nagłówka.
PUMP_ACTIVITY_HEATING = "heating"   # sprężarka pracuje (grzeje)
PUMP_ACTIVITY_RUNNING = "running"   # pompa wody pracuje, sprężarka stoi (obieg/dobieg)
PUMP_ACTIVITY_OFF = "off"           # pompa wody nie pracuje (postój)

PUMP_ACTIVITY_COLORS: dict[str, str] = {
    PUMP_ACTIVITY_HEATING: "#E67E22",  # pomarańcz/ogień — grzeje (sprężarka ON)
    PUMP_ACTIVITY_RUNNING: "#2ECC71",  # zielony — działa (pompa wody ON, sprężarka OFF)
    PUMP_ACTIVITY_OFF: "#555555",      # szary — nie działa
}


def get_pump_activity(status: dict) -> str:
    """Wyznacza 3-stanową aktywność agregatu (jedno źródło prawdy).

    Stany (od najwyższej aktywności):
        - PUMP_ACTIVITY_HEATING ('heating') — sprężarka pracuje (comp_freq > próg): GRZEJE,
        - PUMP_ACTIVITY_RUNNING ('running') — pompa wody pracuje, sprężarka stoi: DZIAŁA
          (obieg kontrolny / dobieg — pompa wody rusza przed sprężarką i pracuje po niej),
        - PUMP_ACTIVITY_OFF ('off') — pompa wody nie pracuje: NIE DZIAŁA.

    Args:
        status: Słownik ostatnich odczytów (load_latest_status).

    Returns:
        Klucz stanu (patrz stałe PUMP_ACTIVITY_*). Kolor: PUMP_ACTIVITY_COLORS[klucz].
    """
    comp_freq = status.get("comp_freq", {}).get("val_num", 0) or 0
    flow_rate = status.get("flow_rate", {}).get("val_num", 0) or 0

    if comp_freq > COMP_FREQ_ON_THRESHOLD:
        return PUMP_ACTIVITY_HEATING
    if is_pump_running(flow_rate, FLOW_RATE_ON_THRESHOLD):
        return PUMP_ACTIVITY_RUNNING
    return PUMP_ACTIVITY_OFF


def pump_supports_cwu(status: dict) -> bool:
    """Czy pompa obsługuje CWU (ma podłączony czujnik zasobnika ciepłej wody).

    Rozróżnienie po tank_temp: pompa BEZ obsługi CWU ma czujnik zasobnika
    rozwarty/niepodłączony, przez co raportuje wartość ujemną (obserwowane -30°C,
    surowo -300). Pompa z CWU raportuje realną temperaturę zasobnika (> 0).

    Próg: tank_temp < 0 → brak CWU. Gdy brak danych tank_temp → zakładamy że
    pompa obsługuje CWU (bezpieczny domyślny — nie ukrywamy istniejącej funkcji).

    Args:
        status: Słownik ostatnich odczytów (load_latest_status).

    Returns:
        True gdy pompa obsługuje CWU, False gdy nie (ujemny tank_temp).
    """
    tank = get_temp_value(status, "tank_temp")
    if tank is None:
        return True
    return tank >= 0


def get_live_heating_mode(status: dict) -> Optional[str]:
    """Co pompa grzeje TERAZ (na żywo) — 'co', 'cwu' albo None.

    Zwraca tryb tylko gdy SPRĘŻARKA pracuje (comp_freq > próg) — tj. gdy realnie
    idzie grzanie. W fazie obiegu wody (pompa wody ON, sprężarka OFF), postoju,
    defrostu i awarii zwraca None (nic nie jest aktywnie grzane).

    Klasyfikacja CO/CWU spójna z silnikiem energii przez _live_is_cwu()
    (to samo źródło co get_pump_status / _classify_cwu_mask).

    Args:
        status: Słownik ostatnich odczytów (load_latest_status).

    Returns:
        'cwu' — grzeje ciepłą wodę, 'co' — grzeje centralne, None — nic nie grzeje.
    """
    comp_freq = status.get("comp_freq", {}).get("val_num", 0) or 0
    if comp_freq <= COMP_FREQ_ON_THRESHOLD:
        return None
    work_mode = (status.get("work_mode", {}) or {}).get("val_str")
    return "cwu" if _live_is_cwu(work_mode, status) else "co"


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

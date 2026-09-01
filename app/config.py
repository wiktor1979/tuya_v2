"""Konfiguracja projektu tuya_v2 — stałe i parametry domyślne."""
import os
import json
from typing import List, Dict, Any

# --- Tuya Pulsar (collector) ---
TUYA_ACCOUNTS: List[Dict[str, Any]] = []

_single_id = os.environ.get("TUYA_ACCESS_ID")
_single_key = os.environ.get("TUYA_ACCESS_KEY")
_single_devs = os.environ.get("TUYA_DEVICE_IDS", "")

if _single_id and _single_key:
    TUYA_ACCOUNTS.append({
        "access_id": _single_id,
        "access_key": _single_key,
        "devices": [d.strip() for d in _single_devs.split(",") if d.strip()],
    })

_accounts_json = os.environ.get("TUYA_ACCOUNTS_JSON")
if _accounts_json:
    try:
        parsed = json.loads(_accounts_json)
        if isinstance(parsed, list):
            TUYA_ACCOUNTS = parsed
    except json.JSONDecodeError:
        pass

MQ_ENV_PROD = "event"
PULSAR_SERVER_EU = "pulsar+ssl://mqe.tuyaeu.com:7285/"

# --- Baza danych ---
DB_FILE: str = os.environ.get("DB_FILE", "./data/tuya_telemetry.db")

# --- Urządzenia ---
HEAT_PUMP_DEV_ID: str = "bf874f7ae72aca1fc23op0"
MANUAL_METER_DEV_ID: str = "licznikRęczny"

# --- Kody telemetryczne potrzebne do bilansu energetycznego ---
ENERGY_CODES: tuple[str, ...] = (
    "ac_vol", "ac_curr",               # P_el
    "flow_rate", "out_water_temp", "in_water_temp",  # P_th
    "comp_freq",                        # praca sprężarki (ON/OFF, starty)
    "valve",                            # CO/CWU (>= 0.5 = CWU)
    "defrost",                          # cykl odszraniania
    "amb_temp",                         # temperatura zewnętrzna (HDD)
)

# --- Kody temperatur (wartości w bazie dzielone przez 10) ---
TEMP_CODES: frozenset[str] = frozenset({
    "in_water_temp", "out_water_temp", "tank_temp",
    "amb_temp", "disc_temp", "back_temp", "tidr",
    "cool_temp_set", "heat_temp_set", "hot_water_temp_set",
    "heat_temp_set_z2", "cool_temp_set_z2",
    "auto_heat_temp_set_z1", "auto_heat_temp_set_z2", "auto_cool_temp_set_z2",
    "idr_temp_set",
})

# --- Parametry całkowania ---
DT_MAX_SEC: int = 360
"""Maksymalny Δt między próbkami [s]. Heartbeat=300s + margines na jitter.
Przerwy > 360s traktowane jako gap w danych (E=0, gaps_skipped++)."""

COMP_FREQ_ON_THRESHOLD: float = 5.0
"""Sprężarka pracuje gdy comp_freq > 5 Hz."""

CWU_VALVE_THRESHOLD: float = 0.5
"""Tryb CWU gdy valve >= 0.5, CO gdy < 0.5."""

# --- Parametry fizyczne (domyślne) ---
DEFAULT_COS_PHI: float = 0.95
DEFAULT_STANDBY_POWER_W: float = 25.0
"""Moc standby WIDOCZNA w czujniku [W] (gdy raw P_el < 100W)."""
DEFAULT_ACTIVE_POWER_W: float = 40.0
"""Dodatkowa moc WIDOCZNA w czujniku podczas pracy sprężarki [W]."""
DEFAULT_HIDDEN_POWER_W: float = 0.0
"""Stały pobór NIEWIDOCZNY w czujniku [W]. Kalibrowany z licznika. 0 = brak (dane letnie nie dają sensownego hidden)."""
DEFAULT_SENSOR_FACTOR: float = 0.98
"""Korekcja proporcjonalna czujnika [×]. 0.98 = telemetria zawyża ~2% vs licznik fizyczny (kalibracja 2026-09-01, dane letnie CWU)."""

# --- HDD ---
HDD_BASE_TEMP_C: float = 15.0
"""Temperatura bazowa dla Heating Degree Days [°C]."""

# --- Strefa czasowa ---
DEFAULT_TIME_OFFSET_HOURS: int = int(os.environ.get("SERVER_TIMEZONE_OFFSET", "2"))
"""Przesunięcie strefy czasowej vs UTC. Domyślnie CEST=2."""

# Alias dla kompatybilności z collectorem/notifierem
SERVER_TIMEZONE_OFFSET: int = DEFAULT_TIME_OFFSET_HOURS

# --- Sezon grzewczy ---
HEATING_SEASON_START_MONTH: int = 9   # wrzesień
HEATING_SEASON_END_MONTH: int = 4     # kwiecień

# --- Histereza DeadbandFilter (collector) ---
HISTERESIS_CONFIG: dict = {
    "out_water_temp": {"active": 0.2, "idle": 0.5, "last_value": None},
    "in_water_temp":  {"active": 0.2, "idle": 0.5, "last_value": None},
    "tank_temp":      {"active": 0.2, "idle": 0.5, "last_value": None},
    "amb_temp":       {"active": 0.5, "idle": 0.8, "last_value": None},
    "tidr":           {"active": 0.5, "idle": 0.5, "last_value": None},
    "disc_temp":      {"active": 0.5, "idle": 1.5, "last_value": None},
    "back_temp":      {"active": 0.5, "idle": 1.5, "last_value": None},
    "ac_curr":        {"active": 2.0, "idle": 5.0, "last_value": None},
    "ac_vol":         {"active": 2.0, "idle": 3.0, "last_value": None},
    "comp_freq":      {"active": 2.0, "idle": 1.0, "last_value": None},
    "flow_rate":      {"active": 2.0, "idle": 1.0, "last_value": None},
    "dc_fan1":        {"active": 15.0, "idle": 50.0, "last_value": None},
    "dc_fan2":        {"active": 50.0, "idle": 50.0, "last_value": None},
    "m_eev":          {"active": 5.0, "idle": 20.0, "last_value": None},
    "a_eev":          {"active": 5.0, "idle": 20.0, "last_value": None},
}

MAX_HEARTBEAT_SEC: int = 300

# --- Lokalizacja (Open-Meteo) ---
LATITUDE: float = float(os.environ.get("LATITUDE", 51.7592))
LONGITUDE: float = float(os.environ.get("LONGITUDE", 19.4560))
LOCATION_NAME: str = os.environ.get("LOCATION_NAME", "Łódź")

# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED: bool = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
DAILY_REPORT_HOUR: int = int(os.environ.get("DAILY_REPORT_HOUR", "8"))


# --- Metadane parametrów pompy (z oficjalnej specyfikacji modelu Tuya 0000043th5) ---
PARAM_INFO: dict[str, dict[str, str]] = {
    "in_water_temp": {"label": "Powrót CO", "desc": "Temperatura wody powracającej z instalacji grzewczej"},
    "out_water_temp": {"label": "Zasilanie CO", "desc": "Temperatura wody wychodzącej na dom"},
    "tank_temp": {"label": "Woda CWU", "desc": "Temperatura wody w zasobniku ciepłej wody użytkowej"},
    "amb_temp": {"label": "Temp. zewnętrzna", "desc": "Temperatura powietrza na zewnątrz budynku"},
    "disc_temp": {"label": "Tłoczenie sprężarki", "desc": "Temperatura gazu na wylocie sprężarki"},
    "back_temp": {"label": "Powrót do sprężarki", "desc": "Temperatura czynnika na ssaniu sprężarki"},
    "tidr": {"label": "Temp. pokojowa", "desc": "Temperatura wewnętrzna pomieszczenia"},
    "heat_temp_set": {"label": "Nastawa CO Z1", "desc": "Zadana temperatura zasilania — strefa 1"},
    "hot_water_temp_set": {"label": "Nastawa CWU", "desc": "Zadana temperatura wody użytkowej"},
    "heat_temp_set_z2": {"label": "Nastawa CO Z2", "desc": "Zadana temperatura zasilania — strefa 2 / podłogówka"},
    "idr_temp_set": {"label": "Nastawa z krzywej", "desc": "Temperatura zadana z krzywej grzewczej"},
    "ac_vol": {"label": "Napięcie AC", "desc": "Napięcie zasilania [V]"},
    "ac_curr": {"label": "Prąd AC", "desc": "Prąd pobierany, skala ×0.1 A"},
    "comp_freq": {"label": "Częst. sprężarki", "desc": "Częstotliwość pracy sprężarki [Hz]"},
    "flow_rate": {"label": "Przepływ", "desc": "Przepływ wody, skala ×0.1 m³/h"},
    "m_eev": {"label": "Zawór EEV", "desc": "Pozycja głównego zaworu rozprężnego, 0-480 kroków"},
    "dc_fan1": {"label": "Wentylator DC", "desc": "Obroty wentylatora DC, 0-1000 RPM"},
    "defrost": {"label": "Odszranianie", "desc": "Cykl odszraniania parownika"},
    "valve": {"label": "Zawór 3-drożny", "desc": "CO/CWU (≥0.5 = CWU)"},
    "fault": {"label": "Kody błędów", "desc": "Bitmapa błędów E01-E16, P01-P14"},
    "work_mode": {"label": "Tryb pracy", "desc": "cool, heat, auto, hot_water, ..."},
    "zone_select": {"label": "Aktywna strefa", "desc": "0=brak, 1=Z1, 2=Z2, 3=obie"},
}

FAULT_BITMAP_LABELS: list[str] = [
    "E01", "E02", "E03", "E04", "E05", "E06", "E07", "E08",
    "E09", "E10", "E11", "E12", "E13", "E14", "E15", "E16",
    "P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08",
    "P09", "P10", "P11", "P12", "P13", "P14",
]


def get_param_label(code: str) -> str:
    """Zwraca etykietę parametru z kodem w nawiasie."""
    info = PARAM_INFO.get(code)
    return f"{info['label']} ({code})" if info else code

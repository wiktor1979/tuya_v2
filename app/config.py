"""Konfiguracja projektu tuya_v2 — stałe i parametry domyślne."""
import os
import json
from typing import List, Dict, Any, Optional

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

# --- Urządzenia / pompy ciepła ---
# Lista monitorowanych pomp. Każda pompa: własne device_id (telemetria) i opcjonalny
# meter_id (fizyczny licznik energii Tuya). Druga pompa nie ma licznika → meter_id=None.
# Wszystkie na TYM SAMYM koncie Tuya (jeden strumień Pulsar).
PUMPS: list[dict] = [
    {
        "id": "pompa1",
        "name": "Wiktor",
        "device_id": "bf874f7ae72aca1fc23op0",
        "meter_id": "bf215e9c483af020b12cak",
        "thermo_id": "bf9134db09e1ea78cdskae",  # zewnętrzny termometr temp. powietrza w pomieszczeniu
    },
    {
        "id": "pompa2",
        "name": "Karol",
        "device_id": "bf16fd09ab4030b8f8ktge",
        "meter_id": None,               # druga pompa BEZ licznika energii
        "thermo_id": None,              # bez zewnętrznego termometru
    },
]
"""Konfiguracja monitorowanych pomp. Nazwa i id stałe (nieedytowalne w UI)."""

DEFAULT_PUMP_ID: str = PUMPS[0]["id"]
"""Domyślna pompa (gdy brak wyboru w query_params)."""


def list_pumps() -> list[dict]:
    """Zwraca listę skonfigurowanych pomp (kopia, by nie modyfikować oryginału)."""
    return [dict(p) for p in PUMPS]


def get_pump(pump_id: Optional[str]) -> dict:
    """Zwraca konfigurację pompy po jej 'id'. Fallback: pompa domyślna (pierwsza).

    Args:
        pump_id: Identyfikator pompy ('id' z PUMPS) lub None.

    Returns:
        Dict pompy: {id, name, device_id, meter_id}.
    """
    if pump_id:
        for p in PUMPS:
            if p["id"] == pump_id:
                return dict(p)
    return dict(PUMPS[0])


# Aliasy wsteczne — pierwsza pompa. Utrzymują kompatybilność z kodem/testami,
# które importują pojedyncze stałe (notifier, power_analysis, testy, domyślne argumenty).
HEAT_PUMP_DEV_ID: str = PUMPS[0]["device_id"]
MANUAL_METER_DEV_ID: str = "licznikRęczny"
ENERGY_METER_DEV_ID: str = PUMPS[0]["meter_id"]
"""Inteligentny licznik prądu (Tuya, to samo konto co pompa) — alias pierwszej pompy.
Uwaga: wielo-pompowo używać ENERGY_METER_DEV_IDS (zbiór wszystkich liczników)."""

# Zbiór wszystkich device_id liczników (do whitelist/obsługi w collectorze).
ENERGY_METER_DEV_IDS: frozenset[str] = frozenset(
    p["meter_id"] for p in PUMPS if p["meter_id"]
)
"""Wszystkie device_id liczników energii (pomijając pompy bez licznika)."""

# Zbiór wszystkich device_id pomp (telemetria) — do whitelist collectora.
HEAT_PUMP_DEV_IDS: frozenset[str] = frozenset(p["device_id"] for p in PUMPS)

# Zbiór wszystkich device_id zewnętrznych termometrów (temp. powietrza w pomieszczeniu) —
# do whitelist collectora. Termometr powiązany z pompą przez pole 'thermo_id' w PUMPS.
# Pomija pompy bez termometru (thermo_id=None).
THERMO_DEV_IDS: frozenset[str] = frozenset(
    p["thermo_id"] for p in PUMPS if p.get("thermo_id")
)

# Czytelne nazwy urządzeń — używane w powiadomieniach Telegram i UI zamiast device_id.
DEVICE_NAMES: dict[str, str] = {}
for _p in PUMPS:
    DEVICE_NAMES[_p["device_id"]] = _p["name"]
    if _p["meter_id"]:
        DEVICE_NAMES[_p["meter_id"]] = f"Licznik {_p['name']}"
    if _p.get("thermo_id"):
        DEVICE_NAMES[_p["thermo_id"]] = f"Termometr {_p['name']}"
DEVICE_NAMES[MANUAL_METER_DEV_ID] = "Licznik ręczny"


def get_device_name(device_id: str) -> str:
    """Zwraca czytelną nazwę urządzenia (fallback: samo device_id)."""
    return DEVICE_NAMES.get(device_id, device_id)

# --- Kody telemetryczne potrzebne do bilansu energetycznego ---
ENERGY_CODES: tuple[str, ...] = (
    "ac_vol", "ac_curr",               # P_el
    "flow_rate", "out_water_temp", "in_water_temp",  # P_th
    "comp_freq",                        # praca sprężarki (ON/OFF, starty)
    "work_mode",                        # tryb pracy (heat/hot_water/heat_hot_water) → CO/CWU
    "tank_temp",                        # temp. zasobnika CWU (podział trybu łączonego)
    "hot_water_temp_set",               # zadana temp. CWU (podział trybu łączonego)
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
    # Zewnętrzny termometr temp. powietrza w pomieszczeniu (skala ×0.1, jak pompa).
    # va_temperature i temp_current to duplikat tej samej wartości (dwa kody DP).
    "va_temperature", "temp_current",
})

# --- Parametry całkowania ---
DT_MAX_SEC: int = 360
"""Maksymalny Δt między próbkami [s]. Heartbeat=300s + margines na jitter.
Przerwy > 360s traktowane jako gap w danych (E=0, gaps_skipped++)."""

COMP_FREQ_ON_THRESHOLD: float = 5.0
"""Sprężarka pracuje gdy comp_freq > 5 Hz."""

CWU_TANK_DIFF_ON: float = 5.0
"""Próg WEJŚCIA w tryb CWU w trybie łączonym 'heat_hot_water'.

Gdy (hot_water_temp_set − tank_temp) > 5°C, zasobnik CWU jest niedogrzany
i pompa priorytetowo ładuje CWU (priorytet ciepłej wody). Inaczej grzeje CO.
Zawór 3-drożny CO/CWU NIE jest raportowany jako osobny DP w tej pompie, więc
podział trybu łączonego wyznaczamy z różnicy temperatur zasobnika."""

CWU_TANK_DIFF_OFF: float = 0.0
"""Próg WYJŚCIA z trybu CWU (histereza). Raz rozpoczęte ładowanie CWU trwa aż
zasobnik osiągnie temperaturę zadaną, tj. różnica (hot_water_temp_set − tank_temp)
spadnie ≤ 0°C. Histereza (ON=5, OFF=0, rozpiętość 5°C): CWU ma priorytet i jest
grzane do końca (do setpointu), zanim pompa wróci do CO."""

HEATING_WORK_MODES: frozenset[str] = frozenset({"heat", "heat_hot_water"})
"""work_mode obejmujące ogrzewanie CO."""

DHW_WORK_MODES: frozenset[str] = frozenset({"hot_water", "heat_hot_water"})
"""work_mode grzewcze obejmujące CWU (ciepłą wodę). Chłodzenie ('cool_hot_water')
pominięte — wyłączone sprzętowo i wykluczane z bilansu grzewczego."""

COMBINED_WORK_MODE: str = "heat_hot_water"
"""Tryb łączony CO+CWU — podział wg reguły histerezy zasobnika."""

COOLING_WORK_MODES: frozenset[str] = frozenset({"cool", "cool_hot_water"})
"""work_mode z chłodzeniem. Chłodzenie jest WYŁĄCZONE sprzętowo w tej instalacji —
energia chłodzenia pomijana w bilansie SCOP grzewczym."""

FLOW_RATE_ON_THRESHOLD: float = 3.0
"""Pompa wody (obiegowa) pracuje gdy flow_rate (surowe, skala ×0.1 m³/h) > 3,
tj. > 0.3 m³/h. To sygnał "AGREGAT PRACUJE" (nie sama sprężarka).

Analiza danych (sekwencja startu/stopu CWU): pompa wody rusza ~2 min PRZED
sprężarką i pracuje ~2 min PO jej zatrzymaniu (dobieg/odbiór ciepła).
Podczas pracy flow_rate ~5–17 (0.5–1.7 m³/h), w postoju 0. Próg 3 (0.3 m³/h)
odcina szum i pojedyncze zafałszowania, łapiąc też fazę wolnego obiegu kontrolnego.

Różnica względem COMP_FREQ_ON_THRESHOLD: comp_freq = "sprężarka pracuje"
(pobór energii, liczenie startów, SCOP), flow_rate = "urządzenie pracuje"
(hydraulika aktywna) — szerszy interwał obejmujący dobieg pompy."""

# --- Parametry fizyczne (domyślne) ---
DEFAULT_COS_PHI: float = 0.95
DEFAULT_STANDBY_POWER_W: float = 4.0
"""Pobór pompy w standby [W]. Zmierzone licznikiem fizycznym (2026-09-02):
obwód licznika = TYLKO pompa ciepła, cur_power w spoczynku ~4 W (moc czynna).
Wcześniej 25 W (szacunek dopasowany do sondy prądowej przed montażem licznika)."""
DEFAULT_ACTIVE_POWER_W: float = 300.0
"""Dodatkowa moc WIDOCZNA w czujniku podczas pracy sprężarki [W].
300 W (2026-09-11) — realny dodatkowy pobór podczas pracy agregatu: pompa
obiegowa + wentylator (max) + elektronika, widoczny w sondzie prądowej.
Synchronizacja z wartością w tabeli settings (load_calibration()).
Config = fallback dla pustej bazy."""
DEFAULT_HIDDEN_POWER_W: float = 0.0
"""Stały pobór NIEWIDOCZNY w czujniku [W]. Kalibrowany z licznika. 0 = brak (dane letnie nie dają sensownego hidden)."""
DEFAULT_SENSOR_FACTOR: float = 0.98
"""Korekcja proporcjonalna czujnika [×]. 0.98 = telemetria zawyża ~2% vs licznik fizyczny (kalibracja 2026-09-01, dane letnie CWU)."""

# --- HDD ---
HDD_BASE_TEMP_C: float = 15.0
"""Temperatura bazowa dla Heating Degree Days [°C]."""

# --- Strefa czasowa ---
def get_timezone_offset() -> int:
    """Dynamicznie oblicza offset strefy czasowej (uwzględnia DST automatycznie).
    
    Używa strefy 'Europe/Warsaw' — latem +2 (CEST), zimą +1 (CET).
    Działa na Python 3.9+ (zoneinfo w stdlib).
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        return int(datetime.now(ZoneInfo("Europe/Warsaw")).utcoffset().total_seconds() // 3600)
    except Exception:
        # Fallback dla środowisk bez zoneinfo
        return 2


# Aktualna wartość — używana w całej aplikacji
SERVER_TIMEZONE_OFFSET: int = get_timezone_offset()

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
    # Zewnętrzny termometr — temperatura pokojowa (dzielona ×0.1 przy zapisie, jak pompa).
    # va_temperature i temp_current to duplikat tej samej wartości; oba przez próg 0.2/0.3°C.
    "va_temperature": {"active": 0.2, "idle": 0.3, "last_value": None},
    "temp_current":   {"active": 0.2, "idle": 0.3, "last_value": None},
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
    # Licznik energii (ENERGY_METER_DEV_ID). Wartości surowe.
    # Próg 5 (surowo) = 0.5 W. Jeden próg — licznik niezależny od stanu sprężarki pompy.
    # Zmienione 2026-09-03: wcześniejsze 20.0 (2.0 W) pomijało zbyt wiele danych.
    "cur_power":      {"active": 5.0, "idle": 5.0, "last_value": None},
    # Historia dopisana 2026-09-08: zbieranie również cur_voltage i cur_current
    # (dla analizy rozkładu mocy).
    "cur_voltage":    {"active": 2.0, "idle": 3.0, "last_value": None},  # V (surowe), skala ×1
    "cur_current":    {"active": 2.0, "idle": 5.0, "last_value": None},  # mA (surowe), skala ×0.001 A
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
    "va_temperature": {"label": "Temp. pokojowa (termometr)", "desc": "Temperatura powietrza w pomieszczeniu z zewnętrznego termometru"},
    "heat_temp_set": {"label": "Nastawa CO Z1", "desc": "Zadana temperatura zasilania — strefa 1"},
    "hot_water_temp_set": {"label": "Nastawa CWU", "desc": "Zadana temperatura wody użytkowej"},
    "heat_temp_set_z2": {"label": "Nastawa CO Z2", "desc": "Zadana temperatura zasilania — strefa 2 / podłogówka"},
    "idr_temp_set": {"label": "Nastawa pokojowa", "desc": "Zadana temperatura powietrza w pomieszczeniu"},
    "ac_vol": {"label": "Napięcie AC", "desc": "Napięcie zasilania [V]"},
    "ac_curr": {"label": "Prąd AC", "desc": "Prąd pobierany, skala ×0.1 A"},
    "comp_freq": {"label": "Częst. sprężarki", "desc": "Częstotliwość pracy sprężarki [Hz]"},
    "flow_rate": {"label": "Przepływ", "desc": "Przepływ wody, skala ×0.1 m³/h"},
    "m_eev": {"label": "Zawór EEV", "desc": "Pozycja głównego zaworu rozprężnego, 0-480 kroków"},
    "a_eev": {"label": "Zawór EEV dod.", "desc": "Pozycja dodatkowego zaworu rozprężnego, 0-480 kroków"},
    "dc_fan1": {"label": "Wentylator DC", "desc": "Obroty wentylatora DC, 0-1000 RPM"},
    "dc_fan2": {"label": "Wentylator DC 2", "desc": "Obroty drugiego wentylatora DC [RPM]"},
    "ac_fan": {"label": "Tryb wentylatora", "desc": "Bieg wentylatora: close / low_spd / high_spd"},
    "defrost": {"label": "Odszranianie", "desc": "Cykl odszraniania parownika"},
    "valve": {"label": "Zawór 4-drożny", "desc": "Rewers grzanie/chłodzenie (koreluje ze sprężarką, nie CO/CWU)"},
    "fault": {"label": "Kody błędów", "desc": "Bitmapa błędów E01-E16, P01-P14"},
    "work_mode": {"label": "Tryb pracy", "desc": "heat=CO, hot_water=CWU, heat_hot_water=CO+CWU (źródło podziału)"},
    "zone_select": {"label": "Aktywna strefa", "desc": "0=brak, 1=Z1, 2=Z2, 3=obie"},
    "auto_run_tar_mode": {"label": "Cel trybu auto", "desc": "Co pompa robi w trybie auto: 0=chłodzenie, 1=ogrzewanie"},
    "cool_temp_set": {"label": "Nastawa chłodzenia Z1", "desc": "Zadana temperatura chłodzenia — strefa 1"},
    "cool_temp_set_z2": {"label": "Nastawa chłodzenia Z2", "desc": "Zadana temperatura chłodzenia — strefa 2"},
    "auto_heat_temp_set_z1": {"label": "Nastawa auto CO Z1", "desc": "Zadana temperatura auto grzanie — strefa 1"},
    "auto_heat_temp_set_z2": {"label": "Nastawa auto CO Z2", "desc": "Zadana temperatura auto grzanie — strefa 2"},
    "auto_cool_temp_set_z2": {"label": "Nastawa auto chłodz. Z2", "desc": "Zadana temperatura auto chłodzenie — strefa 2"},
    "pump_sta": {"label": "Pompa obiegowa", "desc": "Status pompy wody (pracuje ~2 min po sprężarce)"},
    "protect_flag": {"label": "Ochrona", "desc": "Flaga ochrony urządzenia"},
    "freeze": {"label": "Antyzamrożenie", "desc": "Ochrona antyzamrożeniowa"},
    "fault_flag": {"label": "Flaga awarii", "desc": "Flaga sygnalizująca awarię"},
    "switch": {"label": "Wyłącznik główny", "desc": "Główny wyłącznik pompy"},
    "mute": {"label": "Tryb cichy", "desc": "Tryb cichy (Silent)"},
    "holiday_sw": {"label": "Tryb urlopowy", "desc": "Tryb urlopowy (Holiday)"},
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

"""Warstwa dostępu do bazy danych z poolingiem połączeń."""
import sqlite3
from typing import Optional, List, Tuple
from contextlib import contextmanager
from app.config import (
    DB_FILE, HEAT_PUMP_DEV_ID, MANUAL_METER_DEV_ID, TEMP_CODES,
    DEFAULT_COS_PHI, DEFAULT_STANDBY_POWER_W, DEFAULT_ACTIVE_POWER_W,
    DEFAULT_HIDDEN_POWER_W, DEFAULT_SENSOR_FACTOR,
)

# Parametry kalibracji — JEDNO źródło prawdy dla kluczy i wartości domyślnych.
# Bieżące wartości trzymane w tabeli `settings`; DEFAULT_* to fallback dla pustej bazy.
# Używane przez UI (suwaki) i raport Telegram — wszystkie liczą z tych samych danych.
CALIBRATION_DEFAULTS: dict = {
    "cos_phi": DEFAULT_COS_PHI,
    "standby_power_w": DEFAULT_STANDBY_POWER_W,
    "active_power_w": DEFAULT_ACTIVE_POWER_W,
    "hidden_power_w": DEFAULT_HIDDEN_POWER_W,
    "sensor_factor": DEFAULT_SENSOR_FACTOR,
}

# Globalna pula połączeń (thread-local)
import threading
_local = threading.local()

def get_db_connection():
    """Pobiera połączenie z puli lub tworzy nowe."""
    if not hasattr(_local, 'connection') or _local.connection is None:
        _local.connection = sqlite3.connect(DB_FILE, check_same_thread=False)
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA synchronous=NORMAL")
        _local.connection.execute("PRAGMA cache_size=-64000")  # 64MB cache
    return _local.connection

@contextmanager
def db_cursor():
    """Kontekst menedżer dla kursora bazy danych."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    finally:
        cursor.close()

def init_db() -> None:
    """Tworzy tabelę telemetry oraz indeksy wyszukiwania."""
    with db_cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                code TEXT NOT NULL,
                val_num REAL,
                val_str TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS weather_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                temperature REAL,
                humidity REAL,
                windspeed REAL,
                precipitation REAL,
                latitude REAL,
                longitude REAL,
                direct_radiation REAL,
                diffuse_radiation REAL
            )
        ''')
        
        # Migracja: dodaj kolumny nasłonecznienia jeśli nie istnieją
        cursor.execute("PRAGMA table_info(weather_data)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if 'direct_radiation' not in existing_cols:
            cursor.execute('ALTER TABLE weather_data ADD COLUMN direct_radiation REAL')
        if 'diffuse_radiation' not in existing_cols:
            cursor.execute('ALTER TABLE weather_data ADD COLUMN diffuse_radiation REAL')
        
        # Indeksy zoptymalizowane pod typowe zapytania
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_code_time ON telemetry (code, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_dev_time ON telemetry (device_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_weather_time ON weather_data (timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_dev_code_time ON telemetry (device_id, code, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_time_desc ON telemetry (timestamp DESC)')

        # Tabela ustawień użytkownika (key/value)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')

        # Tabela historii awarii pompy
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fault_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                fault_code TEXT NOT NULL,
                fault_bitmap INTEGER NOT NULL,
                resolved_at INTEGER,
                resolved INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_fault_device_time ON fault_log (device_id, timestamp DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_fault_unresolved ON fault_log (device_id, resolved)')


def save_manual_energy_reading(reading_val: float, timestamp_sec: int) -> Tuple[bool, str]:
    """
    Zapisuje ręczny odczyt z fizycznego licznika energii.
    Zabezpiecza przed wysłaniem pustych danych, ujemnych oraz duplikatów.
    """
    if reading_val is None or reading_val <= 0:
        return False, "Wartość licznika musi być większa od zera."

    with db_cursor() as cursor:
        # Zabezpieczenie 1: Dokładnie ten sam znacznik czasu
        cursor.execute('''
            SELECT id FROM telemetry
            WHERE device_id = ? AND timestamp = ? AND code = 'energy_kwh'
        ''', (MANUAL_METER_DEV_ID, timestamp_sec))
        if cursor.fetchone():
            return False, "Wpis z wybraną datą i godziną już istnieje."

        # Zabezpieczenie 2: Identyczny stan licznika dla sąsiadującego wpisu
        cursor.execute('''
            SELECT val_num FROM telemetry
            WHERE device_id = ? AND code = 'energy_kwh'
            ORDER BY ABS(timestamp - ?) ASC LIMIT 1
        ''', (MANUAL_METER_DEV_ID, timestamp_sec))
        closest = cursor.fetchone()
        if closest and closest[0] is not None and abs(closest[0] - reading_val) < 0.0001:
            return False, "Taka wartość licznika została już wcześniej zarejestrowana."

        cursor.execute('''
            INSERT INTO telemetry (timestamp, device_id, code, val_num, val_str)
            VALUES (?, ?, 'energy_kwh', ?, NULL)
        ''', (timestamp_sec, MANUAL_METER_DEV_ID, float(reading_val)))

    return True, "Odczyt został pomyślnie zapisany."


def update_manual_energy_reading(rec_id: int, new_val: float, timestamp_sec: int) -> Tuple[bool, str]:
    """Aktualizuje istniejący wpis ręczny wyłącznie dla device_id = MANUAL_METER_DEV_ID."""
    if new_val is None or new_val <= 0:
        return False, "Wartość licznika musi być większa od zera."

    with db_cursor() as cursor:
        cursor.execute('''
            UPDATE telemetry
            SET timestamp = ?, val_num = ?
            WHERE id = ? AND device_id = ? AND code = 'energy_kwh'
        ''', (timestamp_sec, float(new_val), rec_id, MANUAL_METER_DEV_ID))
        
        updated = cursor.rowcount > 0
    
    if updated:
        return True, "Wpis został zaktualizowany."
    return False, "Nie znaleziono wskazanego wpisu do edycji."


def delete_manual_energy_reading(rec_id: int) -> bool:
    """Usuwa wpis ręczny z bazy danych."""
    with db_cursor() as cursor:
        cursor.execute('''
            DELETE FROM telemetry 
            WHERE id = ? AND device_id = ? AND code = 'energy_kwh'
        ''', (rec_id, MANUAL_METER_DEV_ID))
        deleted = cursor.rowcount > 0
    return deleted


def save_properties_to_db(dev_id: str, properties: list, event_time: Optional[int] = None) -> bool:
    """Zapisuje dynamiczną listę parametrów z ramki Tuya do bazy SQLite."""
    # Akceptuj wszystkie urządzenia - nie filtruj po sztywnym ID
    # Dzięki temu można obsługiwać wiele pomp z różnych kont Tuya

    if not event_time:
        import time
        event_time = int(time.time())

    with db_cursor() as cursor:
        comp_freq_val = None
        for item in properties:
            if item.get("code") == "comp_freq":
                comp_freq_val = item.get("value")
                break

        if comp_freq_val is None:
            cursor.execute('''
                SELECT val_num FROM telemetry 
                WHERE device_id = ? AND code = 'comp_freq' 
                ORDER BY timestamp DESC LIMIT 1
            ''', (dev_id,))
            row = cursor.fetchone()
            comp_freq_val = row[0] if (row and row[0] is not None) else 0

        is_running = (comp_freq_val is not None and comp_freq_val > 0)
        records_to_insert = []

        for item in properties:
            code = item.get("code")
            raw_val = item.get("value")

            if code is None or raw_val is None:
                continue

            if code == "ac_vol" and not is_running:
                continue

            val_num = None
            val_str = None

            if code in TEMP_CODES and isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
                val_num = round(raw_val / 10.0, 1)
            elif isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
                val_num = float(raw_val)
            else:
                val_str = str(raw_val)

            records_to_insert.append((event_time, dev_id, code, val_num, val_str))

        if records_to_insert:
            cursor.executemany('''
                INSERT INTO telemetry (timestamp, device_id, code, val_num, val_str)
                VALUES (?, ?, ?, ?, ?)
            ''', records_to_insert)
            return True

    return False


def save_weather_data(timestamp: int, temperature: float, humidity: float, 
                      windspeed: float, precipitation: float, 
                      latitude: float, longitude: float,
                      direct_radiation: float = None,
                      diffuse_radiation: float = None) -> bool:
    """Zapisuje dane pogodowe z API Open-Meteo do bazy danych.
    
    HISTEREZA WYŁĄCZONA - każdy odczyt jest natychmiast zapisywany.
    """
    with db_cursor() as cursor:
        cursor.execute('''
            INSERT INTO weather_data (timestamp, temperature, humidity, windspeed, precipitation, latitude, longitude, direct_radiation, diffuse_radiation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (timestamp, temperature, humidity, windspeed, precipitation, latitude, longitude, direct_radiation, diffuse_radiation))
    return True


def get_weather_data(days: int = 7, is_today: bool = False) -> Optional[List[Tuple]]:
    """Pobiera dane pogodowe z ostatnich dni lub od 00:00 dzisiaj."""
    import time
    
    with db_cursor() as cursor:
        if is_today:
            cursor.execute('''
                SELECT id, timestamp, temperature, humidity, windspeed, precipitation, latitude, longitude,
                       direct_radiation, diffuse_radiation
                FROM weather_data
                WHERE date(timestamp, 'unixepoch', 'localtime') = date('now', 'localtime')
                ORDER BY timestamp ASC
            ''')
        else:
            cutoff_time = int(time.time()) - (days * 24 * 60 * 60)
            cursor.execute('''
                SELECT id, timestamp, temperature, humidity, windspeed, precipitation, latitude, longitude,
                       direct_radiation, diffuse_radiation
                FROM weather_data
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
            ''', (cutoff_time,))
        data = cursor.fetchall()
    return data


def get_setting(key: str, default: str = None) -> Optional[str]:
    """Pobiera wartość ustawienia z tabeli settings."""
    with db_cursor() as cursor:
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = cursor.fetchone()
    return row[0] if row else default


def get_current_work_mode() -> Optional[str]:
    """Pobiera aktualny tryb pracy pompy (work_mode) z ostatniego rekordu."""
    with db_cursor() as cursor:
        cursor.execute('''
            SELECT val_str FROM telemetry
            WHERE device_id = ? AND code = 'work_mode'
            ORDER BY timestamp DESC LIMIT 1
        ''', (HEAT_PUMP_DEV_ID,))
        row = cursor.fetchone()
    return row[0] if row else None


def get_current_auto_target() -> Optional[str]:
    """Pobiera aktualny cel trybu auto (auto_run_tar_mode): '0'=chłodzenie, '1'=ogrzewanie."""
    with db_cursor() as cursor:
        cursor.execute('''
            SELECT val_str FROM telemetry
            WHERE device_id = ? AND code = 'auto_run_tar_mode'
            ORDER BY timestamp DESC LIMIT 1
        ''', (HEAT_PUMP_DEV_ID,))
        row = cursor.fetchone()
    return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    """Zapisuje lub aktualizuje ustawienie w tabeli settings."""
    with db_cursor() as cursor:
        cursor.execute(
            'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
            (key, value)
        )


def load_calibration() -> dict:
    """Czyta parametry kalibracji z tabeli `settings` (fallback DEFAULT_*).

    JEDYNE źródło prawdy dla kalibracji w całej aplikacji. Zwraca dict gotowy
    do przekazania jako **cal do compute_energy()/cached_energy().
    Używane przez UI (suwaki) i raport Telegram (notifier).
    """
    cal: dict = {}
    for key, default in CALIBRATION_DEFAULTS.items():
        raw = get_setting(key, None)
        try:
            cal[key] = float(raw) if raw is not None else float(default)
        except (TypeError, ValueError):
            cal[key] = float(default)
    return cal


def save_calibration(cal: dict) -> None:
    """Zapisuje parametry kalibracji do tabeli `settings`.

    Zapewnia jedno źródło prawdy — UI (dashboard) i Telegram czytają
    zawsze tę samą wartość z `settings` przez load_calibration().
    """
    for key in CALIBRATION_DEFAULTS:
        if key in cal:
            set_setting(key, str(cal[key]))


# --- Obsługa awarii (fault_log) ---

def log_fault(device_id: str, fault_code: str, fault_bitmap: int, timestamp: int) -> None:
    """Zapisuje nową awarię do logu. Pomija jeśli identyczna awaria jest już aktywna."""
    with db_cursor() as cursor:
        # Sprawdź czy ten kod jest już aktywny (nierozwiązany)
        cursor.execute('''
            SELECT id FROM fault_log
            WHERE device_id = ? AND fault_code = ? AND resolved = 0
        ''', (device_id, fault_code))
        if cursor.fetchone():
            return  # Awaria już aktywna — nie duplikuj

        cursor.execute('''
            INSERT INTO fault_log (timestamp, device_id, fault_code, fault_bitmap, resolved, resolved_at)
            VALUES (?, ?, ?, ?, 0, NULL)
        ''', (timestamp, device_id, fault_code, fault_bitmap))


def resolve_faults(device_id: str, still_active_codes: List[str], timestamp: int) -> List[str]:
    """
    Oznacza jako rozwiązane awarie, które nie są już aktywne.
    Zwraca listę kodów, które zostały rozwiązane.
    """
    with db_cursor() as cursor:
        cursor.execute('''
            SELECT id, fault_code FROM fault_log
            WHERE device_id = ? AND resolved = 0
        ''', (device_id,))
        unresolved = cursor.fetchall()

        resolved_codes = []
        for row_id, code in unresolved:
            if code not in still_active_codes:
                cursor.execute('''
                    UPDATE fault_log SET resolved = 1, resolved_at = ?
                    WHERE id = ?
                ''', (timestamp, row_id))
                resolved_codes.append(code)

    return resolved_codes


def get_active_faults(device_id: str) -> List[Tuple]:
    """Pobiera aktywne (nierozwiązane) awarie."""
    with db_cursor() as cursor:
        cursor.execute('''
            SELECT id, timestamp, fault_code, fault_bitmap
            FROM fault_log
            WHERE device_id = ? AND resolved = 0
            ORDER BY timestamp DESC
        ''', (device_id,))
        return cursor.fetchall()


def get_fault_history(device_id: str, limit: int = 50) -> List[Tuple]:
    """Pobiera historię awarii (rozwiązane i aktywne)."""
    with db_cursor() as cursor:
        cursor.execute('''
            SELECT id, timestamp, fault_code, fault_bitmap, resolved, resolved_at
            FROM fault_log
            WHERE device_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (device_id, limit))
        return cursor.fetchall()


def get_current_fault_value(device_id: str) -> Optional[float]:
    """Pobiera ostatnią wartość bitmapy fault z telemetrii."""
    with db_cursor() as cursor:
        cursor.execute('''
            SELECT val_num FROM telemetry
            WHERE device_id = ? AND code = 'fault'
            ORDER BY timestamp DESC LIMIT 1
        ''', (device_id,))
        row = cursor.fetchone()
    return row[0] if row else None

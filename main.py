import time
import threading
from datetime import datetime, timezone

from app.services.tuya_client import TuyaPulsarClient, MultiAccountTuyaClient, get_tuya_accounts
from app.services.database import (
    init_db, save_properties_to_db, save_weather_data,
    log_fault, resolve_faults,
)
from app.services.analytics import decode_fault_bitmap
from app.services.notifier import (
    send_fault_alert, send_fault_resolved, send_communication_lost,
    send_daily_report,
)
from app.config import (
    LATITUDE, LONGITUDE, LOCATION_NAME,
    TELEGRAM_ENABLED, DAILY_REPORT_HOUR, HEAT_PUMP_DEV_ID,
    SERVER_TIMEZONE_OFFSET, ENERGY_METER_DEV_ID,
)

# Śledzenie ostatniego odbioru danych per urządzenie
_last_data_received: dict[str, float] = {}
COMM_LOST_THRESHOLD_SEC = 900  # 15 minut bez danych = alert
_comm_lost_alerted: set[str] = set()  # urządzenia z aktywnym alertem utraty


def save_with_fault_detection(dev_id: str, properties: list, event_time: int = None) -> bool:
    """Wrapper na save_properties_to_db — wykrywanie awarii + alerty Telegram."""
    saved = save_properties_to_db(dev_id, properties, event_time)

    if not event_time:
        event_time = int(time.time())

    # Rejestruj odbiór danych (do wykrywania utraty komunikacji)
    _last_data_received[dev_id] = time.time()
    if dev_id in _comm_lost_alerted:
        _comm_lost_alerted.discard(dev_id)
        print(f"[{time.strftime('%H:%M:%S')}] Komunikacja przywrocona: {dev_id}", flush=True)

    # Sprawdź czy w tej paczce jest parametr 'fault'
    for item in properties:
        if item.get("code") == "fault":
            fault_val = item.get("value", 0)
            if isinstance(fault_val, (int, float)):
                active_codes = decode_fault_bitmap(fault_val)
                bitmap_int = int(fault_val)

                # Loguj nowe awarie do bazy
                for code in active_codes:
                    log_fault(dev_id, code, bitmap_int, event_time)

                # Rozwiąż awarie, które zniknęły z bitmapy
                resolved = resolve_faults(dev_id, active_codes, event_time)

                if active_codes:
                    print(f"[{time.strftime('%H:%M:%S')}] !! AWARIA {dev_id}: {', '.join(active_codes)} (bitmap={bitmap_int})", flush=True)
                    send_fault_alert(dev_id, active_codes, bitmap_int)
                if resolved:
                    print(f"[{time.strftime('%H:%M:%S')}] OK Rozwiazano {dev_id}: {', '.join(resolved)}", flush=True)
                    send_fault_resolved(dev_id, resolved)
            break

    return saved


def communication_watchdog_loop():
    """Wątek sprawdzający czy pompa wysyła dane. Alert po 15 min ciszy."""
    print("Uruchomiono watchdog komunikacji (prog: 15 min)", flush=True)

    while True:
        time.sleep(60)  # sprawdzaj co minutę
        now = time.time()

        for dev_id, last_ts in list(_last_data_received.items()):
            # Licznik energii pomijany: w postoju (0 W, brak przyrostu) Tuya nie
            # przysyła ramek nawet przez 1-2 h — heartbeat nie tworzy zapisów, więc
            # cisza jest normalna, nie oznacza utraty komunikacji.
            if dev_id == ENERGY_METER_DEV_ID:
                continue

            silent_sec = now - last_ts

            if silent_sec >= COMM_LOST_THRESHOLD_SEC and dev_id not in _comm_lost_alerted:
                minutes = int(silent_sec / 60)
                print(f"[{time.strftime('%H:%M:%S')}] !! Utrata komunikacji {dev_id}: {minutes} min", flush=True)
                send_communication_lost(dev_id, minutes)
                _comm_lost_alerted.add(dev_id)


def daily_report_loop():
    """Wątek wysyłający raport dzienny o ustalonej godzinie."""
    if not TELEGRAM_ENABLED:
        print("Telegram wylaczony -- raport dzienny nieaktywny", flush=True)
        return

    # Godzina wysyłki liczona w UTC (niezależnie od strefy procesu na serwerze).
    # Czas lokalny = UTC + SERVER_TIMEZONE_OFFSET  =>  UTC = lokalny - offset.
    # Wartość SERVER_TIMEZONE_OFFSET jest obliczana dynamicznie przez get_timezone_offset()
    # w config.py (używa zoneinfo), co zapewnia automatyczne DST.
    utc_hour = (DAILY_REPORT_HOUR - SERVER_TIMEZONE_OFFSET) % 24
    print(f"Uruchomiono watek raportu dziennego (lokalnie: {DAILY_REPORT_HOUR}:00, UTC: {utc_hour}:00, offset: {SERVER_TIMEZONE_OFFSET}h)", flush=True)
    last_report_date = None

    while True:
        now = datetime.now(timezone.utc)

        # Porównuj z godziną UTC; data też w UTC by nie wysłać dwa razy
        if now.hour == utc_hour and now.date() != last_report_date:
            print(f"[{now.strftime('%H:%M:%S')} UTC] Generowanie raportu dziennego...", flush=True)

            # Raport dla głównego urządzenia
            send_daily_report(HEAT_PUMP_DEV_ID)

            # Raporty dla pozostałych urządzeń (jeśli były aktywne)
            for dev_id in list(_last_data_received.keys()):
                if dev_id != HEAT_PUMP_DEV_ID:
                    send_daily_report(dev_id)

            last_report_date = now.date()

        # Sprawdzaj co 5 minut
        time.sleep(300)


def fetch_weather_loop():
    """Wątek pobierający dane pogodowe z API Open-Meteo co godzinę."""
    import requests
    
    print(f"Uruchomiono wątek pogodowy dla lokalizacji: {LOCATION_NAME} ({LATITUDE}, {LONGITUDE})", flush=True)
    
    while True:
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": LATITUDE,
                "longitude": LONGITUDE,
                "current": ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "precipitation",
                            "direct_radiation", "diffuse_radiation"],
                "timezone": "auto"
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            current = data.get("current", {})
            
            timestamp = int(time.time())
            temperature = current.get("temperature_2m")
            humidity = current.get("relative_humidity_2m")
            windspeed = current.get("wind_speed_10m")
            precipitation = current.get("precipitation")
            direct_radiation = current.get("direct_radiation")
            diffuse_radiation = current.get("diffuse_radiation")
            
            if temperature is not None:
                save_weather_data(
                    timestamp=timestamp,
                    temperature=temperature,
                    humidity=humidity or 0.0,
                    windspeed=windspeed or 0.0,
                    precipitation=precipitation or 0.0,
                    latitude=LATITUDE,
                    longitude=LONGITUDE,
                    direct_radiation=direct_radiation,
                    diffuse_radiation=diffuse_radiation,
                )
                rad_info = f", rad={direct_radiation}W/m2" if direct_radiation is not None else ""
                print(f"Zapisano dane pogodowe: temp={temperature}C{rad_info}", flush=True)
            else:
                print("Blad: Brak danych temperatury w odpowiedzi API", flush=True)
                
        except requests.exceptions.RequestException as e:
            print(f"Blad polaczenia z Open-Meteo: {e}", flush=True)
        except Exception as e:
            print(f"Nieoczekiwany blad w watku pogodowym: {e}", flush=True)
        
        # Czekaj 1 godzinę przed następnym pobraniem
        time.sleep(3600)


def main():
    # Inicjalizacja struktury bazy danych SQLite przy starcie
    init_db()

    if TELEGRAM_ENABLED:
        print("Powiadomienia Telegram: WLACZONE", flush=True)
    else:
        print("Powiadomienia Telegram: WYLACZONE (brak TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)", flush=True)

    # Uruchom wątek pogodowy
    weather_thread = threading.Thread(target=fetch_weather_loop, daemon=True)
    weather_thread.start()

    # Uruchom watchdog komunikacji
    comm_thread = threading.Thread(target=communication_watchdog_loop, daemon=True)
    comm_thread.start()

    # Uruchom wątek raportu dziennego
    report_thread = threading.Thread(target=daily_report_loop, daemon=True)
    report_thread.start()

    # Pobierz skonfigurowane konta Tuya
    accounts = get_tuya_accounts()
    
    if not accounts:
        print("BLAD: Brak skonfigurowanych kont Tuya!", flush=True)
        print("Skonfiguruj zmienne srodowiskowe:", flush=True)
        print("  - TUYA_ACCESS_ID i TUYA_ACCESS_KEY (pojedyncze konto)", flush=True)
        print("  - lub TUYA_ACCOUNTS_JSON (wiele kont w formacie JSON)", flush=True)
        return

    print(f"Znaleziono {len(accounts)} skonfigurowanych kont Tuya.", flush=True)

    if len(accounts) == 1:
        # Pojedyncze konto - użyj prostszego klienta
        print("Uruchamianie w trybie pojedynczego konta...", flush=True)
        client = TuyaPulsarClient(accounts[0])
        client.connect()
        
        try:
            client.listen(save_with_fault_detection)
        except KeyboardInterrupt:
            print("Zatrzymano nasluchiwanie.")
        finally:
            client.close()
    else:
        # Wiele kont - użyj klienta wielokontowego
        print("Uruchamianie w trybie wielu kont...", flush=True)
        multi_client = MultiAccountTuyaClient()
        
        for account in accounts:
            multi_client.add_account(account)
        
        try:
            multi_client.start_listening(save_with_fault_detection)
        except KeyboardInterrupt:
            print("Zatrzymano nasluchiwanie.")
        finally:
            multi_client.close_all()


if __name__ == "__main__":
    main()

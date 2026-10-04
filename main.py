import time
import threading
from datetime import datetime, timezone

from tuya_connector import TuyaOpenAPI

from app.services.tuya_client import TuyaPulsarClient, MultiAccountTuyaClient, get_tuya_accounts
from app.services.database import (
    init_db, save_properties_to_db, save_weather_data,
    log_fault, resolve_faults,
)
from app.services.analytics import decode_fault_bitmap
from app.core.physics import is_pump_running
from app.services.notifier import (
    send_fault_alert, send_fault_resolved, send_communication_lost,
    send_daily_report,
)
from app.config import (
    LATITUDE, LONGITUDE, LOCATION_NAME,
    TELEGRAM_ENABLED, DAILY_REPORT_HOUR, HEAT_PUMP_DEV_ID,
    SERVER_TIMEZONE_OFFSET, ENERGY_METER_DEV_ID,
    TUYA_ACCOUNTS, FLOW_RATE_ON_THRESHOLD,
    THERMO_DEV_IDS,
    list_pumps,
)

# Śledzenie ostatniego odbioru danych per urządzenie
_last_data_received: dict[str, float] = {}
COMM_LOST_THRESHOLD_SEC = 900  # 15 minut bez danych = alert
_comm_lost_alerted: set[str] = set()  # urządzenia z aktywnym alertem utraty

# ... istniejące importy ...

# Śledzenie stanu pompy ciepła
_heat_pump_active = False
# Interwały
ENERGY_METER_POLL_INTERVAL_ACTIVE = 30   # Gdy pracuje (30 sek)
ENERGY_METER_POLL_INTERVAL_IDLE = 300     # Gdy odpoczywa (5 min)


def energy_meter_poll_loop(account_config: dict):
    """Wątek inteligentnego pollingu licznika energii."""
    # Inicjalizacja Tuya OpenAPI
    access_id = account_config.get("access_id")
    access_key = account_config.get("access_key")
    
    if not access_id or not access_key:
        print(f"[POLL] Błąd: brak access_id/access_key w konfiguracji", flush=True)
        return
    
    # Endpoint dla EU (zgodnie z tuya_client.py)
    endpoint = "https://openapi.tuyaeu.com"
    openapi = TuyaOpenAPI(endpoint, access_id, access_key)
    openapi.connect()
    
    print(f"Uruchomiono inteligentny wątek licznika {ENERGY_METER_DEV_ID}", flush=True)
    
    while True:
        try:
            # Określ interwał na podstawie stanu pompy
            current_interval = ENERGY_METER_POLL_INTERVAL_ACTIVE if _heat_pump_active else ENERGY_METER_POLL_INTERVAL_IDLE

            # Log wysłania requestu — widoczne w logach kiedy i z jakim interwałem pytamy
            pump_state = "PRACUJE" if _heat_pump_active else "postoj"
            print(f"[{time.strftime('%H:%M:%S')}] [POLL] -> GET status licznika {ENERGY_METER_DEV_ID} (interwal {current_interval}s, pompa: {pump_state})", flush=True)

            # Pobieraj dane tylko jeśli pompa pracuje LUB minął długi czas bezczynności
            # (zawsze warto mieć chociaż jeden punkt na kilka minut)
            res = openapi.get(f"/v1.0/iot-03/devices/{ENERGY_METER_DEV_ID}/status")

            # Log odpowiedzi — sprawdzenie czy dane trafiają (porównanie z Pulsar)
            if res is None:
                print(f"[{time.strftime('%H:%M:%S')}] [POLL] <- brak odpowiedzi (None)", flush=True)
            elif res.get("success"):
                result = res.get("result", [])
                # result to lista {'code': ..., 'value': ...} — wypisz w czytelnej formie
                pairs = ", ".join(f"{it.get('code')}={it.get('value')}" for it in result) if isinstance(result, list) else str(result)
                print(f"[{time.strftime('%H:%M:%S')}] [POLL] <- OK: {pairs}", flush=True)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] [POLL] <- BLAD Tuya: code={res.get('code')} msg={res.get('msg')}", flush=True)

            if res and not res.get("success"):
                if res.get("code") in [1010, 1011]:
                    openapi.connect()

        except Exception as e:
            print(f"[POLL] Błąd: {e}")

        time.sleep(current_interval)



def save_with_fault_detection(dev_id: str, properties: list, event_time: int = None) -> bool:
    """Wrapper na save_properties_to_db — wykrywanie awarii + alerty Telegram.

    Dodatkowo śledzi czy pompa PRACUJE (agregat) na podstawie pompy wody
    (flow_rate) przez is_pump_running() — to steruje interwałem pollingu licznika.
    Świadomie NIE po comp_freq: pompa wody rusza ~2 min przed sprężarką i pracuje
    ~2 min po jej zatrzymaniu, więc flow_rate obejmuje pełny cykl pracy agregatu.
    """
    global _heat_pump_active

    saved = save_properties_to_db(dev_id, properties, event_time)

    if not event_time:
        event_time = int(time.time())

    # Rejestruj odbiór danych (do wykrywania utraty komunikacji)
    _last_data_received[dev_id] = time.time()
    if dev_id in _comm_lost_alerted:
        _comm_lost_alerted.discard(dev_id)
        print(f"[{time.strftime('%H:%M:%S')}] Komunikacja przywrocona: {dev_id}", flush=True)

    # Śledzenie czy pompa pracuje — po pompie wody (flow_rate), kanonicznie
    if dev_id == HEAT_PUMP_DEV_ID:
        for item in properties:
            if item.get("code") == "flow_rate":
                new_active = is_pump_running(item.get("value"), FLOW_RATE_ON_THRESHOLD)
                if _heat_pump_active != new_active:
                    status_str = "START" if new_active else "STOP"
                    print(f"[{time.strftime('%H:%M:%S')}] Wykryto {status_str} pompy (flow_rate). Zmiana interwalu probkowania.", flush=True)
                _heat_pump_active = new_active
                break  # wystarczy jeden flow_rate w paczce

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

            # Zewnętrzny termometr pomijany: czujnik temperatury raportuje rzadko
            # (tylko przy zmianie wartości), więc dłuższa cisza jest normalna i nie
            # oznacza utraty komunikacji — jak licznik energii.
            if dev_id in THERMO_DEV_IDS:
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

            # Raport per POMPA (z listy PUMPS) — każda pompa dostaje własny raport
            # (osobny SCOP/energia/licznik/awarie, liczone dla jej device_id).
            # Iterujemy po skonfigurowanych pompach, NIE po _last_data_received —
            # dzięki temu licznik energii (i inne urządzenia) nie dostają raportu.
            # Pompa bez danych za wczoraj → build_daily_report zwróci None (nie wysyła).
            for pump in list_pumps():
                send_daily_report(pump["device_id"])

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

    # Pobierz skonfigurowane konta Tuya (wcześniej niż wątki)
    accounts = get_tuya_accounts()
    
    if not accounts:
        print("BLAD: Brak skonfigurowanych kont Tuya!", flush=True)
        print("Skonfiguruj zmienne srodowiskowe:", flush=True)
        print("  - TUYA_ACCESS_ID i TUYA_ACCESS_KEY (pojedyncze konto)", flush=True)
        print("  - lub TUYA_ACCOUNTS_JSON (wiele kont w formacie JSON)", flush=True)
        return

    print(f"Znaleziono {len(accounts)} skonfigurowanych kont Tuya.", flush=True)

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

    # Wątek REST-owego pollingu licznika WYŁĄCZONY (2026-09-09).
    # Powód: Cloud API zwraca 28841004 "No permissions. Your quota of Trial Edition
    # is used up." — projekt Tuya IoT na Trial Edition wyczerpał limit/okres próbny.
    # Dane licznika (add_ele, cur_power, cur_voltage, cur_current) i tak przychodzą
    # przez Pulsar (potwierdzone), a wynik pollingu był porzucany — wątek tylko
    # generował błędy. Zostawiamy funkcję energy_meter_poll_loop() na wypadek
    # przywrócenia płatnego planu API (wtedy odkomentować blok poniżej).
    print(f"Watek pollingu licznika {ENERGY_METER_DEV_ID}: WYLACZONY (Cloud API Trial wyczerpany; dane ida przez Pulsar).", flush=True)
    # if accounts:
    #     energy_thread = threading.Thread(
    #         target=energy_meter_poll_loop,
    #         args=(accounts[0],),
    #         daemon=True
    #     )
    #     energy_thread.start()
    #     print(f"Uruchomiono wątek pollingu licznika {ENERGY_METER_DEV_ID}", flush=True)

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

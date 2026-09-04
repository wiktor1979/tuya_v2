"""Klient Tuya Pulsar - obsługa połączenia i deszyfrowanie."""
import json
import base64
import hashlib
import time
from typing import Optional, Dict, Any, List
from Crypto.Cipher import AES
import pulsar

from app.config import (
    TUYA_ACCOUNTS, PULSAR_SERVER_EU, 
    MQ_ENV_PROD, TEMP_CODES, HISTERESIS_CONFIG, MAX_HEARTBEAT_SEC,
    ENERGY_METER_DEV_ID,
)


# Próg deduplikacji add_ele [s]. Licznik Tuya wysyła każdy raport add_ele
# PODWOJONY (ta sama wartość, ts ±1 s) — retransmisja po stronie urządzenia/bramki.
# Realne raporty add_ele dzieli ~1800 s (30 min), więc 3 s bezpiecznie odsiewa
# tylko duplikat, nie tnąc prawdziwych przyrostów.
ADD_ELE_DEDUP_SEC = 3


def get_tuya_accounts() -> List[Dict[str, Any]]:
    """Zwraca listę skonfigurowanych kont Tuya."""
    return TUYA_ACCOUNTS


class DeadbandFilter:
    """Filtr deadband dla telemetrii - obsługa dynamicznej histerezy."""
    
    __slots__ = ['last_saved_val', 'last_saved_time', 'last_add_ele_time']

    # Parametry bez heartbeatu — zapisywane TYLKO gdy wartość się zmieni.
    # Flagi binarne i rzadko zmieniające się stany (99-100% duplikatów przy heartbeat).
    NO_HEARTBEAT_CODES = frozenset({
        "ac_fan", "dc_fan2", "defrost", "fault_flag", "freeze", "protect_flag",
        "pump_sta", "valve",
    })
    
    def __init__(self):
        self.last_saved_val: Dict[str, Any] = {}
        self.last_saved_time: Dict[str, float] = {}
        # Czas zdarzenia (event_time z ramki) ostatnio zapisanego add_ele.
        # Służy deduplikacji podwojonych ramek add_ele (retransmisja Tuya, ts ±1 s).
        self.last_add_ele_time: float = 0.0
    
    def should_save(self, code: str, new_val: Any, compressor_status: int = 0) -> bool:
        """
        Decyduje, czy dana wartość parametru powinna zostać zapisana do bazy.
        Używa dynamicznej histerezy zależnej od statusu sprężarki.
        
        Args:
            code: Nazwa parametru (kod z Tuya).
            new_val: Nowa odczytana wartość.
            compressor_status: Status sprężarki (0 = idle, >0 = active).
        """
        now = time.time()
        
        # 1. Pierwszy odczyt w historii -> zapisz
        if code not in self.last_saved_val:
            self.last_saved_val[code] = new_val
            self.last_saved_time[code] = now
            return True
        
        # 2. Heartbeat: upłynęło 5 minut od ostatniego zapisu tego parametru -> zapisz
        #    WYJĄTEK: flagi binarne i rzadko zmieniające się stany — bez heartbeatu
        if code not in self.NO_HEARTBEAT_CODES:
            if (now - self.last_saved_time[code]) >= MAX_HEARTBEAT_SEC:
                self.last_saved_val[code] = new_val
                self.last_saved_time[code] = now
                return True

        old_val = self.last_saved_val[code]

        # 3. BARDZO WAŻNE: Jeśli wartość jest DOKŁADNIE taka sama -> IGNORUJ
        if new_val == old_val:
            return False

        # 4. Sprawdzanie progu dla rzeczywistych liczb (z wykluczeniem booleanów!)
        if isinstance(new_val, (int, float)) and not isinstance(new_val, bool):
            if isinstance(old_val, (int, float)) and not isinstance(old_val, bool):
                # Pobierz konfigurację histerezy dla tego parametru
                config_entry = HISTERESIS_CONFIG.get(code)
                
                if config_entry:
                    # Wybierz próg w zależności od statusu sprężarki
                    threshold = config_entry['active'] if compressor_status > 0 else config_entry['idle']
                else:
                    # Fallback dla parametrów spoza konfiguracji
                    threshold = 0.0
                
                # Jeśli różnica jest mniejsza niż próg -> IGNORUJ
                if abs(new_val - old_val) < threshold:
                    return False

        # 5. Jeśli wartość się zmieniła i przeszła próg -> ZAPISZ
        self.last_saved_val[code] = new_val
        self.last_saved_time[code] = now
        return True


def get_authentication(access_id: str, access_key: str) -> pulsar.AuthenticationBasic:
    """Generuje autoryzację MD5 wymaganą przez serwer Pulsar Tuya."""
    md5_access_key = hashlib.md5(access_key.encode('utf-8')).hexdigest()
    combined = access_id + md5_access_key
    md5_combined = hashlib.md5(combined.encode('utf-8')).hexdigest()
    
    password = '"' + md5_combined[8:24] + '"}'
    user_name = '{{"username": "{}","password"'.format(access_id)
    return pulsar.AuthenticationBasic(user_name, password, "auth1")


def decrypt_by_gcm(raw_bytes: bytes, key_bytes: bytes) -> str:
    """Deszyfrowanie AES-GCM."""
    nonce = raw_bytes[:12]
    ciphertext = raw_bytes[12:-16]
    auth_tag = raw_bytes[-16:]
    aes_cipher = AES.new(key_bytes, AES.MODE_GCM, nonce)
    return aes_cipher.decrypt_and_verify(ciphertext, auth_tag).decode('utf-8')


def decrypt_by_ecb(raw_bytes: bytes, key_bytes: bytes) -> str:
    """Deszyfrowanie AES-ECB."""
    cipher = AES.new(key_bytes, AES.MODE_ECB)
    decrypted_data = cipher.decrypt(raw_bytes)
    res_str = decrypted_data.decode('utf-8')
    return res_str.replace('\r', '').replace('\n', '').replace('\f', '')


def decrypt_by_aes(raw: str, key: str, decrypt_model: str) -> str:
    """Wybiera odpowiedni algorytm deszyfrowania."""
    raw_bytes = base64.b64decode(raw)
    key_bytes = key[8:24].encode('utf-8')

    if decrypt_model == "aes_gcm":
        return decrypt_by_gcm(raw_bytes, key_bytes)
    else:
        return decrypt_by_ecb(raw_bytes, key_bytes)


def decrypt_message(pulsar_message: pulsar.Message, access_key: str) -> str:
    """Wyciąga dane z ramki Pulsar i wywołuje deszyfrowanie."""
    payload = pulsar_message.data().decode('utf-8')
    decrypt_model = pulsar_message.properties().get("em")
    
    data_json = json.loads(payload)
    encrypt_data = data_json['data']
    return decrypt_by_aes(encrypt_data, access_key, decrypt_model)


def message_id(msg_id: pulsar.MessageId) -> str:
    """Formatuje ID wiadomości Pulsar."""
    return f"{msg_id.ledger_id()}:{msg_id.entry_id()}:{msg_id.partition()}:{msg_id.batch_index()}"


class TuyaPulsarClient:
    """Klient do obsługi połączenia z Tuya Pulsar - obsługa wielu kont."""
    
    def __init__(self, account_config: Optional[Dict[str, Any]] = None):
        """
        Inicjalizacja klienta dla konkretnego konta Tuya.
        
        Args:
            account_config: Słownik z kluczami: access_id, access_key, devices (opcjonalnie lista ID)
        """
        if account_config is None:
            raise ValueError("Brak konfiguracji konta Tuya!")
        
        self.access_id = account_config.get("access_id")
        self.access_key = account_config.get("access_key")
        self.monitored_devices = account_config.get("devices", [])
        
        if not self.access_id or not self.access_key:
            raise ValueError("Brak kluczy access_id / access_key w konfiguracji konta!")
        
        self.filter = DeadbandFilter()
        self.client: Optional[pulsar.Client] = None
        self.consumer: Optional[pulsar.Consumer] = None
    
    def connect(self) -> None:
        """Łączy się z serwerem Tuya Pulsar dla tego konta."""
        print(f"Łączenie z serwerem Tuya Pulsar (EU) dla konta: {self.access_id}...", flush=True)

        self.client = pulsar.Client(
            PULSAR_SERVER_EU,
            authentication=get_authentication(self.access_id, self.access_key),
            tls_allow_insecure_connection=True,
        )

        topic = f"{self.access_id}/out/{MQ_ENV_PROD}"
        subscription_name = f"{self.access_id}-sub"

        self.consumer = self.client.subscribe(
            topic,
            subscription_name,
            consumer_type=pulsar.ConsumerType.Failover
        )

        devices_info = f" (monitorowane urządzenia: {', '.join(self.monitored_devices)})" if self.monitored_devices else " (wszystkie urządzenia)"
        print(f"Połączono pomyślnie! Subskrypcja tematu: {topic}{devices_info}", flush=True)
        print("Oczekiwanie na zdarzenia z pompy ciepła (z włączonym filtrem Deadband)...\n", flush=True)
    
    def handle_parsed_payload(self, decrypted_json_str: str, save_callback) -> None:
        """Przetwarza odszyfrowaną wiadomość i zapisuje dane przez callback."""
        try:
            data = json.loads(decrypted_json_str)
            biz_data = data.get("bizData", {}) if isinstance(data.get("bizData"), dict) else {}
            
            dev_id = biz_data.get("devId") or data.get("devId")
            status_list = (
                biz_data.get("properties") or 
                biz_data.get("status") or 
                data.get("status") or []
            )
            
            raw_ts = data.get("ts") or biz_data.get("ts")
            event_time = int(raw_ts / 1000) if raw_ts else int(time.time())

            # Filtruj urządzenia jeśli lista monitorowanych jest określona.
            # Licznik energii ZAWSZE przepuszczany, niezależnie od listy.
            if (self.monitored_devices
                    and dev_id not in self.monitored_devices
                    and dev_id != ENERGY_METER_DEV_ID):
                return  # Ignoruj urządzenia spoza listy monitorowanych

            if dev_id and status_list:
                # Pobierz status sprężarki z tej samej wiadomości, jeśli dostępny
                compressor_status = 0
                for item in status_list:
                    if item.get("code") == "comp_freq":
                        c_val = item.get("value", 0)
                        if isinstance(c_val, (int, float)) and c_val > 0:
                            compressor_status = 1
                        break
                
                filtered_status_list = []

                # Licznik energii — wariant minimalny:
                #   - add_ele  : przyrost energii, zapisywany ZAWSZE (bypass filtra,
                #                inaczej powtórzone przyrosty zostałyby zgubione).
                #   - cur_power: moc czynna [W], przez DeadbandFilter (histereza ~2 W).
                #   - cur_voltage / cur_current: POMIJANE (tylko diagnostyka, nie energia).
                if dev_id == ENERGY_METER_DEV_ID:
                    for item in status_list:
                        code = item.get("code")
                        if code == "add_ele":
                            # Deduplikacja retransmisji: Tuya wysyła każdy raport add_ele
                            # podwojony (ts ±1 s). Pomiń, jeśli od ostatniego zapisanego
                            # add_ele minęło < ADD_ELE_DEDUP_SEC. Realne raporty dzieli
                            # ~1800 s, więc próg 3 s odsiewa tylko duplikat.
                            if (event_time - self.filter.last_add_ele_time) < ADD_ELE_DEDUP_SEC:
                                continue
                            self.filter.last_add_ele_time = event_time
                            filtered_status_list.append(item)
                        elif code == "cur_power":
                            if self.filter.should_save(code, item.get("value"), compressor_status):
                                filtered_status_list.append(item)
                        # pozostałe kody licznika ignorujemy
                else:
                    for item in status_list:
                        code = item.get("code")
                        val = item.get("value")

                        # Przeliczenie wartości do testu (temperatury / 10)
                        check_val = val
                        if code in TEMP_CODES and isinstance(val, (int, float)) and not isinstance(val, bool):
                            check_val = val / 10.0

                        if self.filter.should_save(code, check_val, compressor_status):
                            filtered_status_list.append(item)

                if filtered_status_list:
                    is_saved = save_callback(dev_id, filtered_status_list, event_time)
                    
                    if is_saved:
                        saved_codes = [f"{i['code']}={i['value']}" for i in filtered_status_list]
                        print(f"[{time.strftime('%H:%M:%S')}] {dev_id}: Zapisano ({len(filtered_status_list)}/{len(status_list)}): {', '.join(saved_codes)}", flush=True)

        except Exception as e:
            print(f"Błąd przetwarzania/zapisu ramki: {e}", flush=True)
    
    def listen(self, save_callback) -> None:
        """Nasłuchuje wiadomości z Pulsar i przetwarza je."""
        if not self.consumer:
            raise RuntimeError("Najpierw wywołaj connect()")
        
        while True:
            try:
                pulsar_message = self.consumer.receive()
                decrypted_msg = decrypt_message(pulsar_message, self.access_key)
                
                self.handle_parsed_payload(decrypted_msg, save_callback)
                
                self.consumer.acknowledge_cumulative(pulsar_message)
            except pulsar.Interrupted:
                print("Zatrzymano nasłuchiwanie.")
                break
            except Exception as e:
                print(f"Błąd podczas przetwarzania ramki: {e}", flush=True)
    
    def close(self) -> None:
        """Zamyka połączenie z Pulsar."""
        if self.client:
            self.client.close()


class MultiAccountTuyaClient:
    """Zarządza wieloma klientami TuyaPulsarClient dla różnych kont."""
    
    def __init__(self):
        self.clients: List[TuyaPulsarClient] = []
        self.threads: List = []
    
    def add_account(self, account_config: Dict[str, Any]) -> None:
        """Dodaje nowe konto Tuya do monitorowania."""
        client = TuyaPulsarClient(account_config)
        self.clients.append(client)
        print(f"Dodano konto Tuya: {account_config.get('access_id')}", flush=True)
    
    def start_listening(self, save_callback) -> None:
        """Uruchamia nasłuchiwanie na wszystkich kontach w osobnych wątkach."""
        import threading
        
        if not self.clients:
            raise RuntimeError("Brak skonfigurowanych kont Tuya!")
        
        print(f"Uruchamianie nasłuchiwania na {len(self.clients)} kontach Tuya...", flush=True)
        
        for client in self.clients:
            client.connect()
            thread = threading.Thread(target=client.listen, args=(save_callback,), daemon=True)
            thread.start()
            self.threads.append(thread)
        
        # Główny wątek czeka na wszystkie wątki klienckie
        for thread in self.threads:
            thread.join()
    
    def close_all(self) -> None:
        """Zamyka wszystkie połączenia z Pulsar."""
        for client in self.clients:
            client.close()

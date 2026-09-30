"""Testy collectora Tuya — deduplikacja add_ele.

Licznik Tuya wysyła każdy raport add_ele podwojony (ta sama wartość, ts ±1 s).
Collector musi zapisać tylko jedną ramkę na raport, ale nie tnąć realnych
przyrostów oddalonych o ~1800 s.
"""
import json

from app.config import ENERGY_METER_DEV_ID
from app.services.tuya_client import (
    ADD_ELE_DEDUP_SEC,
    TuyaPulsarClient,
)


def _frame(dev_id: str, code: str, value, ts_ms: int) -> str:
    """Buduje odszyfrowany JSON ramki Pulsar (jak po decrypt_message)."""
    return json.dumps({
        "devId": dev_id,
        "ts": ts_ms,
        "status": [{"code": code, "value": value}],
    })


def _make_client() -> TuyaPulsarClient:
    return TuyaPulsarClient({
        "access_id": "test",
        "access_key": "testkey",
        "devices": [],
    })


class TestAddEleDedup:
    """Deduplikacja podwojonych ramek add_ele po event_time."""

    def _collector(self):
        client = _make_client()
        saved: list = []

        def save_cb(dev_id, properties, event_time):
            for item in properties:
                saved.append((event_time, item["code"], item["value"]))
            return True

        return client, saved, save_cb

    def test_duplicate_within_threshold_skipped(self) -> None:
        """Druga ramka add_ele w ±1 s (retransmisja) jest pomijana."""
        client, saved, cb = self._collector()
        base_ms = 1_788_466_216_000
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, base_ms), cb)
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, base_ms + 1000), cb)

        add_ele_saved = [s for s in saved if s[1] == "add_ele"]
        assert len(add_ele_saved) == 1

    def test_duplicate_same_second_skipped(self) -> None:
        """Druga ramka add_ele w tej samej sekundzie (dts=0) jest pomijana."""
        client, saved, cb = self._collector()
        base_ms = 1_788_496_141_000
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, base_ms), cb)
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, base_ms), cb)

        add_ele_saved = [s for s in saved if s[1] == "add_ele"]
        assert len(add_ele_saved) == 1

    def test_real_reports_kept(self) -> None:
        """Realne raporty add_ele (~1800 s odstępu) są zachowane oba."""
        client, saved, cb = self._collector()
        base_ms = 1_788_466_216_000
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, base_ms), cb)
        # duplikat pierwszego — pominięty
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, base_ms + 1000), cb)
        # kolejny realny raport 30 min później
        next_ms = base_ms + 1800 * 1000
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, next_ms), cb)

        add_ele_saved = [s for s in saved if s[1] == "add_ele"]
        assert len(add_ele_saved) == 2

    def test_threshold_boundary(self) -> None:
        """Ramka dokładnie w ADD_ELE_DEDUP_SEC nie jest już duplikatem (>= próg)."""
        client, saved, cb = self._collector()
        base_ms = 1_788_466_216_000
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, base_ms), cb)
        # dokładnie próg sekund później — powinna zostać zapisana (warunek: < próg = skip)
        later_ms = base_ms + ADD_ELE_DEDUP_SEC * 1000
        client.handle_parsed_payload(_frame(ENERGY_METER_DEV_ID, "add_ele", 2, later_ms), cb)

        add_ele_saved = [s for s in saved if s[1] == "add_ele"]
        assert len(add_ele_saved) == 2



class TestDeadbandPerDevice:
    """Stan histerezy per (device_id, code) — regresja dwóch pomp na jednym filtrze.

    Bug (2026-09-27): DeadbandFilter kluczował stan po samym `code`. Dwie pompy
    o tych samych kodach (out_water_temp itd.), ale różnych wartościach,
    przeplatały ramki na wspólnym filtrze i wzajemnie nadpisywały last_saved_val
    → każda ramka przechodziła próg (~200× za dużo zapisów temperatur).
    """

    def test_two_devices_same_code_do_not_reset_each_other(self) -> None:
        """Przeplot 2 urządzeń (różne stałe wartości) → tylko starty przechodzą filtr."""
        from app.services.tuya_client import DeadbandFilter

        f = DeadbandFilter()
        # out_water_temp: pompa A stale 29.7, pompa B stale 21.8 (postój, comp=0)
        saved_a = saved_b = 0
        for _ in range(100):
            if f.should_save("devA", "out_water_temp", 29.7, 0):
                saved_a += 1
            if f.should_save("devB", "out_water_temp", 21.8, 0):
                saved_b += 1

        # Każde urządzenie: tylko pierwszy odczyt (potem identyczna wartość → skip).
        # Bez per-device każda z 200 ramek przeszłaby (wzajemny reset).
        assert saved_a == 1
        assert saved_b == 1

    def test_same_code_independent_state(self) -> None:
        """Zmiana wartości u jednego urządzenia nie wpływa na drugie."""
        from app.services.tuya_client import DeadbandFilter

        f = DeadbandFilter()
        assert f.should_save("devA", "tank_temp", 43.2, 0) is True   # 1. odczyt A
        assert f.should_save("devB", "tank_temp", 55.0, 0) is True   # 1. odczyt B
        # Powtórzenie tej samej wartości A — mimo że B ma inną — musi być pominięte
        assert f.should_save("devA", "tank_temp", 43.2, 0) is False
        assert f.should_save("devB", "tank_temp", 55.0, 0) is False


class TestAddEleDedupPerDevice:
    """Dedup add_ele per device_id — dwa liczniki mają niezależne strumienie."""

    def test_two_meters_independent_dedup_state(self) -> None:
        """Stan dedup add_ele jest osobny per device_id (dict)."""
        from app.services.tuya_client import DeadbandFilter

        f = DeadbandFilter()
        ts = 1_788_466_216
        # Licznik A i B raportują w tej samej chwili — nie są dla siebie duplikatem
        assert (ts - f.last_add_ele_time.get("meterA", 0.0)) >= ADD_ELE_DEDUP_SEC
        f.last_add_ele_time["meterA"] = ts
        assert (ts - f.last_add_ele_time.get("meterB", 0.0)) >= ADD_ELE_DEDUP_SEC
        f.last_add_ele_time["meterB"] = ts
        # Retransmisja licznika A (±1 s) — duplikat, ma być odrzucona
        assert (ts + 1 - f.last_add_ele_time["meterA"]) < ADD_ELE_DEDUP_SEC
        # Stan B nietknięty przez A
        assert f.last_add_ele_time["meterB"] == ts

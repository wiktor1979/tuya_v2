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

"""Testy config.py — obsługa wielu pomp (PUMPS, get_pump, list_pumps, zbiory ID)."""
from app import config


class TestListPumps:
    """list_pumps — kopia listy skonfigurowanych pomp."""

    def test_returns_all_pumps(self) -> None:
        pumps = config.list_pumps()
        assert len(pumps) == len(config.PUMPS)
        ids = [p["id"] for p in pumps]
        assert "pompa1" in ids

    def test_returns_copy_not_original(self) -> None:
        """Modyfikacja zwróconej listy nie zmienia PUMPS."""
        pumps = config.list_pumps()
        pumps[0]["name"] = "ZMIENIONE"
        assert config.PUMPS[0]["name"] != "ZMIENIONE"

    def test_each_pump_has_required_keys(self) -> None:
        for p in config.list_pumps():
            assert {"id", "name", "device_id", "meter_id", "thermo_id"} <= set(p.keys())


class TestGetPump:
    """get_pump — wyszukiwanie po id z fallbackiem na domyślną."""

    def test_get_existing_pump(self) -> None:
        p = config.get_pump("pompa1")
        assert p["id"] == "pompa1"
        assert p["device_id"] == config.HEAT_PUMP_DEV_ID

    def test_unknown_id_falls_back_to_first(self) -> None:
        p = config.get_pump("nieistnieje")
        assert p["id"] == config.PUMPS[0]["id"]

    def test_none_falls_back_to_first(self) -> None:
        p = config.get_pump(None)
        assert p["id"] == config.PUMPS[0]["id"]

    def test_second_pump_has_no_meter(self) -> None:
        """Druga pompa jest bez licznika (meter_id=None)."""
        p = config.get_pump("pompa2")
        assert p["meter_id"] is None

    def test_first_pump_has_thermo(self) -> None:
        """Pompa1 ma powiązany zewnętrzny termometr (thermo_id)."""
        p = config.get_pump("pompa1")
        assert p["thermo_id"] == "bf9134db09e1ea78cdskae"

    def test_second_pump_has_no_thermo(self) -> None:
        """Druga pompa jest bez zewnętrznego termometru (thermo_id=None)."""
        p = config.get_pump("pompa2")
        assert p["thermo_id"] is None


class TestDeviceIdSets:
    """Zbiory device_id — whitelist collectora i aliasy wsteczne."""

    def test_meter_ids_excludes_none(self) -> None:
        """ENERGY_METER_DEV_IDS zawiera tylko realne liczniki (bez None)."""
        assert None not in config.ENERGY_METER_DEV_IDS
        # Pompa1 ma licznik → jego meter_id jest w zbiorze.
        assert config.PUMPS[0]["meter_id"] in config.ENERGY_METER_DEV_IDS

    def test_heat_pump_ids_covers_all_pumps(self) -> None:
        assert config.HEAT_PUMP_DEV_IDS == frozenset(p["device_id"] for p in config.PUMPS)

    def test_thermo_ids_excludes_none(self) -> None:
        """THERMO_DEV_IDS zawiera tylko realne termometry (bez None)."""
        assert None not in config.THERMO_DEV_IDS
        # Pompa1 ma termometr → jego thermo_id jest w zbiorze.
        assert config.PUMPS[0]["thermo_id"] in config.THERMO_DEV_IDS
        # Pompa2 nie ma termometru → zbiór ma dokładnie jeden element.
        assert len(config.THERMO_DEV_IDS) == 1

    def test_thermo_temp_codes_scaled(self) -> None:
        """Kody temperatury termometru są w TEMP_CODES (dzielone ×0.1 przy zapisie)."""
        assert "va_temperature" in config.TEMP_CODES
        assert "temp_current" in config.TEMP_CODES

    def test_thermo_temp_has_label(self) -> None:
        """va_temperature ma etykietę w PARAM_INFO (widoczne na wykresie)."""
        assert "va_temperature" in config.PARAM_INFO
        # temp_current celowo BEZ etykiety — duplikat ukryty na wykresie.
        assert "temp_current" not in config.PARAM_INFO

    def test_backward_alias_is_first_pump(self) -> None:
        """Aliasy pojedyncze wskazują pierwszą pompę (kompatybilność wsteczna)."""
        assert config.HEAT_PUMP_DEV_ID == config.PUMPS[0]["device_id"]
        assert config.ENERGY_METER_DEV_ID == config.PUMPS[0]["meter_id"]


class TestDeviceNames:
    """get_device_name — czytelne nazwy dla pomp i liczników."""

    def test_pump_name(self) -> None:
        assert config.get_device_name(config.PUMPS[0]["device_id"]) == config.PUMPS[0]["name"]

    def test_thermo_name(self) -> None:
        """Termometr ma czytelną nazwę 'Termometr <pompa>'."""
        tid = config.PUMPS[0]["thermo_id"]
        assert config.get_device_name(tid) == f"Termometr {config.PUMPS[0]['name']}"

    def test_unknown_device_returns_id(self) -> None:
        assert config.get_device_name("cos_nieznanego") == "cos_nieznanego"

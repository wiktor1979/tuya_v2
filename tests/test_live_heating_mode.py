"""Testy get_live_heating_mode — co pompa grzeje TERAZ (CO/CWU/None).

Wizualizacja na Panelu (kafelek SCOP + paski temperatur) zależy od tej funkcji.
Klasyfikacja CO/CWU spójna z silnikiem (_live_is_cwu → _classify_cwu_mask).
"""
from app.ui.helpers import get_live_heating_mode, pump_supports_cwu
from app.config import COMP_FREQ_ON_THRESHOLD, CWU_TANK_DIFF_ON, COMBINED_WORK_MODE


def _status(comp_freq=0.0, work_mode=None, tank=None, hw_set=None):
    """Buduje snapshot statusu jak load_latest_status: {code: {val_num, val_str}}."""
    s = {"comp_freq": {"val_num": comp_freq, "val_str": None}}
    if work_mode is not None:
        s["work_mode"] = {"val_num": None, "val_str": work_mode}
    if tank is not None:
        s["tank_temp"] = {"val_num": tank, "val_str": None}
    if hw_set is not None:
        s["hot_water_temp_set"] = {"val_num": hw_set, "val_str": None}
    return s


class TestGetLiveHeatingMode:
    def test_compressor_off_returns_none(self):
        """Sprężarka stoi (comp_freq <= próg) → nic nie grzeje, None."""
        assert get_live_heating_mode(_status(comp_freq=0, work_mode="heat")) is None
        assert get_live_heating_mode(_status(comp_freq=COMP_FREQ_ON_THRESHOLD, work_mode="hot_water")) is None

    def test_heat_mode_is_co(self):
        """work_mode='heat' + sprężarka pracuje → CO."""
        assert get_live_heating_mode(_status(comp_freq=45, work_mode="heat")) == "co"

    def test_hot_water_mode_is_cwu(self):
        """work_mode='hot_water' + sprężarka pracuje → CWU."""
        assert get_live_heating_mode(_status(comp_freq=45, work_mode="hot_water")) == "cwu"

    def test_combined_mode_cwu_when_tank_cold(self):
        """Tryb łączony + zasobnik niedogrzany (różnica > próg) → CWU."""
        s = _status(comp_freq=45, work_mode=COMBINED_WORK_MODE,
                    tank=40.0, hw_set=40.0 + CWU_TANK_DIFF_ON + 1.0)
        assert get_live_heating_mode(s) == "cwu"

    def test_combined_mode_co_when_tank_warm(self):
        """Tryb łączony + zasobnik dogrzany (różnica <= próg) → CO."""
        s = _status(comp_freq=45, work_mode=COMBINED_WORK_MODE,
                    tank=48.0, hw_set=49.0)
        assert get_live_heating_mode(s) == "co"

    def test_unknown_work_mode_defaults_co(self):
        """Brak work_mode, ale sprężarka pracuje → domyślnie CO (nie None)."""
        assert get_live_heating_mode(_status(comp_freq=45)) == "co"


class TestPumpSupportsCwu:
    """pump_supports_cwu — pompa bez CWU ma czujnik zasobnika rozwarty (tank_temp < 0)."""

    def test_negative_tank_temp_no_cwu(self):
        """tank_temp ujemna (czujnik rozwarty, obserwowane -30) → brak CWU."""
        assert pump_supports_cwu(_status(tank=-30.0)) is False

    def test_positive_tank_temp_has_cwu(self):
        """tank_temp dodatnia (realny zasobnik) → obsługuje CWU."""
        assert pump_supports_cwu(_status(tank=43.1)) is True

    def test_zero_tank_temp_has_cwu(self):
        """Próg 0: tank_temp == 0 traktujemy jako CWU (>= 0)."""
        assert pump_supports_cwu(_status(tank=0.0)) is True

    def test_missing_tank_temp_defaults_true(self):
        """Brak danych tank_temp → domyślnie zakładamy obsługę CWU (bezpieczny default)."""
        assert pump_supports_cwu(_status()) is True

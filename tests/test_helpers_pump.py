"""Testy trwałości wyboru pompy — get_selected_pump / persist_pump_choice.

Sprawdzają kolejność priorytetów źródeł wyboru:
    query_params (URL) > session_state > localStorage (przeglądarka) > DEFAULT.

Streamlit (st) jest podmieniany na atrapę (FakeSt), a localStorage na FakeLS —
testujemy czystą logikę priorytetów bez runtime Streamlita.
"""
import pytest

from app import config
from app.ui import helpers


class FakeQueryParams:
    """Atrapa st.query_params — słownik z metodą get()."""

    def __init__(self) -> None:
        self._d: dict = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __getitem__(self, key):
        return self._d[key]

    def __setitem__(self, key, value):
        self._d[key] = value


class FakeSt:
    """Atrapa modułu streamlit z query_params i session_state."""

    def __init__(self) -> None:
        self.query_params = FakeQueryParams()
        self.session_state: dict = {}


class FakeLS:
    """Atrapa localStorage przeglądarki."""

    def __init__(self, initial=None) -> None:
        self._store: dict = dict(initial or {})

    def getItem(self, key):
        return self._store.get(key)

    def setItem(self, itemKey, itemValue, key=None, **kwargs):  # noqa: N803
        self._store[itemKey] = itemValue


@pytest.fixture
def fake_env(monkeypatch):
    """Podmienia st i localStorage w helpers; zwraca (fake_st, fake_ls)."""
    fst = FakeSt()
    fls = FakeLS()
    monkeypatch.setattr(helpers, "st", fst)
    monkeypatch.setattr(helpers, "_get_local_storage", lambda: fls)
    return fst, fls


class TestGetSelectedPumpPriority:
    """Kolejność odczytu źródeł wyboru pompy."""

    def test_default_when_all_empty(self, fake_env) -> None:
        fst, _ = fake_env
        pump = helpers.get_selected_pump()
        assert pump["id"] == config.DEFAULT_PUMP_ID

    def test_query_params_wins(self, fake_env) -> None:
        fst, fls = fake_env
        fst.query_params["pump"] = "pompa2"
        fst.session_state[helpers._SS_PUMP_KEY] = "pompa1"
        fls.setItem(helpers._LS_PUMP_KEY, "pompa1")
        assert helpers.get_selected_pump()["id"] == "pompa2"

    def test_session_state_when_no_url(self, fake_env) -> None:
        fst, fls = fake_env
        fst.session_state[helpers._SS_PUMP_KEY] = "pompa2"
        fls.setItem(helpers._LS_PUMP_KEY, "pompa1")
        assert helpers.get_selected_pump()["id"] == "pompa2"

    def test_localstorage_when_no_url_no_session(self, fake_env) -> None:
        """Kluczowe: przetrwanie zamknięcia przeglądarki — tylko localStorage ma wybór."""
        fst, fls = fake_env
        fls.setItem(helpers._LS_PUMP_KEY, "pompa2")
        assert helpers.get_selected_pump()["id"] == "pompa2"

    def test_invalid_id_falls_back_to_default(self, fake_env) -> None:
        fst, _ = fake_env
        fst.query_params["pump"] = "nieistnieje"
        assert helpers.get_selected_pump()["id"] == config.DEFAULT_PUMP_ID

    def test_syncs_back_to_session_and_url(self, fake_env) -> None:
        """Po odczycie z localStorage wybór trafia do session_state i URL."""
        fst, fls = fake_env
        fls.setItem(helpers._LS_PUMP_KEY, "pompa2")
        helpers.get_selected_pump()
        assert fst.session_state[helpers._SS_PUMP_KEY] == "pompa2"
        assert fst.query_params.get("pump") == "pompa2"


class TestPersistPumpChoice:
    """Zapis wyboru do wszystkich trzech warstw."""

    def test_writes_all_layers(self, fake_env) -> None:
        fst, fls = fake_env
        helpers.persist_pump_choice("pompa2")
        assert fst.session_state[helpers._SS_PUMP_KEY] == "pompa2"
        assert fst.query_params.get("pump") == "pompa2"
        assert fls.getItem(helpers._LS_PUMP_KEY) == "pompa2"

    def test_survives_browser_close_simulation(self, fake_env) -> None:
        """Symulacja: po zapisie czyścimy URL+session (nowa sesja), zostaje localStorage."""
        fst, fls = fake_env
        helpers.persist_pump_choice("pompa2")
        # Nowa sesja/otwarcie przeglądarki: URL i session puste, localStorage trwa.
        fst.query_params = FakeQueryParams()
        fst.session_state = {}
        assert helpers.get_selected_pump()["id"] == "pompa2"


class TestChartParams:
    """Konfiguracja wykresu 'Przebieg parametrów' — localStorage, zapis ręczny."""

    def test_default_when_empty(self, fake_env) -> None:
        """Brak zapisu → fallback na default."""
        _, _ = fake_env
        default = ["out_water_temp", "amb_temp"]
        assert helpers.get_chart_params(default) == default

    def test_roundtrip_save_read(self, fake_env) -> None:
        """Zapis → odczyt zwraca zapisaną listę (nie default)."""
        _, _ = fake_env
        helpers.persist_chart_params(["comp_freq", "flow_rate"])
        assert helpers.get_chart_params(["out_water_temp"]) == ["comp_freq", "flow_rate"]

    def test_broken_json_falls_back(self, fake_env) -> None:
        """Zepsuty JSON w localStorage → fallback na default."""
        _, fls = fake_env
        fls.setItem(helpers._LS_CHART_PARAMS_KEY, "{nie-json")
        default = ["amb_temp"]
        assert helpers.get_chart_params(default) == default

    def test_non_list_json_falls_back(self, fake_env) -> None:
        """Poprawny JSON, ale nie lista → fallback na default."""
        _, fls = fake_env
        fls.setItem(helpers._LS_CHART_PARAMS_KEY, '{"a": 1}')
        default = ["amb_temp"]
        assert helpers.get_chart_params(default) == default

    def test_empty_list_falls_back(self, fake_env) -> None:
        """Zapisana pusta lista → fallback na default (nie pusty wykres)."""
        _, fls = fake_env
        fls.setItem(helpers._LS_CHART_PARAMS_KEY, "[]")
        default = ["amb_temp"]
        assert helpers.get_chart_params(default) == default

    def test_falls_back_when_ls_unavailable(self, monkeypatch) -> None:
        """localStorage niedostępny (None) → fallback na default, brak wyjątku."""
        monkeypatch.setattr(helpers, "_get_local_storage", lambda: None)
        default = ["amb_temp"]
        assert helpers.get_chart_params(default) == default
        # persist nie może rzucić wyjątku, gdy LS brak
        helpers.persist_chart_params(["comp_freq"])



class TestGetPumpActivity:
    """3-stanowa aktywność agregatu: heating / running / off.

    heating = sprężarka ON (comp_freq > próg),
    running = pompa wody ON, sprężarka OFF,
    off     = pompa wody OFF.
    """

    @staticmethod
    def _status(comp_freq=0.0, flow_rate=0.0):
        return {
            "comp_freq": {"val_num": comp_freq, "val_str": None},
            "flow_rate": {"val_num": flow_rate, "val_str": None},
        }

    def test_heating_when_compressor_on(self) -> None:
        """comp_freq > próg → heating (nawet gdy pompa wody też pracuje)."""
        st = self._status(comp_freq=45.0, flow_rate=17.0)
        assert helpers.get_pump_activity(st) == helpers.PUMP_ACTIVITY_HEATING

    def test_heating_takes_priority_over_running(self) -> None:
        """Sprężarka ON ma priorytet nad samym obiegiem wody."""
        st = self._status(comp_freq=10.0, flow_rate=5.0)
        assert helpers.get_pump_activity(st) == helpers.PUMP_ACTIVITY_HEATING

    def test_running_when_only_water_pump(self) -> None:
        """Sprężarka OFF, pompa wody ON → running (obieg/dobieg)."""
        st = self._status(comp_freq=0.0, flow_rate=10.0)
        assert helpers.get_pump_activity(st) == helpers.PUMP_ACTIVITY_RUNNING

    def test_off_when_nothing_runs(self) -> None:
        """Sprężarka OFF i pompa wody OFF → off."""
        st = self._status(comp_freq=0.0, flow_rate=0.0)
        assert helpers.get_pump_activity(st) == helpers.PUMP_ACTIVITY_OFF

    def test_off_below_flow_threshold(self) -> None:
        """Przepływ poniżej progu (szum) → off."""
        st = self._status(comp_freq=0.0, flow_rate=1.0)
        assert helpers.get_pump_activity(st) == helpers.PUMP_ACTIVITY_OFF

    def test_missing_keys_default_off(self) -> None:
        """Brak kluczy w statusie → off (bezpieczny domyślny stan)."""
        assert helpers.get_pump_activity({}) == helpers.PUMP_ACTIVITY_OFF

    def test_each_state_has_distinct_color(self) -> None:
        """Każdy stan ma zdefiniowany, unikalny kolor."""
        colors = helpers.PUMP_ACTIVITY_COLORS
        keys = [helpers.PUMP_ACTIVITY_HEATING, helpers.PUMP_ACTIVITY_RUNNING, helpers.PUMP_ACTIVITY_OFF]
        assert all(k in colors for k in keys)
        assert len({colors[k] for k in keys}) == 3

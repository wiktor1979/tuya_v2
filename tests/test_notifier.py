"""Testy notifiera — raport dzienny.

Regresja: build_daily_report używał funkcji liczącej zużycie z licznika bez
importu → NameError na produkcji podczas wysyłki raportu (2026-09-04). Test pilnuje,
by wszystkie zależności funkcji były dostępne i raport budował się w całości.
Zużycie z licznika liczone z add_ele (licznik zdalny Tuya)."""
from unittest import mock
from contextlib import contextmanager

from app.core.models import EnergyResult
from app.services import notifier


@contextmanager
def _fake_cursor():
    yield mock.MagicMock()


def _fake_result() -> EnergyResult:
    return EnergyResult(
        e_el_co=1.0, e_el_cwu=1.5, e_el_standby=0.2,
        e_th_co=4.0, e_th_cwu=5.0, e_th_defrost=0.0,
        scop_real=3.6, comp_starts=8, comp_hours=2.5, defrost_count=0,
    )


class TestBuildDailyReport:
    """build_daily_report — kompletność zależności i treść."""

    def test_no_nameerror_and_includes_meter(self) -> None:
        """Raport buduje się bez NameError i zawiera zużycie z licznika."""
        with mock.patch("app.core.energy.compute_energy", return_value=_fake_result()), \
             mock.patch("app.services.database.load_calibration", return_value={
                 "cos_phi": 0.95, "standby_power_w": 4.0, "active_power_w": 300.0,
                 "hidden_power_w": 0.0, "sensor_factor": 0.98,
             }), \
             mock.patch("app.services.database.get_remote_meter_energy", return_value=2.73), \
             mock.patch("app.services.database.get_fault_history", return_value=[]), \
             mock.patch("app.services.database.db_cursor", _fake_cursor):
            report = notifier.build_daily_report("dev-test")

        assert report is not None
        assert "Raport dzienny" in report
        assert "licznik" in report.lower()
        # zużycie z licznika ujęte w raporcie
        assert "2.73" in report

    def test_returns_none_when_no_energy(self) -> None:
        """Brak energii (e_el_total <= 0) → raport None."""
        with mock.patch("app.core.energy.compute_energy", return_value=EnergyResult()), \
             mock.patch("app.services.database.load_calibration", return_value={
                 "cos_phi": 0.95, "standby_power_w": 4.0, "active_power_w": 300.0,
                 "hidden_power_w": 0.0, "sensor_factor": 0.98,
             }), \
             mock.patch("app.services.database.get_remote_meter_energy", return_value=None), \
             mock.patch("app.services.database.get_fault_history", return_value=[]), \
             mock.patch("app.services.database.db_cursor", _fake_cursor):
            report = notifier.build_daily_report("dev-test")

        assert report is None


class TestPerPumpReport:
    """build_daily_report — poprawny device_id i meter_id per pompa."""

    def test_passes_device_id_to_compute_energy(self) -> None:
        """compute_energy dostaje device_id danej pompy (raport per pompa)."""
        from app.config import PUMPS
        dev = PUMPS[0]["device_id"]

        with mock.patch("app.core.energy.compute_energy", return_value=_fake_result()) as m_ce, \
             mock.patch("app.services.database.load_calibration", return_value={
                 "cos_phi": 0.95, "standby_power_w": 4.0, "active_power_w": 300.0,
                 "hidden_power_w": 0.0, "sensor_factor": 0.98,
             }), \
             mock.patch("app.services.database.get_remote_meter_energy", return_value=None) as m_meter, \
             mock.patch("app.services.database.get_fault_history", return_value=[]), \
             mock.patch("app.services.database.db_cursor", _fake_cursor):
            notifier.build_daily_report(dev)

        # device_id przekazany do silnika energii
        assert m_ce.call_args.kwargs.get("device_id") == dev
        # meter_id pompy 0 przekazany do licznika (3. pozycyjny argument)
        assert m_meter.call_args.args[2] == PUMPS[0]["meter_id"]

    def test_pump_without_meter_gets_none_meter_id(self) -> None:
        """Pompa bez licznika (meter_id=None) → get_remote_meter_energy dostaje None."""
        from app.config import PUMPS
        # Znajdź pompę bez licznika (jeśli istnieje w konfiguracji)
        no_meter = next((p for p in PUMPS if p["meter_id"] is None), None)
        if no_meter is None:
            return  # brak takiej pompy w konfiguracji — nic do sprawdzenia

        with mock.patch("app.core.energy.compute_energy", return_value=_fake_result()), \
             mock.patch("app.services.database.load_calibration", return_value={
                 "cos_phi": 0.95, "standby_power_w": 4.0, "active_power_w": 300.0,
                 "hidden_power_w": 0.0, "sensor_factor": 0.98,
             }), \
             mock.patch("app.services.database.get_remote_meter_energy", return_value=None) as m_meter, \
             mock.patch("app.services.database.get_fault_history", return_value=[]), \
             mock.patch("app.services.database.db_cursor", _fake_cursor):
            notifier.build_daily_report(no_meter["device_id"])

        assert m_meter.call_args.args[2] is None

"""Testy database.py — energia z licznika zdalnego Tuya (add_ele)."""
from contextlib import contextmanager
from unittest import mock

from app.services import database


@contextmanager
def _cursor_returning(row):
    cur = mock.MagicMock()
    cur.fetchone.return_value = row
    yield cur


class TestGetRemoteMeterEnergy:
    """get_remote_meter_energy — suma add_ele × 0.001 kWh (1 jedn. = 1 Wh)."""

    def test_sum_scaled_to_kwh(self) -> None:
        """Suma 2736 Wh → 2.736 kWh."""
        with mock.patch.object(database, "db_cursor", lambda: _cursor_returning((2736.0, 58))):
            result = database.get_remote_meter_energy(0, 1_000_000)
        assert result is not None
        assert abs(result - 2.736) < 1e-9

    def test_none_when_no_reports(self) -> None:
        """Brak raportów add_ele (COUNT=0) → None."""
        with mock.patch.object(database, "db_cursor", lambda: _cursor_returning((0, 0))):
            result = database.get_remote_meter_energy(0, 1_000_000)
        assert result is None

    def test_zero_sum_with_reports_is_zero_kwh(self) -> None:
        """Są raporty (COUNT>0), ale suma 0 Wh → 0.0 kWh, nie None."""
        with mock.patch.object(database, "db_cursor", lambda: _cursor_returning((0.0, 3))):
            result = database.get_remote_meter_energy(0, 1_000_000)
        assert result == 0.0

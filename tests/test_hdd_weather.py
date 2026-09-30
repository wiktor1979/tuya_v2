"""Testy HDD liczonego z danych pogodowych (weather_daily) w compute_energy().

Decyzja 2026-09-27: HDD ze WSPÓLNEGO źródła (pogoda Open-Meteo), nie z czujnika
amb_temp jednostki (offset montażowy ~1.3°C między pompami → HDD nieporównywalny).
Fallback na amb_temp gdy brak pogody dla dnia (decyzja C).
"""
import os
import sqlite3
import tempfile
from datetime import date, datetime
from contextlib import contextmanager
from unittest import mock

import pytest

from app.core.energy import compute_energy
from app.core.physics import compute_hdd
from app.services import database


def _mk_db(path: str, day_utc_start: int, amb_temp: float) -> None:
    """Tworzy minimalną bazę: 1 dzień telemetrii CO z zadaną amb_temp czujnika.

    Próbki co 60 s przez 2 h pracy CO (comp_freq>5, flow, ΔT dodatnie), żeby był
    bilans energii i rozbicie dzienne. amb_temp stałe = amb_temp (czujnik).
    """
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER, device_id TEXT, code TEXT, val_num REAL, val_str TEXT)""")
    dev = "devTEST"
    rows = []
    # seed + 120 próbek co 60 s
    for i in range(0, 121):
        ts = day_utc_start + 10 * 3600 + i * 60  # ok. 10:00 lokalnie-ish
        def add(code, num=None, s=None):
            rows.append((ts, dev, code, num, s))
        add("comp_freq", 45.0)
        add("flow_rate", 15.0)
        add("out_water_temp", 35.0)
        add("in_water_temp", 30.0)
        add("ac_vol", 230.0)
        add("ac_curr", 30.0)
        add("work_mode", None, "heat")
        add("tank_temp", 40.0)
        add("hot_water_temp_set", 45.0)
        add("defrost", None, "False")
        add("amb_temp", amb_temp)
    conn.executemany(
        "INSERT INTO telemetry (timestamp, device_id, code, val_num, val_str) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # 2026-09-26 00:00 UTC
    day_start = int((datetime(2026, 9, 26) - datetime(1970, 1, 1)).total_seconds())
    _mk_db(path, day_start, amb_temp=15.5)  # czujnik: 15.5 → HDD amb ≈ 0
    yield path, date(2026, 9, 26)
    os.remove(path)


class TestHddFromWeather:
    def test_hdd_uses_weather_when_provided(self, tmp_db):
        """weather_daily podane → HDD z pogody (nie z amb_temp czujnika)."""
        path, d = tmp_db
        # Pogoda: 11.0°C → HDD = 4.0; czujnik amb=15.5 dałby ~0
        result = compute_energy(
            date_from="2026-09-26", date_to="2026-09-27",
            db_file=path, device_id="devTEST", daily_breakdown=True,
            weather_daily={d: 11.0},
        )
        assert abs(result.hdd - compute_hdd(11.0)) < 1e-6
        assert result.hdd > 3.9  # z pogody, nie ~0 z czujnika
        # kolumna daily też z pogody
        assert abs(float(result.daily.iloc[0]["hdd"]) - 4.0) < 1e-6

    def test_hdd_fallback_to_amb_when_no_weather(self, tmp_db):
        """Brak weather_daily → fallback na amb_temp czujnika (dotychczasowe)."""
        path, d = tmp_db
        result = compute_energy(
            date_from="2026-09-26", date_to="2026-09-27",
            db_file=path, device_id="devTEST", daily_breakdown=True,
            weather_daily=None,
        )
        # amb=15.5 → HDD = max(0, 15-15.5) = 0
        assert result.hdd == 0.0

    def test_hdd_fallback_for_day_missing_in_weather(self, tmp_db):
        """weather_daily podane, ale bez tego dnia → fallback na amb_temp dla dnia."""
        path, d = tmp_db
        other = date(2020, 1, 1)
        result = compute_energy(
            date_from="2026-09-26", date_to="2026-09-27",
            db_file=path, device_id="devTEST", daily_breakdown=True,
            weather_daily={other: 0.0},
        )
        assert result.hdd == 0.0  # dzień nieobjęty pogodą → amb (15.5) → 0


@contextmanager
def _cursor_returning(rows):
    cur = mock.MagicMock()
    cur.fetchall.return_value = rows
    yield cur


class TestGetWeatherDailyAvg:
    """get_weather_daily_avg — grupowanie po dniu lokalnym, mapowanie na date."""

    def test_maps_rows_to_dates(self):
        rows = [("2026-09-26", 13.71), ("2026-09-27", 16.40)]
        with mock.patch.object(database, "db_cursor", lambda: _cursor_returning(rows)):
            out = database.get_weather_daily_avg(0, 1_000_000, 2)
        assert out == {date(2026, 9, 26): 13.71, date(2026, 9, 27): 16.40}

    def test_empty_when_no_rows(self):
        with mock.patch.object(database, "db_cursor", lambda: _cursor_returning([])):
            out = database.get_weather_daily_avg(0, 1_000_000, 2)
        assert out == {}

    def test_skips_null_avg(self):
        rows = [("2026-09-26", None), ("2026-09-27", 16.40)]
        with mock.patch.object(database, "db_cursor", lambda: _cursor_returning(rows)):
            out = database.get_weather_daily_avg(0, 1_000_000, 2)
        assert out == {date(2026, 9, 27): 16.40}

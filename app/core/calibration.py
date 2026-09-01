"""Kalibracja energii elektrycznej z licznika fizycznego.

Model kalibracji:
    E_el_real = E_el_sensor × sensor_factor + hidden_power_w × total_hours / 1000

Gdzie:
    - E_el_sensor: energia z czujnika pompy (ac_curr × ac_vol × cos_phi) [kWh]
    - sensor_factor: korekcja proporcjonalna czujnika (błąd cos_phi, offset) [×]
    - hidden_power_w: stały ukryty pobór niewidoczny dla czujnika [W]
      (elektronika, pompa obiegowa standby, grzałka antyzamrożeniowa)
    - total_hours: całkowity czas okresu (praca + standby) [h]

Dlaczego dwa parametry zamiast jednego factora:
    - hidden_power_w jest ADDYTYWNY i STAŁY (~20W, 24/7)
    - sensor_factor jest MULTIPLIKATYWNY i dotyczy tylko okresu pracy
    - Latem (1h pracy/dobę): hidden = 15% energii, factor = ×1.18
    - Zimą (20h pracy/dobę): hidden = 0.1% energii, factor = ×1.001
    - Stały factor ×1.21 zawyżyłby E_el zimą o 21% → SCOP błędnie niski

Kalibracja z licznika:
    Z wielu odczytów licznika (różne proporcje praca/postój) można wyliczyć oba
    parametry regresją liniową. Na start prostsza wersja: sensor_factor=1.0,
    hidden_power_w wyliczony z różnicy licznik-telemetria.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CalibrationResult:
    """Wynik kalibracji z licznika fizycznego."""

    hidden_power_w: float = 0.0
    """Stały ukryty pobór [W]. Addytywny, 24/7."""

    sensor_factor: float = 1.0
    """Korekcja proporcjonalna czujnika [×]. Multiplikatywny, dotyczy pracy."""

    readings_used: int = 0
    """Ile par odczytów użyto do kalibracji."""

    confidence: str = "none"
    """Poziom pewności: 'none' (brak danych), 'low' (<3 odczyty), 'medium' (3-7), 'high' (>7)."""


def compute_calibration(
    meter_readings: list[tuple[int, float]],
    sensor_energy_fn: "callable",
    min_readings: int = 2,
    max_readings: int = 14,
) -> CalibrationResult:
    """Wylicza parametry kalibracji z odczytów licznika fizycznego.

    Model prosty (faza 1): sensor_factor = 1.0, liczy tylko hidden_power_w.
    Model pełny (faza 2): regresja liniowa na oba parametry.

    Args:
        meter_readings: Lista (timestamp, kwh) posortowana chronologicznie.
            Odczyty ręczne (1/dzień) lub automatyczne (co minutę z licznika Tuya).
        sensor_energy_fn: Funkcja (ts_from, ts_to) -> float [kWh]
            Wywołuje compute_energy(sensor_factor=1.0, hidden_power_w=0)
            żeby dostać surową energię z czujnika bez kalibracji.
        min_readings: Minimalna liczba odczytów do kalibracji.
        max_readings: Ile ostatnich odczytów brać (rolling window).

    Returns:
        CalibrationResult z hidden_power_w i sensor_factor.
    """
    if len(meter_readings) < min_readings:
        return CalibrationResult(confidence="none")

    # Weź ostatnie N par odczytów
    readings = meter_readings[-max_readings:]

    # Oblicz delty między kolejnymi odczytami
    hidden_powers: list[float] = []
    sensor_factors: list[float] = []

    for i in range(1, len(readings)):
        ts_from, kwh_from = readings[i - 1]
        ts_to, kwh_to = readings[i]

        meter_delta_kwh = kwh_to - kwh_from
        period_hours = (ts_to - ts_from) / 3600.0

        if meter_delta_kwh <= 0 or period_hours < 0.5:
            continue  # pomijaj niepoprawne odczyty

        # Energia z czujnika (bez kalibracji!)
        try:
            sensor_kwh = sensor_energy_fn(ts_from, ts_to)
        except Exception as e:
            logger.warning(f"Calibration: sensor_energy_fn failed: {e}")
            continue

        if sensor_kwh < 0:
            continue

        # Model: meter = sensor × factor + hidden_W × hours / 1000
        # Z założeniem factor=1.0:
        # hidden_W = (meter - sensor) / hours × 1000
        diff_kwh = meter_delta_kwh - sensor_kwh
        hidden_w = diff_kwh / period_hours * 1000.0

        # Walidacja
        if hidden_w < -50 or hidden_w > 200:
            logger.warning(
                f"Calibration: hidden_power={hidden_w:.1f}W out of range "
                f"[-50, 200], skipping period {ts_from}->{ts_to}"
            )
            continue

        hidden_powers.append(hidden_w)

        # Factor (dla przyszłego modelu pełnego)
        if sensor_kwh > 0.1:
            factor = meter_delta_kwh / (sensor_kwh + hidden_w * period_hours / 1000)
            if 0.7 < factor < 1.5:
                sensor_factors.append(factor)

    if not hidden_powers:
        return CalibrationResult(confidence="none")

    # Mediana zamiast średniej — odporna na outliers
    hidden_w_result = float(np.median(hidden_powers))
    factor_result = float(np.median(sensor_factors)) if sensor_factors else 1.0

    # Walidacja wyniku
    if hidden_w_result < 0:
        logger.info(f"Calibration: hidden_power={hidden_w_result:.1f}W < 0, clamping to 0")
        hidden_w_result = 0.0

    # Confidence
    n = len(hidden_powers)
    if n >= 7:
        confidence = "high"
    elif n >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    if confidence in ("medium", "high"):
        std = float(np.std(hidden_powers))
        if std > 15:
            logger.warning(
                f"Calibration: high variance in hidden_power "
                f"(std={std:.1f}W, n={n}). Check meter readings."
            )

    return CalibrationResult(
        hidden_power_w=hidden_w_result,
        sensor_factor=factor_result,
        readings_used=n,
        confidence=confidence,
    )


def apply_calibration(
    e_el_sensor_kwh: float,
    total_hours: float,
    hidden_power_w: float = 0.0,
    sensor_factor: float = 1.0,
) -> float:
    """Stosuje kalibrację do energii elektrycznej z czujnika.

    Args:
        e_el_sensor_kwh: Energia z czujnika (surowa) [kWh].
        total_hours: Całkowity czas okresu (praca + postój) [h].
        hidden_power_w: Stały ukryty pobór [W].
        sensor_factor: Korekcja proporcjonalna czujnika [×].

    Returns:
        Skalibrowana energia elektryczna [kWh].

    Example:
        Lato (24h, 1.5h pracy):
            apply_calibration(2.5, 24.0, hidden_power_w=20) = 2.5 + 0.48 = 2.98 kWh
        Zima (24h, 16h pracy):
            apply_calibration(25.0, 24.0, hidden_power_w=20) = 25.0 + 0.48 = 25.48 kWh
    """
    return e_el_sensor_kwh * sensor_factor + hidden_power_w * total_hours / 1000.0

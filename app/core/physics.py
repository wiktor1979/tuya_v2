"""Czyste wzory fizyczne — P_el, P_th, COP.

Moduł nie ma żadnych zależności od Streamlit, bazy danych ani I/O.
Wszystkie funkcje przyjmują wartości liczbowe i zwracają wartości liczbowe.
"""
import numpy as np


def compute_p_el_w(
    ac_vol: float,
    ac_curr_raw: float,
    cos_phi: float = 0.95,
    standby_power_w: float = 25.0,
    active_power_w: float = 40.0,
    sensor_factor: float = 1.0,
) -> float:
    """Oblicza moc elektryczną pompy ciepła [W].

    Args:
        ac_vol: Napięcie zasilania [V] (surowa wartość z telemetrii).
        ac_curr_raw: Prąd surowy z telemetrii (skala ×0.1 A, tj. 35 = 3.5 A).
        cos_phi: Współczynnik mocy (domyślnie 0.95).
        standby_power_w: Pobór mocy w stanie spoczynku [W] (elektronika, pompa obiegowa).
        active_power_w: Dodatkowy pobór podczas pracy sprężarki [W].
        sensor_factor: Korekcja proporcjonalna czujnika prądu [×].

    Returns:
        Moc elektryczna [W]. Zawsze >= 0.
    """
    curr_a = ac_curr_raw / 10.0
    raw_p_w = ac_vol * curr_a * cos_phi
    is_active = raw_p_w > 100.0  # sprężarka pracuje
    correction_w = standby_power_w + (active_power_w if is_active else 0.0)
    p_el_w = (raw_p_w + correction_w) * sensor_factor
    return max(0.0, p_el_w)


def compute_p_el_w_array(
    ac_vol: np.ndarray,
    ac_curr_raw: np.ndarray,
    cos_phi: float = 0.95,
    standby_power_w: float = 25.0,
    active_power_w: float = 40.0,
    sensor_factor: float = 1.0,
) -> np.ndarray:
    """Wektorowa wersja compute_p_el_w — dla tablic numpy.

    Args:
        ac_vol: Tablica napięć [V].
        ac_curr_raw: Tablica prądów surowych (skala ×0.1 A).
        cos_phi: Współczynnik mocy.
        standby_power_w: Pobór standby [W].
        active_power_w: Dodatkowy pobór active [W].
        sensor_factor: Korekcja proporcjonalna czujnika [×].

    Returns:
        Tablica mocy elektrycznych [W].
    """
    curr_a = ac_curr_raw / 10.0
    raw_p_w = ac_vol * curr_a * cos_phi
    is_active = raw_p_w > 100.0
    correction_w = standby_power_w + np.where(is_active, active_power_w, 0.0)
    p_el_w = (raw_p_w + correction_w) * sensor_factor
    return np.maximum(0.0, p_el_w)


def compute_p_th_w(
    flow_rate_raw: float,
    out_water_temp_c: float,
    in_water_temp_c: float,
) -> float:
    """Oblicza moc cieplną pompy ciepła [W].

    Ujemna wartość podczas defrostu (ΔT < 0) jest POPRAWNA — oznacza
    ciepło odebrane z obiegu grzewczego.

    Args:
        flow_rate_raw: Przepływ surowy z telemetrii (skala ×0.1 m³/h, tj. 25 = 2.5 m³/h).
        out_water_temp_c: Temperatura zasilania [°C] (już przeliczona, nie surowa).
        in_water_temp_c: Temperatura powrotu [°C] (już przeliczona, nie surowa).

    Returns:
        Moc cieplna [W]. Ujemna podczas defrostu.
    """
    flow_m3h = flow_rate_raw / 10.0
    delta_t_c = out_water_temp_c - in_water_temp_c
    # P_th [kW] = flow [m³/h] × 4.186 [kJ/(kg·K)] × ΔT [K] / 3.6
    # × 1000 → [W]
    p_th_w = flow_m3h * 4.186 * delta_t_c / 3.6 * 1000.0
    return p_th_w


def compute_p_th_w_array(
    flow_rate_raw: np.ndarray,
    out_water_temp_c: np.ndarray,
    in_water_temp_c: np.ndarray,
) -> np.ndarray:
    """Wektorowa wersja compute_p_th_w.

    Returns:
        Tablica mocy cieplnych [W]. Ujemne podczas defrostu.
    """
    flow_m3h = flow_rate_raw / 10.0
    delta_t_c = out_water_temp_c - in_water_temp_c
    return flow_m3h * 4.186 * delta_t_c / 3.6 * 1000.0


def compute_cop(p_th_w: float, p_el_w: float) -> float:
    """Oblicza chwilowy COP.

    Args:
        p_th_w: Moc cieplna [W].
        p_el_w: Moc elektryczna [W].

    Returns:
        COP (Coefficient of Performance). 0.0 jeśli P_el <= 0
        lub COP poza zakresem [0.5, 12.0].
    """
    if p_el_w <= 0.0 or p_th_w <= 0.0:
        return 0.0
    cop = p_th_w / p_el_w
    if cop < 0.5 or cop > 12.0:
        return 0.0
    return cop


def compute_hdd(amb_temp_avg_c: float, base_temp_c: float = 15.0) -> float:
    """Oblicza Heating Degree Days dla jednego dnia.

    Args:
        amb_temp_avg_c: Średnia dobowa temperatura zewnętrzna [°C].
        base_temp_c: Temperatura bazowa HDD [°C] (domyślnie 15°C).

    Returns:
        HDD dla jednego dnia. Zawsze >= 0.
    """
    return max(0.0, base_temp_c - amb_temp_avg_c)

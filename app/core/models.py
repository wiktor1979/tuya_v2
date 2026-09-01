"""Modele danych dla silnika obliczeniowego v2."""
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class EnergyResult:
    """Wynik obliczenia energetycznego z compute_energy().

    Konwencje:
        - Energia w kWh (float).
        - e_th_defrost jest ZAWSZE ujemne (strata cieplna podczas defrostu).
        - SCOP realny = (e_th + e_th_defrost) / e_el. Dodajemy wartość ze znakiem:
          e_th_defrost ≤ 0, więc realnie obniża licznik (dodanie liczby ujemnej = odjęcie).
    """

    # Energia [kWh]
    e_el_co: float = 0.0
    """Energia elektryczna zużyta na ogrzewanie (CO) [kWh]."""

    e_el_cwu: float = 0.0
    """Energia elektryczna zużyta na ciepłą wodę (CWU) [kWh]."""

    e_el_standby: float = 0.0
    """Energia elektryczna standby (sprężarka OFF) [kWh]. Nie przypisana do CO/CWU."""

    e_th_co: float = 0.0
    """Ciepło wygenerowane w trybie CO [kWh]."""

    e_th_cwu: float = 0.0
    """Ciepło wygenerowane w trybie CWU [kWh]."""

    e_th_defrost: float = 0.0
    """Strata cieplna defrostu [kWh]. ZAWSZE ujemne lub zero."""

    # SCOP
    scop_nominal: float = 0.0
    """SCOP nominalny = E_th / E_el (bez strat defrostu)."""

    scop_real: float = 0.0
    """SCOP realny = (E_th + E_th_defrost) / E_el (z defrostem)."""

    # Statystyki
    comp_starts: int = 0
    """Liczba startów sprężarki w okresie."""

    comp_hours: float = 0.0
    """Godziny pracy sprężarki."""

    defrost_count: int = 0
    """Liczba cykli defrostu."""

    amb_temp_avg: float = 0.0
    """Średnia temperatura zewnętrzna [°C]."""

    hdd: float = 0.0
    """Heating Degree Days (baza 15°C)."""

    # Agregacja dzienna (opcjonalna)
    daily: Optional[pd.DataFrame] = field(default=None, repr=False)
    """Rozbicie na dni. Kolumny: date, e_el_co, e_el_cwu, e_th_co, e_th_cwu,
    e_th_defrost, scop_nominal, scop_real, hdd, amb_temp_avg,
    comp_starts, defrost_count, comp_hours."""

    # Metadane
    date_from: str = ""
    """Początek zakresu (ISO date string)."""

    date_to: str = ""
    """Koniec zakresu (ISO date string)."""

    sample_count: int = 0
    """Liczba próbek użytych do obliczeń."""

    gaps_skipped: int = 0
    """Liczba interwałów pominiętych (Δt > dt_max_sec)."""

    compute_time_ms: float = 0.0
    """Czas obliczeń [ms]."""

    # --- Właściwości pomocnicze ---

    @property
    def e_el_total(self) -> float:
        """Całkowita energia elektryczna (CO + CWU + standby) [kWh]."""
        return self.e_el_co + self.e_el_cwu + self.e_el_standby

    @property
    def e_th_total(self) -> float:
        """Ciepło nominalne (bez defrostu) [kWh]."""
        return self.e_th_co + self.e_th_cwu

    @property
    def e_th_total_real(self) -> float:
        """Ciepło realne (z defrostem) [kWh]. e_th_defrost < 0 obniża sumę."""
        return self.e_th_co + self.e_th_cwu + self.e_th_defrost

"""Krzywa grzewcza — model matematyczny, epizody grzania, rekomendacja nastaw.

Czysty Python (bez Streamlit, bez I/O). Zależność tylko od numpy (regresja).

KONTEKST FIZYCZNY (instalacja użytkownika):
- Pompa dobiera temperaturę wody z 2-punktowej krzywej pogodowej:
    * przy amb = -15°C  → woda = T_low  (np. 43°C)
    * przy amb = +15°C  → woda = T_high (np. 28°C)
  Dla dowolnej temp. zewnętrznej pompa interpoluje liniowo (patrz curve_water_temp).
- Pompa NIE raportuje wyliczonej z krzywej temperatury wody. Z nastaw krzywej
  (podanych w formularzu) odtwarzamy ją sami.
- Termostat pokojowy ON/OFF nie ma własnego DP. Jego stan widać przez work_mode:
    * 'hot_water'      → termostat OFF (nie żąda CO; pompa robi tylko CWU)
    * 'heat_hot_water' → termostat ON  (żąda grzania → pompa dołącza CO)
    * 'heat'           → termostat ON  (czyste CO; rzadkie)
  Przejście hot_water → (heat|heat_hot_water) = termostat ZAŻĄDAŁ grzania.

CEL ANALIZY: dobrać krzywą tak, by pompa grzała możliwie CIĄGLE z MINIMALNĄ
wymaganą temperaturą wody (duty cycle termostatu > ~85%, bez taktowania).
Za ciepła woda → krótkie, częste epizody grzania (duty niski) → OBNIŻYĆ krzywą.
Za zimna woda → termostat trzyma ON ciągle, temp. pokojowa nie dochodzi → PODNIEŚĆ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

# --- Punkty kotwiczące krzywej (°C temp. zewnętrznej) ---
CURVE_POINT_LOW_AMB: float = -15.0
"""Zimny koniec krzywej — temperatura zewnętrzna, dla której podajemy T_low."""

CURVE_POINT_HIGH_AMB: float = 15.0
"""Ciepły koniec krzywej — temperatura zewnętrzna, dla której podajemy T_high."""

# --- Progi jakości danych (rozpiętość temperatury zewnętrznej) ---
AMB_RANGE_MIN_SLOPE: float = 8.0
"""Minimalny rozrzut amb [°C], poniżej którego nachylenia NIE da się sensownie
wyznaczyć — rekomendacja tylko dla lokalnego końca krzywej."""

AMB_RANGE_RELIABLE_SLOPE: float = 15.0
"""Rozrzut amb [°C], od którego nachylenie uznajemy za wiarygodne — pełna
rekomendacja obu końców (T_low i T_high)."""

# --- Duty cycle termostatu ---
TARGET_DUTY_PCT: float = 85.0
"""Docelowy duty cycle grzania (udział czasu ON). > 85% = praca ciągła (OK)."""

MIN_EPISODE_SEC: int = 60
"""Minimalny czas epizodu, by go liczyć (odcięcie szumu/pojedynczych próbek)."""

MAX_GAP_SEC: int = 24 * 3600
"""Maksymalna przerwa OFF między epizodami zaliczana do duty cycle [s].
Dłuższa przerwa (np. pompa w trybie letnim na tygodnie) traktowana jako luka —
nie zaniża duty cycle do zera. 24h pokrywa realny OFF termostatu w sezonie
(ciepły dzień, noc), ale odcina wielodniowe postoje."""

# Kody work_mode żądające grzania CO (termostat ON).
_HEATING_DEMAND_MODES: frozenset[str] = frozenset({"heat", "heat_hot_water"})


# =============================================================================
# 1. MODEL KRZYWEJ — interpolacja / ekstrapolacja liniowa
# =============================================================================

def curve_slope(
    t_low: float,
    t_high: float,
    amb_low: float = CURVE_POINT_LOW_AMB,
    amb_high: float = CURVE_POINT_HIGH_AMB,
) -> float:
    """Nachylenie krzywej [°C wody / °C powietrza]. Zwykle ujemne
    (cieplej na zewnątrz → chłodniejsza woda).

    Raises:
        ValueError: gdy punkty kotwiczące mają tę samą temp. zewnętrzną.
    """
    if amb_high == amb_low:
        raise ValueError("Punkty kotwiczące krzywej muszą mieć różne temp. zewnętrzne.")
    return (t_high - t_low) / (amb_high - amb_low)


def curve_water_temp(
    amb: float,
    t_low: float,
    t_high: float,
    amb_low: float = CURVE_POINT_LOW_AMB,
    amb_high: float = CURVE_POINT_HIGH_AMB,
) -> float:
    """Temperatura wody z krzywej dla danej temp. zewnętrznej (interpolacja/ekstrapolacja).

    Liniowa funkcja dwóch punktów: (amb_low, t_low) i (amb_high, t_high).
    Dla amb poza [amb_low, amb_high] ekstrapoluje (pompa też tak robi,
    zwykle z wewnętrznymi limitami, których tu nie modelujemy).

    Args:
        amb: Temperatura zewnętrzna [°C].
        t_low: Temp. wody przy amb_low (zimny koniec, np. -15°C).
        t_high: Temp. wody przy amb_high (ciepły koniec, np. +15°C).

    Returns:
        Temperatura wody [°C] wyznaczona z krzywej.
    """
    slope = curve_slope(t_low, t_high, amb_low, amb_high)
    return t_low + slope * (amb - amb_low)


# =============================================================================
# 2. EPIZODY GRZANIA — z sekwencji zdarzeń work_mode
# =============================================================================

@dataclass
class HeatingEpisode:
    """Pojedynczy epizod żądania grzania przez termostat pokojowy.

    ON  = termostat zażądał grzania (work_mode heat / heat_hot_water).
    Epizod trwa od wejścia w tryb grzewczy do powrotu do hot_water (OFF).
    """
    start_ts: int
    end_ts: int
    duration_sec: int
    amb_avg: float            # średnia temp. zewnętrzna w trakcie epizodu
    off_after_sec: int        # czas OFF po tym epizodzie (do następnego ON); 0 jeśli nieznany


@dataclass
class DutyBin:
    """Agregat duty cycle dla jednego przedziału temperatury zewnętrznej."""
    amb_min: float
    amb_max: float
    amb_center: float
    on_sec: float
    total_sec: float
    duty_pct: float
    n_episodes: int
    avg_episode_sec: float

    @property
    def has_enough_data(self) -> bool:
        """Czy bin ma wystarczająco danych (min. 1h łącznie)."""
        return self.total_sec >= 3600.0


ZONE_SELECT_Z1: float = 1.0
"""zone_select: aktywna tylko strefa 1."""
ZONE_SELECT_Z2: float = 2.0
"""zone_select: aktywna tylko strefa 2 (stała temp. wody — POMIJANA w analizie krzywej)."""
ZONE_SELECT_BOTH: float = 3.0
"""zone_select: aktywne obie strefy."""

# Strefy, w których krzywa grzewcza strefy 1 jest realizowana (Z1 lub obie).
# Strefa 2 ma STAŁĄ temperaturę wody — nie podlega krzywej, więc epizody z samą
# strefą 2 pomijamy przy dobieraniu nastaw krzywej.
_CURVE_ZONES: frozenset[float] = frozenset({ZONE_SELECT_Z1, ZONE_SELECT_BOTH})


def detect_heating_episodes(
    events: Sequence[Tuple[int, str]],
    amb_samples: Optional[Sequence[Tuple[int, float]]] = None,
    zone_samples: Optional[Sequence[Tuple[int, float]]] = None,
    min_episode_sec: int = MIN_EPISODE_SEC,
    max_gap_sec: int = MAX_GAP_SEC,
    curve_zones: frozenset = _CURVE_ZONES,
    since_ts: Optional[int] = None,
) -> List[HeatingEpisode]:
    """Wykrywa epizody żądania grzania z sekwencji zdarzeń work_mode.

    work_mode jest serią ZDARZENIOWĄ (zapis tylko przy zmianie). Każde zdarzenie
    (ts, mode) obowiązuje aż do następnego. Epizod ON zaczyna się, gdy mode wchodzi
    w tryb grzewczy (heat/heat_hot_water), a kończy, gdy wraca do innego trybu
    (hot_water) lub na końcu danych.

    FILTR STREFY: krzywa grzewcza dobierana jest dla STREFY 1. Strefa 2 ma stałą
    temperaturę wody (nie z krzywej). Jeśli podano zone_samples, epizod jest
    liczony tylko, gdy większość jego okna należy do stref z `curve_zones`.
    Domyślnie curve_zones = {Z1, obie} — epizody samej strefy 2 są odrzucane.
    (Można podać np. {Z2}, by policzyć WYŁĄCZNIE epizody strefy 2 — do diagnostyki.)

    Args:
        events: Posortowana rosnąco lista (timestamp_epoch, work_mode_str).
        amb_samples: Opcjonalna lista (timestamp, amb_temp_°C) do policzenia
            średniej temp. zewnętrznej per epizod. Jeśli None → amb_avg = nan.
        zone_samples: Opcjonalna lista (timestamp, zone_select) — gdy podana,
            filtr strefy aktywny (patrz curve_zones).
        min_episode_sec: Epizody krótsze pomijamy (szum).
        max_gap_sec: Przerwa OFF dłuższa traktowana jako luka (nie wliczana do duty).
        curve_zones: Zbiór kodów zone_select uznawanych za objęte analizą krzywej.
        since_ts: Jeśli podane, pomija epizody rozpoczęte PRZED tym czasem (epoch).
            Służy do analizy wyłącznie danych wygenerowanych przez AKTUALNĄ krzywą
            (po jej ostatniej zmianie) — bez mieszania starej i nowej krzywej.

    Returns:
        Lista HeatingEpisode (posortowana wg startu).
    """
    ev = [(int(ts), str(mode)) for ts, mode in events if mode is not None]
    ev.sort(key=lambda x: x[0])
    if not ev:
        return []

    amb_ts: List[int] = []
    amb_val: List[float] = []
    if amb_samples:
        s = sorted(amb_samples, key=lambda x: x[0])
        amb_ts = [int(t) for t, _ in s]
        amb_val = [float(v) for _, v in s]

    zone_ts: List[int] = []
    zone_val: List[float] = []
    if zone_samples:
        z = sorted(zone_samples, key=lambda x: x[0])
        zone_ts = [int(t) for t, _ in z]
        zone_val = [float(v) for _, v in z]

    def _zone_ok(t0: int, t1: int) -> bool:
        """Czy w oknie [t0, t1] grzana jest strefa 1 lub obie (nie sama Z2).

        Bez danych zone_select zakładamy OK (nie odfiltrowujemy). Z danymi:
        liczymy, czy większość okna należy do stref objętych krzywą (Z1/obie).
        """
        if not zone_ts:
            return True
        lo = _bisect_left(zone_ts, t0)
        hi = _bisect_right(zone_ts, t1)
        window = zone_val[lo:hi]
        if not window:
            idx = _nearest_idx(zone_ts, (t0 + t1) // 2)
            if idx is None:
                return True
            return zone_val[idx] in curve_zones
        curve_cnt = sum(1 for v in window if v in curve_zones)
        return curve_cnt >= (len(window) / 2.0)

    def _amb_avg(t0: int, t1: int) -> float:
        if not amb_ts or t1 <= t0:
            return float("nan")
        lo = _bisect_left(amb_ts, t0)
        hi = _bisect_right(amb_ts, t1)
        window = amb_val[lo:hi]
        if not window:
            # najbliższa próbka do środka epizodu
            mid = (t0 + t1) // 2
            idx = _nearest_idx(amb_ts, mid)
            return amb_val[idx] if idx is not None else float("nan")
        return float(np.mean(window))

    episodes: List[HeatingEpisode] = []
    cur_start: Optional[int] = None

    for i, (ts, mode) in enumerate(ev):
        is_demand = mode in _HEATING_DEMAND_MODES

        if is_demand and cur_start is None:
            cur_start = ts
        elif not is_demand and cur_start is not None:
            # koniec epizodu w momencie tego zdarzenia (OFF/CWU)
            _close_episode(episodes, cur_start, ts, _amb_avg, _zone_ok, min_episode_sec)
            cur_start = None

    # epizod otwarty na końcu danych — zamknij na ostatnim zdarzeniu
    if cur_start is not None:
        last_ts = ev[-1][0]
        if last_ts > cur_start:
            _close_episode(episodes, cur_start, last_ts, _amb_avg, _zone_ok, min_episode_sec)

    # Filtr daty: tylko epizody po ostatniej zmianie krzywej (aktualna krzywa).
    if since_ts is not None:
        episodes = [e for e in episodes if e.start_ts >= since_ts]

    # policz off_after_sec (przerwa do następnego epizodu).
    # Przerwa dłuższa niż max_gap_sec traktowana jako luka w danych (0, nie wliczamy).
    for j in range(len(episodes) - 1):
        gap = episodes[j + 1].start_ts - episodes[j].end_ts
        episodes[j].off_after_sec = max(0, int(gap)) if gap <= max_gap_sec else 0

    return episodes


def _close_episode(episodes, start_ts, end_ts, amb_avg_fn, zone_ok_fn, min_episode_sec):
    dur = int(end_ts - start_ts)
    if dur < min_episode_sec:
        return
    # Pomijamy epizody grzane wyłącznie w strefie 2 (stała temp. wody, nie z krzywej).
    if not zone_ok_fn(start_ts, end_ts):
        return
    episodes.append(HeatingEpisode(
        start_ts=start_ts,
        end_ts=end_ts,
        duration_sec=dur,
        amb_avg=amb_avg_fn(start_ts, end_ts),
        off_after_sec=0,
    ))


# --- lekkie wersje bisect (unikamy importu dla czytelności testów) ---
def _bisect_left(a: List[int], x: int) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(a: List[int], x: int) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _nearest_idx(a: List[int], x: int) -> Optional[int]:
    if not a:
        return None
    i = _bisect_left(a, x)
    best, bestd = None, None
    for j in (i - 1, i):
        if 0 <= j < len(a):
            d = abs(a[j] - x)
            if bestd is None or d < bestd:
                best, bestd = j, d
    return best


# =============================================================================
# 3. DUTY CYCLE per bin temperatury zewnętrznej
# =============================================================================

def compute_duty_bins(
    episodes: Sequence[HeatingEpisode],
    bin_size: float = 3.0,
) -> List[DutyBin]:
    """Grupuje epizody wg średniej temp. zewnętrznej i liczy duty cycle per bin.

    Duty cycle = suma czasu ON / (suma czasu ON + suma czasu OFF po epizodach)
    w danym binie. To udział czasu, w którym termostat żądał grzania.

    Args:
        episodes: Lista epizodów (z amb_avg i off_after_sec).
        bin_size: Szerokość przedziału temperatury [°C].

    Returns:
        Lista DutyBin posortowana rosnąco wg temperatury.
    """
    usable = [e for e in episodes if e.amb_avg is not None and not np.isnan(e.amb_avg)]
    if not usable:
        return []

    buckets: dict[int, dict] = {}
    for e in usable:
        key = int(np.floor(e.amb_avg / bin_size))
        b = buckets.setdefault(key, {"on": 0.0, "off": 0.0, "n": 0, "dur": []})
        b["on"] += e.duration_sec
        b["off"] += e.off_after_sec
        b["n"] += 1
        b["dur"].append(e.duration_sec)

    result: List[DutyBin] = []
    for key in sorted(buckets):
        b = buckets[key]
        amb_min = key * bin_size
        amb_max = amb_min + bin_size
        total = b["on"] + b["off"]
        duty = (b["on"] / total * 100.0) if total > 0 else 0.0
        result.append(DutyBin(
            amb_min=amb_min,
            amb_max=amb_max,
            amb_center=(amb_min + amb_max) / 2.0,
            on_sec=b["on"],
            total_sec=total,
            duty_pct=duty,
            n_episodes=b["n"],
            avg_episode_sec=float(np.mean(b["dur"])) if b["dur"] else 0.0,
        ))
    return result


# =============================================================================
# 4. REKOMENDACJA ZMIANY KRZYWEJ
# =============================================================================

@dataclass
class CurveRecommendation:
    """Wynik analizy krzywej grzewczej."""
    # Dane wejściowe (echo)
    t_low: Optional[float]
    t_high: Optional[float]
    target_room_temp: Optional[float]
    # Jakość danych
    amb_min_observed: Optional[float]
    amb_max_observed: Optional[float]
    amb_range: float
    data_quality: str                 # 'none' | 'narrow' | 'slope_rough' | 'slope_reliable'
    # Rekomendacja
    new_t_low: Optional[float] = None
    new_t_high: Optional[float] = None
    messages: List[str] = field(default_factory=list)
    bins: List[DutyBin] = field(default_factory=list)

    @property
    def has_recommendation(self) -> bool:
        return self.new_t_low is not None or self.new_t_high is not None


def _duty_to_delta(duty_pct: float, target_duty: float) -> float:
    """Przelicza odchyłkę duty cycle na sugerowaną korektę temp. wody [°C].

    Heurystyka: brak 100% duty → woda za ciepła (termostat odcina). Każde ~7 pkt%
    poniżej celu ≈ 1°C nadmiaru wody. Zwraca wartość DODATNIĄ = o ile OBNIŻYĆ wodę.
    duty >= cel → 0 (nie obniżać; ewentualnie podnieść przy przegrzaniu obsłuży kto inny).
    """
    gap = target_duty - duty_pct
    if gap <= 0:
        return 0.0
    return round(gap / 7.0, 1)


def recommend_curve_adjustment(
    bins: Sequence[DutyBin],
    t_low: Optional[float],
    t_high: Optional[float],
    target_room_temp: Optional[float] = None,
    amb_low: float = CURVE_POINT_LOW_AMB,
    amb_high: float = CURVE_POINT_HIGH_AMB,
    target_duty: float = TARGET_DUTY_PCT,
    range_min_slope: float = AMB_RANGE_MIN_SLOPE,
    range_reliable_slope: float = AMB_RANGE_RELIABLE_SLOPE,
    min_bin_sec: float = 3600.0,
) -> CurveRecommendation:
    """Rekomenduje zmianę nastaw krzywej (T_low i/lub T_high) na podstawie duty cycle.

    LOGIKA:
    - Dla każdego binu z wystarczającą ilością danych liczymy niedobór duty cycle
      względem celu (>85%). Niedobór = woda za ciepła przy tej temp. zewnętrznej.
    - Przeliczamy niedobór na °C nadmiaru wody (_duty_to_delta).
    - Rzutujemy korektę na końce krzywej: biny bliżej -15°C korygują T_low,
      biny bliżej +15°C korygują T_high (waga liniowa wg pozycji amb na osi).
    - Jakość: rozrzut amb < range_min_slope → tylko lokalny koniec; w przedziale
      [min, reliable] → nachylenie orientacyjne; >= reliable → pełna rekomendacja.

    Returns:
        CurveRecommendation z new_t_low / new_t_high (jeśli jest podstawa) i komunikatami.
    """
    valid = [b for b in bins if b.total_sec >= min_bin_sec]

    if not valid:
        return CurveRecommendation(
            t_low=t_low, t_high=t_high, target_room_temp=target_room_temp,
            amb_min_observed=None, amb_max_observed=None, amb_range=0.0,
            data_quality="none",
            messages=["Brak wystarczających danych o epizodach grzania (min. 1h na przedział temperatury). "
                      "Analiza będzie możliwa, gdy pompa pogrzeje w trybie CO."],
            bins=list(bins),
        )

    centers = [b.amb_center for b in valid]
    amb_min = min(centers)
    amb_max = max(centers)
    amb_range = amb_max - amb_min

    # Jakość danych wg rozpiętości temperatur
    if amb_range < range_min_slope:
        quality = "narrow"
    elif amb_range < range_reliable_slope:
        quality = "slope_rough"
    else:
        quality = "slope_reliable"

    messages: List[str] = []
    if quality == "narrow":
        messages.append(
            f"⚠️ Zbyt mały zakres temperatur zewnętrznych w danych "
            f"({amb_min:.1f}…{amb_max:.1f}°C, rozrzut {amb_range:.1f}°C < {range_min_slope:.0f}°C). "
            "Nie można wyznaczyć nachylenia krzywej — rekomendacja dotyczy tylko końca bliższego "
            "obserwowanym temperaturom. Dla pełnej analycji potrzeba danych z szerszego zakresu "
            "(np. mróz + okres przejściowy)."
        )
    elif quality == "slope_rough":
        messages.append(
            f"ℹ️ Zakres temperatur {amb_min:.1f}…{amb_max:.1f}°C (rozrzut {amb_range:.1f}°C). "
            f"Nachylenie krzywej orientacyjne — pewne dopasowanie obu końców od rozrzutu "
            f"≥ {range_reliable_slope:.0f}°C."
        )
    else:
        messages.append(
            f"✅ Szeroki zakres temperatur {amb_min:.1f}…{amb_max:.1f}°C "
            f"(rozrzut {amb_range:.1f}°C) — wiarygodne dopasowanie obu końców krzywej."
        )

    # Korekta per bin → rzut na końce krzywej (waga wg pozycji na osi amb_low..amb_high)
    span = amb_high - amb_low
    low_corr: List[Tuple[float, float]] = []   # (waga, delta_obniżki)
    high_corr: List[Tuple[float, float]] = []

    for b in valid:
        delta = _duty_to_delta(b.duty_pct, target_duty)
        if delta <= 0:
            continue
        # waga przynależności do ciepłego końca (0 przy -15, 1 przy +15), z klamrowaniem
        w_high = (b.amb_center - amb_low) / span if span else 0.5
        w_high = min(1.0, max(0.0, w_high))
        w_low = 1.0 - w_high
        if w_low > 0:
            low_corr.append((w_low, delta))
        if w_high > 0:
            high_corr.append((w_high, delta))

    def _weighted(corr: List[Tuple[float, float]]) -> float:
        wsum = sum(w for w, _ in corr)
        if wsum <= 0:
            return 0.0
        return sum(w * d for w, d in corr) / wsum

    rec = CurveRecommendation(
        t_low=t_low, t_high=t_high, target_room_temp=target_room_temp,
        amb_min_observed=amb_min, amb_max_observed=amb_max, amb_range=amb_range,
        data_quality=quality, messages=messages, bins=list(bins),
    )

    # Czy w ogóle jest co obniżać?
    any_overshoot = bool(low_corr or high_corr)
    if not any_overshoot:
        messages.append(
            f"✅ Duty cycle w normie (≥ {target_duty:.0f}%) w obserwowanym zakresie — "
            "krzywa dobrana prawidłowo dla tych warunków."
        )
        return rec

    # Które końce możemy ruszać?
    # - narrow: tylko koniec bliższy obserwowanym temperaturom (ten z większą wagą danych)
    # - rough/reliable: oba końce (reliable z pełnym zaufaniem)
    delta_low = _weighted(low_corr)
    delta_high = _weighted(high_corr)

    if quality == "narrow":
        # wybierz koniec, do którego dane są bliżej (większa sumaryczna waga)
        w_low_total = sum(w for w, _ in low_corr)
        w_high_total = sum(w for w, _ in high_corr)
        if w_high_total >= w_low_total and t_high is not None and delta_high > 0:
            rec.new_t_high = round(t_high - delta_high, 1)
            messages.append(
                f"🔧 Obniż temp. wody dla punktu +{amb_high:.0f}°C "
                f"z {t_high:.1f}°C na ~{rec.new_t_high:.1f}°C "
                "(woda za ciepła — termostat często odcina)."
            )
        elif t_low is not None and delta_low > 0:
            rec.new_t_low = round(t_low - delta_low, 1)
            messages.append(
                f"🔧 Obniż temp. wody dla punktu {amb_low:.0f}°C "
                f"z {t_low:.1f}°C na ~{rec.new_t_low:.1f}°C "
                "(woda za ciepła — termostat często odcina)."
            )
        else:
            messages.append("Brak danych formularza (T_low/T_high) do podania konkretnych wartości.")
        return rec

    # rough / reliable → oba końce
    if t_low is not None and delta_low > 0:
        rec.new_t_low = round(t_low - delta_low, 1)
        messages.append(
            f"🔧 Obniż temp. wody dla punktu {amb_low:.0f}°C "
            f"z {t_low:.1f}°C na ~{rec.new_t_low:.1f}°C."
        )
    if t_high is not None and delta_high > 0:
        rec.new_t_high = round(t_high - delta_high, 1)
        messages.append(
            f"🔧 Obniż temp. wody dla punktu +{amb_high:.0f}°C "
            f"z {t_high:.1f}°C na ~{rec.new_t_high:.1f}°C."
        )
    if (t_low is None and delta_low > 0) or (t_high is None and delta_high > 0):
        messages.append("Podaj aktualne T_low/T_high w formularzu, by otrzymać konkretne wartości docelowe.")

    return rec

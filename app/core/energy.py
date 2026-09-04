"""Centralna funkcja compute_energy() — jedno źródło prawdy dla energii i SCOP.

Zawsze liczy z surowych danych (tabela telemetry). Nigdy nie przechodzi przez resample.
Czysty Python — bez zależności od Streamlit. Cache nakłada warstwa UI.
"""
import sqlite3
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from app.config import (
    COMP_FREQ_ON_THRESHOLD,
    CWU_VALVE_THRESHOLD,
    DB_FILE,
    DEFAULT_ACTIVE_POWER_W,
    DEFAULT_COS_PHI,
    DEFAULT_HIDDEN_POWER_W,
    DEFAULT_SENSOR_FACTOR,
    DEFAULT_STANDBY_POWER_W,
    SERVER_TIMEZONE_OFFSET,
    DT_MAX_SEC,
    ENERGY_CODES,
    HDD_BASE_TEMP_C,
    HEAT_PUMP_DEV_ID,
    TEMP_CODES,
)
from app.core.models import EnergyResult
from app.core.physics import compute_hdd, compute_p_el_w_array, compute_p_th_w_array


def compute_scop(
    e_el_co: float,
    e_el_cwu: float,
    e_el_standby: float,
    e_th_co: float,
    e_th_cwu: float,
    e_th_defrost: float,
    scope: str = "total",
    kind: str = "real",
) -> float:
    """JEDYNE źródło prawdy dla wzoru SCOP w całej aplikacji.

    Wszystkie strony UI i silnik liczą SCOP wyłącznie przez tę funkcję —
    dzięki temu wynik jest zawsze identyczny, niezależnie od miejsca wywołania.

    Konwencje:
        - e_th_defrost jest ZAWSZE ujemne lub zero (strata cieplna defrostu).
        - Defrost obciąża wyłącznie tryb CO (odszranianie dotyczy ogrzewania).
        - Standby (sprężarka OFF) wchodzi do mianownika TYLKO dla scope="total".

    Args:
        e_el_co: Energia elektryczna CO [kWh].
        e_el_cwu: Energia elektryczna CWU [kWh].
        e_el_standby: Energia elektryczna standby [kWh].
        e_th_co: Ciepło CO [kWh].
        e_th_cwu: Ciepło CWU [kWh].
        e_th_defrost: Strata cieplna defrostu [kWh] (≤ 0).
        scope: Zakres SCOP:
            - "total" — cały system: (E_th_CO + E_th_CWU) / (E_el_CO + E_el_CWU + standby).
            - "co"    — tylko ogrzewanie: E_th_CO / E_el_CO (defrost tylko tutaj).
            - "cwu"   — tylko ciepła woda: E_th_CWU / E_el_CWU.
        kind: Rodzaj SCOP:
            - "real"    — z uwzględnieniem strat defrostu (prawdziwa efektywność).
            - "nominal" — bez strat defrostu (teoretyczny, edukacyjny).

    Returns:
        SCOP jako float. 0.0 gdy brak danych (mianownik ≤ 0).
    """
    # Człon defrostu doliczany tylko dla real; dotyczy CO i total (nie CWU)
    defrost_term = e_th_defrost if kind == "real" else 0.0

    if scope == "co":
        numerator = e_th_co + defrost_term
        denominator = e_el_co
    elif scope == "cwu":
        # Defrost nie dotyczy CWU
        numerator = e_th_cwu
        denominator = e_el_cwu
    else:  # "total"
        numerator = e_th_co + e_th_cwu + defrost_term
        denominator = e_el_co + e_el_cwu + e_el_standby

    return numerator / denominator if denominator > 0 else 0.0


def scop_from_result(result: EnergyResult, scope: str = "total", kind: str = "real") -> float:
    """Wygodny wrapper: liczy SCOP z obiektu EnergyResult przez compute_scop()."""
    return compute_scop(
        e_el_co=result.e_el_co,
        e_el_cwu=result.e_el_cwu,
        e_el_standby=result.e_el_standby,
        e_th_co=result.e_th_co,
        e_th_cwu=result.e_th_cwu,
        e_th_defrost=result.e_th_defrost,
        scope=scope,
        kind=kind,
    )


def compute_energy(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    mode: str = "total",
    include_standby: bool = True,
    daily_breakdown: bool = False,
    time_offset_hours: int = SERVER_TIMEZONE_OFFSET,
    cos_phi: float = DEFAULT_COS_PHI,
    standby_power_w: float = DEFAULT_STANDBY_POWER_W,
    active_power_w: float = DEFAULT_ACTIVE_POWER_W,
    hidden_power_w: float = DEFAULT_HIDDEN_POWER_W,
    sensor_factor: float = DEFAULT_SENSOR_FACTOR,
    dt_max_sec: int = DT_MAX_SEC,
    db_file: str = DB_FILE,
    device_id: str = HEAT_PUMP_DEV_ID,
) -> EnergyResult:
    """Jedno źródło prawdy dla energii i SCOP.

    Zawsze liczy z surowych danych (tabela telemetry).
    Nigdy nie przechodzi przez resample.
    Używana WSZĘDZIE: Panel, Analiza, Porównanie, Telegram.

    Model kalibracji (addytywny + multiplikatywny):
        E_el_real = E_el_sensor × sensor_factor + hidden_power_w × total_hours / 1000
        - hidden_power_w: stały pobór niewidoczny w czujniku (~20W) [W]
        - sensor_factor: korekcja proporcjonalna czujnika (domyślnie 1.0) [×]

    Args:
        date_from: Początek zakresu 'YYYY-MM-DD' lub None (all-time).
        date_to: Koniec zakresu 'YYYY-MM-DD' lub None (do teraz).
        mode: 'total', 'co', 'cwu' — filtr trybu pracy.
        include_standby: Wlicz prąd postoju (standby_power_w) z czujnika.
        daily_breakdown: Zwróć rozbicie na dni w result.daily.
        time_offset_hours: Przesunięcie strefy czasowej (CEST=2).
        cos_phi: Współczynnik mocy.
        standby_power_w: Moc standby WIDOCZNA w czujniku [W].
        active_power_w: Dodatkowa moc active WIDOCZNA w czujniku [W].
        hidden_power_w: Stały pobór NIEWIDOCZNY w czujniku [W]. Addytywny, 24/7.
            Kalibrowany z licznika fizycznego. 0 = brak kalibracji.
        sensor_factor: Korekcja proporcjonalna czujnika [×]. 1.0 = brak korekcji.
        dt_max_sec: Max Δt — przerwy dłuższe = pominięte (360s).
        db_file: Ścieżka do bazy SQLite.
        device_id: ID urządzenia pompy ciepła.

    Returns:
        EnergyResult z obliczonymi energiami, SCOP i statystykami.
    """
    t_start = _time.perf_counter()

    # Wyznacz zakres timestamp
    ts_from, ts_to = _resolve_time_range(date_from, date_to, time_offset_hours, db_file, device_id)

    # Dla dużych zakresów (>14 dni) — iteruj po kawałkach
    # Zapobiega OOM na Fly.io (1GB RAM) przy zimowych danych (6M+ wierszy/sezon)
    range_days = (ts_to - ts_from) / 86400
    if range_days > 14:
        result = _compute_chunked(
            ts_from, ts_to, mode, include_standby, daily_breakdown,
            time_offset_hours, cos_phi, standby_power_w, active_power_w,
            hidden_power_w, sensor_factor, dt_max_sec, db_file, device_id, t_start,
        )
        result.date_from = date_from or ""
        result.date_to = date_to or ""
        return result

    # Pobierz surowe dane
    conn = sqlite3.connect(db_file)
    try:
        pivot = _load_and_pivot(conn, device_id, ts_from, ts_to)
    finally:
        conn.close()

    if pivot.empty:
        return EnergyResult(
            date_from=date_from or "",
            date_to=date_to or "",
            compute_time_ms=(_time.perf_counter() - t_start) * 1000,
        )

    # Oblicz energię z surowych danych
    result = _compute_from_pivot(
        pivot, mode, include_standby, time_offset_hours,
        cos_phi, standby_power_w, active_power_w, hidden_power_w,
        sensor_factor, dt_max_sec, daily_breakdown,
    )

    result.date_from = date_from or ""
    result.date_to = date_to or ""
    result.compute_time_ms = (_time.perf_counter() - t_start) * 1000

    return result


# =============================================================================
# Prywatne funkcje pomocnicze
# =============================================================================


def _resolve_time_range(
    date_from: Optional[str],
    date_to: Optional[str],
    time_offset_hours: int,
    db_file: str = DB_FILE,
    device_id: str = HEAT_PUMP_DEV_ID,
) -> tuple[int, int]:
    """Konwertuje daty string na unix timestamp z uwzględnieniem strefy czasowej."""
    offset_sec = time_offset_hours * 3600

    if date_from is None:
        # Pobierz najstarszy timestamp z bazy zamiast epoch 0
        # (unikamy iterowania po tysiącach tygodni od 1970)
        try:
            conn = sqlite3.connect(db_file) if db_file else None
            if conn:
                row = conn.execute(
                    "SELECT MIN(timestamp) FROM telemetry WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                conn.close()
                ts_from = row[0] if row and row[0] else 0
            else:
                ts_from = 0
        except Exception:
            ts_from = 0
    else:
        # Parsuj datę i oblicz timestamp ręcznie (unikamy datetime.timestamp()
        # bo na Windows rzuca OSError dla dat bliskich epoch)
        dt = datetime.strptime(date_from, "%Y-%m-%d")
        # Oblicz sekundy od epoch ręcznie
        delta = dt - datetime(1970, 1, 1)
        ts_from = int(delta.total_seconds()) - offset_sec

    if date_to is None:
        ts_to = int(_time.time())
    else:
        dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        delta = dt - datetime(1970, 1, 1)
        ts_to = int(delta.total_seconds()) - offset_sec

    return ts_from, ts_to


def _load_and_pivot(
    conn: sqlite3.Connection,
    device_id: str,
    ts_from: int,
    ts_to: int,
) -> pd.DataFrame:
    """Pobiera surowe dane z bazy i pivotuje do formatu szerokokolumnowego.

    Uwzględnia seed row — ostatni znany stan każdego kodu sprzed ts_from
    (rozwiązanie problemu NaN na początku zakresu po ffill).
    """
    codes_str = ",".join(f"'{c}'" for c in ENERGY_CODES)

    # Seed: ostatni znany stan sprzed ts_from dla każdego kodu
    seed_query = f"""
        SELECT code, val_num, val_str, MAX(timestamp) as timestamp
        FROM telemetry
        WHERE device_id = ? AND timestamp < ?
          AND code IN ({codes_str})
        GROUP BY code
    """

    # Główne dane
    main_query = f"""
        SELECT timestamp, code, val_num, val_str
        FROM telemetry
        WHERE device_id = ?
          AND timestamp >= ? AND timestamp <= ?
          AND code IN ({codes_str})
        ORDER BY timestamp
    """

    seed_df = pd.read_sql_query(seed_query, conn, params=(device_id, ts_from))
    main_df = pd.read_sql_query(main_query, conn, params=(device_id, ts_from, ts_to))

    if main_df.empty:
        return pd.DataFrame()

    # Konwertuj val_str boolean na val_num (valve, defrost zapisane jako "True"/"False")
    BOOL_MAP = {"True": 1.0, "true": 1.0, "1": 1.0, "1.0": 1.0,
                "False": 0.0, "false": 0.0, "0": 0.0, "0.0": 0.0}
    for df in [main_df, seed_df]:
        if df.empty:
            continue
        mask = df["val_num"].isna() & df["val_str"].notna()
        df.loc[mask, "val_num"] = df.loc[mask, "val_str"].map(BOOL_MAP)

    # Dodaj seed rows na początek (z timestamp = ts_from - 1 żeby nie kolidowały)
    if not seed_df.empty:
        seed_rows = seed_df[["timestamp", "code", "val_num"]].copy()
        seed_rows["timestamp"] = ts_from - 1
        main_df = pd.concat([seed_rows, main_df], ignore_index=True)

    # Pivot
    pivot = main_df.pivot_table(
        index="timestamp", columns="code", values="val_num", aggfunc="first"
    )
    pivot = pivot.sort_index()

    # UWAGA: temperatury w bazie są JUŻ przeliczone (dzielone przez 10 przez collector).
    # NIE dzielimy ponownie.

    # Forward-fill (wartości nie zmieniające się utrzymują ostatnią wartość)
    pivot = pivot.ffill()

    # Usuń seed row (timestamp < ts_from)
    pivot = pivot[pivot.index >= ts_from]

    # Upewnij się że potrzebne kolumny istnieją
    for col in ENERGY_CODES:
        if col not in pivot.columns:
            pivot[col] = np.nan

    return pivot


def _compute_from_pivot(
    pivot: pd.DataFrame,
    mode: str,
    include_standby: bool,
    time_offset_hours: int,
    cos_phi: float,
    standby_power_w: float,
    active_power_w: float,
    hidden_power_w: float,
    sensor_factor: float,
    dt_max_sec: int,
    daily_breakdown: bool,
) -> EnergyResult:
    """Oblicza energię z pivotowanego DataFrame surowych danych."""
    ts = pivot.index.values.astype(np.float64)
    n = len(ts)

    if n < 2:
        return EnergyResult(sample_count=n)

    # Wektory parametrów (fillna(0) bo po ffill mogą zostać NaN na początku)
    ac_vol = pivot["ac_vol"].fillna(0).values.astype(np.float64)
    ac_curr = pivot["ac_curr"].fillna(0).values.astype(np.float64)
    flow_rate = pivot["flow_rate"].fillna(0).values.astype(np.float64)
    out_temp = pivot["out_water_temp"].fillna(0).values.astype(np.float64)
    in_temp = pivot["in_water_temp"].fillna(0).values.astype(np.float64)
    comp_freq = pivot["comp_freq"].fillna(0).values.astype(np.float64)
    valve = pivot["valve"].fillna(0).values.astype(np.float64)
    defrost = pivot["defrost"].fillna(0).values.astype(np.float64)
    amb_temp = pivot["amb_temp"].values.astype(np.float64)  # NaN OK — osobna obsługa

    # Oblicz moce z czujnika (sensor_factor koryguje proporcjonalnie)
    sbw = standby_power_w if include_standby else 0.0
    p_el_w = compute_p_el_w_array(
        ac_vol, ac_curr, cos_phi, sbw, active_power_w, sensor_factor
    )
    p_th_w = compute_p_th_w_array(flow_rate, out_temp, in_temp)

    # hidden_power_w doliczany jest per interwał (stały pobór 24/7)
    # Nie przechodzi przez sensor_factor — to osobny obwód

    # Klasyfikacja interwałów
    is_cwu = valve >= CWU_VALVE_THRESHOLD
    is_defrost = defrost >= 0.5
    is_comp_on = comp_freq > COMP_FREQ_ON_THRESHOLD

    # Filtr trybu
    if mode == "co":
        mode_mask = ~is_cwu
    elif mode == "cwu":
        mode_mask = is_cwu
    else:
        mode_mask = np.ones(n, dtype=bool)

    # Interwały Δt [s]
    dt_sec = np.diff(ts)

    # Maska: pomiń przerwy > dt_max_sec
    dt_valid = dt_sec <= dt_max_sec
    gaps_skipped = int(np.sum(~dt_valid))

    # Dobowe daty (z przesunięciem strefy)
    dates_local = pd.to_datetime(
        ts + time_offset_hours * 3600, unit="s"
    ).date

    # Inicjalizacja akumulatorów
    e_el_co = 0.0
    e_el_cwu = 0.0
    e_el_standby = 0.0
    e_th_co = 0.0
    e_th_cwu = 0.0
    e_th_defrost = 0.0
    comp_starts = 0
    defrost_count = 0
    comp_seconds = 0.0

    # Daily breakdown
    daily_data: dict = {}

    # Całkowanie prostokątami lewymi (wystarczające przy Δt mediana=3s)
    for i in range(n - 1):
        if not dt_valid[i]:
            continue
        if not mode_mask[i]:
            continue

        dt_s = dt_sec[i]
        dt_h = dt_s / 3600.0

        # Energia elektryczna [kWh]
        # = (sensor P_el × sensor_factor) + hidden_power_w — oba w jednym interwale
        e_el_sensor = p_el_w[i] * dt_h / 1000.0
        e_el_hidden = hidden_power_w * dt_h / 1000.0
        e_el_interval = e_el_sensor + e_el_hidden

        # Energia cieplna [kWh]
        p_th = p_th_w[i]

        if is_defrost[i] and p_th < 0:
            # Defrost — ciepło odebrane z obiegu (ujemne)
            e_th_defrost += p_th * dt_h / 1000.0  # ujemne!
            # Energia el. defrostu → CO (defrost dotyczy ogrzewania)
            e_el_co += e_el_interval
        elif not is_comp_on[i]:
            # Sprężarka nie pracuje → standby
            # E_el standby nie przypisujemy do CO ani CWU
            e_el_standby += e_el_interval
        else:
            e_th_interval = max(0.0, p_th * dt_h / 1000.0)

            if is_cwu[i]:
                e_el_cwu += e_el_interval
                e_th_cwu += e_th_interval
            else:
                e_el_co += e_el_interval
                e_th_co += e_th_interval

        # Starty sprężarki
        if i > 0 and is_comp_on[i] and not is_comp_on[i - 1]:
            comp_starts += 1

        # Starty defrostu
        if i > 0 and is_defrost[i] and not is_defrost[i - 1]:
            defrost_count += 1

        # Czas pracy sprężarki
        if is_comp_on[i]:
            comp_seconds += dt_s

        # Daily breakdown
        if daily_breakdown:
            day = dates_local[i]
            if day not in daily_data:
                daily_data[day] = {
                    "e_el_co": 0.0, "e_el_cwu": 0.0, "e_el_standby": 0.0,
                    "e_th_co": 0.0, "e_th_cwu": 0.0,
                    "e_th_defrost": 0.0,
                    "amb_temp_sum": 0.0, "amb_temp_count": 0,
                    "comp_starts": 0, "defrost_count": 0, "comp_seconds": 0.0,
                }
            dd = daily_data[day]

            if is_defrost[i] and p_th < 0:
                dd["e_th_defrost"] += p_th * dt_h / 1000.0
                dd["e_el_co"] += e_el_interval
            elif not is_comp_on[i]:
                dd["e_el_standby"] += e_el_interval
            elif is_cwu[i]:
                dd["e_el_cwu"] += e_el_interval
                dd["e_th_cwu"] += max(0.0, p_th * dt_h / 1000.0)
            else:
                dd["e_el_co"] += e_el_interval
                dd["e_th_co"] += max(0.0, p_th * dt_h / 1000.0)

            if not np.isnan(amb_temp[i]):
                dd["amb_temp_sum"] += amb_temp[i]
                dd["amb_temp_count"] += 1

            if i > 0 and is_comp_on[i] and not is_comp_on[i - 1]:
                dd["comp_starts"] += 1
            if i > 0 and is_defrost[i] and not is_defrost[i - 1]:
                dd["defrost_count"] += 1
            if is_comp_on[i]:
                dd["comp_seconds"] += dt_s

    # SCOP — liczone przez kanoniczną compute_scop() (jedyne źródło wzoru)
    scop_nominal = compute_scop(
        e_el_co, e_el_cwu, e_el_standby, e_th_co, e_th_cwu, e_th_defrost,
        scope="total", kind="nominal",
    )
    scop_real = compute_scop(
        e_el_co, e_el_cwu, e_el_standby, e_th_co, e_th_cwu, e_th_defrost,
        scope="total", kind="real",
    )

    # Średnia temp. zewn.
    amb_valid = amb_temp[~np.isnan(amb_temp)]
    amb_temp_avg = float(np.mean(amb_valid)) if len(amb_valid) > 0 else 0.0

    # HDD
    if daily_breakdown and daily_data:
        hdd = sum(
            compute_hdd(dd["amb_temp_sum"] / dd["amb_temp_count"])
            for dd in daily_data.values()
            if dd["amb_temp_count"] > 0
        )
    else:
        # Prosta estymacja: HDD = n_days × max(0, 15 - avg_temp)
        n_days = max(1.0, (ts[-1] - ts[0]) / 86400.0)
        hdd = n_days * compute_hdd(amb_temp_avg)

    # Daily DataFrame
    daily_df = None
    if daily_breakdown and daily_data:
        daily_rows = []
        for day, dd in sorted(daily_data.items()):
            el_co = dd["e_el_co"]
            el_cwu = dd["e_el_cwu"]
            el_standby = dd["e_el_standby"]
            th_co = dd["e_th_co"]
            th_cwu = dd["e_th_cwu"]
            th_def = dd["e_th_defrost"]

            avg_t = (
                dd["amb_temp_sum"] / dd["amb_temp_count"]
                if dd["amb_temp_count"] > 0
                else np.nan
            )

            daily_rows.append({
                "date": day,
                "e_el_co": el_co,
                "e_el_cwu": el_cwu,
                "e_el_standby": el_standby,
                "e_th_co": th_co,
                "e_th_cwu": th_cwu,
                "e_th_defrost": th_def,
                "scop_nominal": compute_scop(el_co, el_cwu, el_standby, th_co, th_cwu, th_def,
                                             scope="total", kind="nominal"),
                "scop_real": compute_scop(el_co, el_cwu, el_standby, th_co, th_cwu, th_def,
                                          scope="total", kind="real"),
                "hdd": compute_hdd(avg_t) if not np.isnan(avg_t) else 0.0,
                "amb_temp_avg": avg_t,
                "comp_starts": dd["comp_starts"],
                "defrost_count": dd["defrost_count"],
                "comp_hours": dd["comp_seconds"] / 3600.0,
            })
        daily_df = pd.DataFrame(daily_rows)

    return EnergyResult(
        e_el_co=e_el_co,
        e_el_cwu=e_el_cwu,
        e_el_standby=e_el_standby,
        e_th_co=e_th_co,
        e_th_cwu=e_th_cwu,
        e_th_defrost=e_th_defrost,
        scop_nominal=scop_nominal,
        scop_real=scop_real,
        comp_starts=comp_starts,
        comp_hours=comp_seconds / 3600.0,
        defrost_count=defrost_count,
        amb_temp_avg=amb_temp_avg,
        hdd=hdd,
        daily=daily_df,
        sample_count=n,
        gaps_skipped=gaps_skipped,
    )


def _compute_chunked(
    ts_from: int,
    ts_to: int,
    mode: str,
    include_standby: bool,
    daily_breakdown: bool,
    time_offset_hours: int,
    cos_phi: float,
    standby_power_w: float,
    active_power_w: float,
    hidden_power_w: float,
    sensor_factor: float,
    dt_max_sec: int,
    db_file: str,
    device_id: str,
    t_start: float,
) -> EnergyResult:
    """Oblicza energię w kawałkach (po tygodniu) dla dużych zakresów.

    Stałe zużycie pamięci ~2-5 MB niezależnie od zakresu.
    Obsługuje daily_breakdown — zbiera daily DataFrames z chunków.
    """
    CHUNK_SEC = 7 * 86400  # 1 tydzień

    # Akumulatory
    total = EnergyResult()
    daily_frames: list = []

    chunk_from = ts_from
    while chunk_from < ts_to:
        chunk_to = min(chunk_from + CHUNK_SEC, ts_to)

        # Konwertuj timestamp na date string
        d_from = datetime(1970, 1, 1) + timedelta(seconds=chunk_from + time_offset_hours * 3600)
        d_to = datetime(1970, 1, 1) + timedelta(seconds=chunk_to + time_offset_hours * 3600)
        d_from_str = d_from.strftime("%Y-%m-%d")
        d_to_str = d_to.strftime("%Y-%m-%d")

        chunk_result = compute_energy(
            date_from=d_from_str,
            date_to=d_to_str,
            mode=mode,
            include_standby=include_standby,
            daily_breakdown=daily_breakdown,
            time_offset_hours=time_offset_hours,
            cos_phi=cos_phi,
            standby_power_w=standby_power_w,
            active_power_w=active_power_w,
            hidden_power_w=hidden_power_w,
            sensor_factor=sensor_factor,
            dt_max_sec=dt_max_sec,
            db_file=db_file,
            device_id=device_id,
        )

        # Sumuj akumulatory
        total.e_el_co += chunk_result.e_el_co
        total.e_el_cwu += chunk_result.e_el_cwu
        total.e_el_standby += chunk_result.e_el_standby
        total.e_th_co += chunk_result.e_th_co
        total.e_th_cwu += chunk_result.e_th_cwu
        total.e_th_defrost += chunk_result.e_th_defrost
        total.comp_starts += chunk_result.comp_starts
        total.comp_hours += chunk_result.comp_hours
        total.defrost_count += chunk_result.defrost_count
        total.sample_count += chunk_result.sample_count
        total.gaps_skipped += chunk_result.gaps_skipped
        total.hdd += chunk_result.hdd

        # Zbieraj daily DataFrames
        if daily_breakdown and chunk_result.daily is not None and not chunk_result.daily.empty:
            daily_frames.append(chunk_result.daily)

        # Średnia ważona temp. zewn.
        if chunk_result.sample_count > 0 and total.sample_count > 0:
            prev_samples = total.sample_count - chunk_result.sample_count
            if prev_samples > 0:
                total.amb_temp_avg = (
                    total.amb_temp_avg * prev_samples + chunk_result.amb_temp_avg * chunk_result.sample_count
                ) / total.sample_count
            else:
                total.amb_temp_avg = chunk_result.amb_temp_avg

        chunk_from = chunk_to

    # Przelicz SCOP z akumulatorów przez kanoniczną compute_scop()
    # (mianownik = CO + CWU + standby — identycznie jak w wersji single)
    total.scop_nominal = compute_scop(
        total.e_el_co, total.e_el_cwu, total.e_el_standby,
        total.e_th_co, total.e_th_cwu, total.e_th_defrost,
        scope="total", kind="nominal",
    )
    total.scop_real = compute_scop(
        total.e_el_co, total.e_el_cwu, total.e_el_standby,
        total.e_th_co, total.e_th_cwu, total.e_th_defrost,
        scope="total", kind="real",
    )

    # Połącz daily DataFrames
    if daily_breakdown and daily_frames:
        combined = pd.concat(daily_frames, ignore_index=True)
        # Deduplikuj dni na granicach chunków (ten sam dzień może być w dwóch chunkach)
        if "date" in combined.columns:
            agg_dict = {
                "e_el_co": "sum", "e_el_cwu": "sum", "e_el_standby": "sum",
                "e_th_co": "sum", "e_th_cwu": "sum",
                "e_th_defrost": "sum",
                "comp_starts": "sum", "defrost_count": "sum",
                "comp_hours": "sum",
                "amb_temp_avg": "mean", "hdd": "mean",
            }
            combined = combined.groupby("date").agg(agg_dict).reset_index()
            # Przelicz SCOP per dzień przez kanoniczną compute_scop()
            if "e_el_standby" not in combined.columns:
                combined["e_el_standby"] = 0.0
            combined["scop_nominal"] = combined.apply(
                lambda r: compute_scop(
                    r["e_el_co"], r["e_el_cwu"], r["e_el_standby"],
                    r["e_th_co"], r["e_th_cwu"], r["e_th_defrost"],
                    scope="total", kind="nominal",
                ), axis=1,
            )
            combined["scop_real"] = combined.apply(
                lambda r: compute_scop(
                    r["e_el_co"], r["e_el_cwu"], r["e_el_standby"],
                    r["e_th_co"], r["e_th_cwu"], r["e_th_defrost"],
                    scope="total", kind="real",
                ), axis=1,
            )
        total.daily = combined

    total.compute_time_ms = (_time.perf_counter() - t_start) * 1000
    return total

"""Zakładka: Doradca Krzywej Grzewczej — analiza krótko- i długoterminowa.

Dobiera 2-punktową krzywą pogodową (T_low przy -15°C, T_high przy +15°C) tak,
by pompa grzała możliwie CIĄGLE z MINIMALNĄ wymaganą temperaturą wody.

Źródło sygnału żądania grzania: work_mode (termostat pokojowy ON/OFF nie ma DP):
- hot_water      → termostat OFF (tylko CWU)
- heat_hot_water → termostat ON  (żąda CO)
Filtr strefy: liczymy tylko strefę 1 lub obie (zone_select ∈ {1,3}); strefa 2
ma stałą temperaturę wody (nie z krzywej) i jest pomijana.

Krótkoterminowa (wybrany zakres) — co dzieje się TERAZ, przy obecnym paśmie temp.
Długoterminowa (całe dane) — dopasowanie obu końców; wymaga szerokiego zakresu
temperatur zewnętrznych (progi rozpiętości 8/15°C).

Analiza = czysty core app/core/heating_curve.py (bez logiki w UI).
"""
import numpy as np
import pandas as pd
import streamlit as st

from datetime import date, datetime

from app.config import SERVER_TIMEZONE_OFFSET
from app.core.energy import WORK_MODE_CODES
from app.core.heating_curve import (
    detect_heating_episodes,
    compute_duty_bins,
    recommend_curve_adjustment,
    curve_water_temp,
    CURVE_POINT_LOW_AMB,
    CURVE_POINT_HIGH_AMB,
    TARGET_DUTY_PCT,
    ZONE_SELECT_Z2,
)
from app.services.database import get_setting, set_setting

# Odwrotna mapa kodów work_mode (liczbowy → nazwa), do odczytu z pivota.
_CODE_TO_WORK_MODE = {v: k for k, v in WORK_MODE_CODES.items()}


def _curve_setting_keys(pump_id: str) -> dict:
    """Buduje klucze `settings` dla nastaw krzywej PER POMPA.

    Każda pompa ma własną krzywą fizyczną — nastawy nie mogą być współdzielone.
    Klucze: curve_low_temp_<pump_id>, curve_high_temp_<pump_id>,
    curve_room_target_<pump_id>, curve_changed_at_<pump_id>.

    Returns:
        {"low": ..., "high": ..., "room": ..., "changed_at": ...} — nazwy kluczy.
    """
    pid = pump_id or "default"
    return {
        "low": f"curve_low_temp_{pid}",
        "high": f"curve_high_temp_{pid}",
        "room": f"curve_room_target_{pid}",
        "changed_at": f"curve_changed_at_{pid}",
    }


def _date_to_epoch(d: date) -> int:
    """Konwertuje datę LOKALNĄ (początek dnia) na epoch UTC.

    Używa wzoru odpornego na Windows (bez datetime.timestamp()) i korekty strefy:
    czas lokalny = UTC + offset → epoch = (dt − 1970) − offset·3600.
    """
    local_midnight = datetime(d.year, d.month, d.day)
    seconds = (local_midnight - datetime(1970, 1, 1)).total_seconds()
    return int(seconds - SERVER_TIMEZONE_OFFSET * 3600)


def _parse_iso_date(s: str):
    """Parsuje 'YYYY-MM-DD' na date. None gdy pusty/niepoprawny."""
    if not s or not s.strip():
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None



def _extract_events(df_pivot: pd.DataFrame):
    """Wyciąga z pivota zdarzenia work_mode, próbki amb_temp i zone_select.

    Pivot ma work_mode jako kod liczbowy (ffill — ciągły). Zdarzenia odtwarzamy
    jako punkty ZMIANY work_mode (start każdego nowego trybu).

    Returns:
        (events, amb_samples, zone_samples) — listy krotek (ts_epoch, wartość).
        events: (ts, work_mode_str); amb_samples: (ts, °C); zone_samples: (ts, kod).
        Puste listy, gdy brak kolumn/danych.
    """
    if df_pivot is None or df_pivot.empty or "timestamp" not in df_pivot.columns:
        return [], [], []

    df = df_pivot.sort_values("timestamp")

    # --- work_mode: punkty zmiany ---
    events = []
    if "work_mode" in df.columns:
        wm = df[["timestamp", "work_mode"]].dropna()
        if not wm.empty:
            changed = wm["work_mode"].ne(wm["work_mode"].shift())
            for ts, code in zip(wm.loc[changed, "timestamp"], wm.loc[changed, "work_mode"]):
                name = _CODE_TO_WORK_MODE.get(float(code))
                if name:
                    events.append((int(ts), name))

    # --- amb_temp: wszystkie próbki ---
    amb_samples = []
    if "amb_temp" in df.columns:
        amb = df[["timestamp", "amb_temp"]].dropna()
        amb_samples = [(int(t), float(v)) for t, v in zip(amb["timestamp"], amb["amb_temp"])]

    # --- zone_select: wszystkie próbki ---
    zone_samples = []
    if "zone_select" in df.columns:
        z = df[["timestamp", "zone_select"]].dropna()
        zone_samples = [(int(t), float(v)) for t, v in zip(z["timestamp"], z["zone_select"])]

    return events, amb_samples, zone_samples


def _run_analysis(df_pivot, t_low, t_high, target_room, since_ts=None):
    """Uruchamia pełny łańcuch core na jednym zbiorze danych.

    Returns:
        (recommendation, episodes, zone2_count) lub (None, [], zone2_count).
        zone2_count = liczba epizodów grzania SAMEJ strefy 2 (diagnostyka —
        strefa 2 ma stałą temp. wody, nie podlega krzywej strefy 1).
    """
    events, amb_samples, zone_samples = _extract_events(df_pivot)
    if not events:
        return None, [], 0

    # Epizody objęte krzywą (strefa 1 lub obie)
    episodes = detect_heating_episodes(
        events, amb_samples=amb_samples, zone_samples=zone_samples,
        since_ts=since_ts,
    )
    # Epizody SAMEJ strefy 2 (diagnostyka) — tylko gdy mamy dane strefy
    zone2_count = 0
    if zone_samples:
        z2 = detect_heating_episodes(
            events, amb_samples=amb_samples, zone_samples=zone_samples,
            curve_zones=frozenset({ZONE_SELECT_Z2}),
            since_ts=since_ts,
        )
        zone2_count = len(z2)

    bins = compute_duty_bins(episodes)
    rec = recommend_curve_adjustment(
        bins, t_low=t_low, t_high=t_high, target_room_temp=target_room,
    )
    return rec, episodes, zone2_count


def _render_recommendation(rec, title: str, zone2_count: int = 0, n_episodes: int = 0):
    """Renderuje wynik rekomendacji (komunikaty + tabela binów)."""
    st.markdown(f"#### {title}")

    # Diagnostyka strefy 2: krzywa dotyczy strefy 1, a strefa 2 ma stałą temp. wody.
    # Gdy grzeje głównie strefa 2 (mało/zero epizodów Z1), rekomendacja byłaby myląca.
    if zone2_count > 0 and zone2_count >= n_episodes:
        st.warning(
            f"🔁 W tym zakresie grzeje głównie **strefa 2** "
            f"({zone2_count} epizodów Z2 vs {n_episodes} epizodów strefy 1/obu stref). "
            "Strefa 2 ma **stałą temperaturę wody** — nie podlega krzywej grzewczej. "
            "Analiza krzywej (strefa 1) będzie wiarygodna dopiero, gdy strefa 1 zacznie "
            "realnie grzać (np. w sezonie grzewczym). Rekomendacje poniżej liczone są "
            "wyłącznie z nielicznych epizodów strefy 1 — traktuj je ostrożnie."
        )

    if rec is None:
        st.info(
            "Brak zdarzeń trybu pracy (work_mode) w tym zakresie — nie wykryto żadnego "
            "żądania grzania przez termostat pokojowy. Analiza będzie możliwa, gdy pompa "
            "pogrzeje w trybie CO (strefa 1 lub obie)."
        )
        return

    # Zakres temperatur + jakość danych
    if rec.amb_min_observed is not None:
        st.caption(
            f"Zakres temperatur zewnętrznych w epizodach grzania: "
            f"**{rec.amb_min_observed:.1f}°C … {rec.amb_max_observed:.1f}°C** "
            f"(rozrzut {rec.amb_range:.1f}°C)"
        )

    # Komunikaty (jakość danych + rekomendacja) — kolorowane wg prefiksu
    for msg in rec.messages:
        if msg.startswith("✅"):
            st.success(msg)
        elif msg.startswith("⚠️"):
            st.warning(msg)
        elif msg.startswith("🔧"):
            st.error(msg)
        else:
            st.info(msg)

    # Konkretne wartości docelowe (podsumowanie)
    if rec.has_recommendation:
        cols = st.columns(2)
        with cols[0]:
            if rec.new_t_low is not None and rec.t_low is not None:
                st.metric(
                    f"Punkt {CURVE_POINT_LOW_AMB:.0f}°C (T_low)",
                    f"{rec.new_t_low:.1f}°C",
                    delta=f"{rec.new_t_low - rec.t_low:+.1f}°C",
                    delta_color="inverse",
                )
        with cols[1]:
            if rec.new_t_high is not None and rec.t_high is not None:
                st.metric(
                    f"Punkt +{CURVE_POINT_HIGH_AMB:.0f}°C (T_high)",
                    f"{rec.new_t_high:.1f}°C",
                    delta=f"{rec.new_t_high - rec.t_high:+.1f}°C",
                    delta_color="inverse",
                )

    # Tabela binów duty cycle
    valid = [b for b in rec.bins if b.total_sec >= 1800]  # min. 30 min, by pokazać
    if valid:
        rows = []
        for b in valid:
            if b.duty_pct >= TARGET_DUTY_PCT:
                status = "✅ ciągłe"
            elif b.duty_pct >= TARGET_DUTY_PCT * 0.7:
                status = "⚠️ taktuje"
            else:
                status = "🔴 za ciepła woda"
            bar_len = int(b.duty_pct / 5)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            rows.append({
                "Temp. zewn.": f"{b.amb_min:.0f}…{b.amb_max:.0f}°C",
                "Duty cycle": f"{bar} {b.duty_pct:.0f}%",
                "Epizody": b.n_episodes,
                "Śr. epizod": f"{b.avg_episode_sec / 60:.0f} min",
                "Czas łącznie": f"{b.total_sec / 3600:.1f} h",
                "Status": status,
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "**Duty cycle** = udział czasu, w którym termostat żądał grzania. "
            f"≥ {TARGET_DUTY_PCT:.0f}% = praca ciągła z minimalną wodą (cel). "
            "Niski duty = woda za ciepła (termostat szybko odcina → taktowanie)."
        )
    else:
        st.caption("Za mało danych per przedział temperatury, by pokazać tabelę duty cycle.")


def render(df_pivot_all: pd.DataFrame, df_pivot_range: pd.DataFrame = None,
           weather_df: pd.DataFrame = None, pump_id: str = "default"):
    """Renderuje zakładkę Doradca Krzywej Grzewczej.

    Args:
        df_pivot_all: Pivot z CAŁYCH danych (analiza długoterminowa).
        df_pivot_range: Pivot z wybranego zakresu (analiza krótkoterminowa).
            Gdy None → krótkoterminowa pominięta.
        weather_df: Dane pogodowe (opcjonalne, obecnie nieużywane w nowej analizie).
        pump_id: Identyfikator pompy — nastawy krzywej (T_low/T_high/temp. pokojowa)
            są zapisywane PER POMPA (osobne klucze w `settings`). Każda pompa ma
            własną krzywą fizyczną, więc nastawy nie mogą być współdzielone.
    """
    st.subheader("📈 Doradca Krzywej Grzewczej")
    st.caption(
        "Dobiera krzywą pogodową (2 punkty: temp. wody przy **−15°C** i **+15°C**) tak, by pompa "
        "grzała możliwie **ciągle z minimalną** temperaturą wody. Sygnał żądania grzania czytany "
        "z trybu pracy pompy (termostat pokojowy ON/OFF). Analizowana tylko **strefa 1 / obie strefy** "
        "— strefa 2 ma stałą temperaturę wody."
    )

    # Klucze nastaw PER POMPA (każda pompa ma własną krzywą).
    _keys = _curve_setting_keys(pump_id)
    k_low, k_high, k_room, k_changed = _keys["low"], _keys["high"], _keys["room"], _keys["changed_at"]

    # --- Formularz nastaw krzywej + temp. pokojowa + data zmiany ---
    with st.expander("⚙️ Nastawy krzywej i temperatura pokojowa", expanded=True):
        st.caption(
            "Wpisz aktualne nastawy krzywej ze sterownika pompy oraz zadaną temperaturę "
            "w pomieszczeniu. **Data ostatniej zmiany krzywej** ogranicza analizę do danych "
            "wygenerowanych przez aktualną krzywą (bez mieszania ze starymi nastawami)."
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            t_low_str = st.text_input(
                "Temp. wody przy **−15°C** [°C]",
                value=get_setting(k_low, ""),
                key=f"curve_low_input_{pump_id}", placeholder="np. 43",
            )
        with c2:
            t_high_str = st.text_input(
                "Temp. wody przy **+15°C** [°C]",
                value=get_setting(k_high, ""),
                key=f"curve_high_input_{pump_id}", placeholder="np. 28",
            )
        with c3:
            room_str = st.text_input(
                "Zadana temp. **pokojowa** [°C]",
                value=get_setting(k_room, ""),
                key=f"curve_room_input_{pump_id}", placeholder="np. 21",
            )

        # Data ostatniej zmiany krzywej (edytowalna; auto-ustawiana przy zapisie)
        saved_changed = _parse_iso_date(get_setting(k_changed, ""))
        changed_date = st.date_input(
            "Data ostatniej zmiany krzywej",
            value=saved_changed,  # None → puste pole (brak filtra = całe dane)
            key=f"curve_changed_input_{pump_id}",
            format="YYYY-MM-DD",
        )
        st.caption(
            "Pozostaw puste, by analizować całą historię. Przy zapisie nastaw data "
            "ustawia się automatycznie na dziś (możesz ją nadpisać ręcznie)."
        )

        if st.button("💾 Zapisz nastawy", key=f"save_curve_{pump_id}"):
            set_setting(k_low, t_low_str.strip())
            set_setting(k_high, t_high_str.strip())
            set_setting(k_room, room_str.strip())
            # Data zmiany: użyj wpisanej ręcznie, inaczej auto „dziś".
            new_changed = changed_date if changed_date else date.today()
            set_setting(k_changed, new_changed.isoformat())
            st.success(f"Zapisano nastawy krzywej. Data zmiany: {new_changed.isoformat()}.")
            st.rerun()

    def _parse(s):
        try:
            return float(s.strip()) if s and s.strip() else None
        except ValueError:
            return None

    t_low = _parse(t_low_str)
    t_high = _parse(t_high_str)
    target_room = _parse(room_str)

    # since_ts: epoch od daty zmiany krzywej (None → brak filtra, całe dane)
    since_ts = _date_to_epoch(changed_date) if changed_date else None
    if since_ts is not None:
        st.caption(f"🔎 Analiza ograniczona do danych od **{changed_date.isoformat()}** (aktualna krzywa).")

    # Podgląd krzywej (jeśli podano oba końce)
    if t_low is not None and t_high is not None:
        preview = " · ".join(
            f"{amb:+d}°C→{curve_water_temp(amb, t_low, t_high):.0f}°C"
            for amb in (-15, -7, 0, 7, 15)
        )
        st.caption(f"Twoja krzywa: {preview}")

    if df_pivot_all is None or df_pivot_all.empty:
        st.info("Brak danych do analizy krzywej grzewczej.")
        return

    # --- Analiza długoterminowa (dane od zmiany krzywej) ---
    rec_long, eps_long, z2_long = _run_analysis(
        df_pivot_all, t_low, t_high, target_room, since_ts=since_ts,
    )
    _render_recommendation(
        rec_long,
        "📅 Analiza długoterminowa — dobór obu końców krzywej",
        zone2_count=z2_long, n_episodes=len(eps_long),
    )

    st.divider()

    # --- Analiza krótkoterminowa (wybrany zakres, też od zmiany krzywej) ---
    if df_pivot_range is not None and not df_pivot_range.empty:
        rec_short, eps_short, z2_short = _run_analysis(
            df_pivot_range, t_low, t_high, target_room, since_ts=since_ts,
        )
        _render_recommendation(
            rec_short,
            "⏱️ Analiza krótkoterminowa (wybrany zakres) — bieżące warunki",
            zone2_count=z2_short, n_episodes=len(eps_short),
        )
    else:
        st.caption("Analiza krótkoterminowa: brak danych w wybranym zakresie.")

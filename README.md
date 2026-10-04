# Tuya Heat Pump Monitor v2

Monitoring zużycia energii i efektywności (SCOP) pompy ciepła na podstawie telemetrii Tuya. Projekt obejmuje rdzeń obliczeniowy, collector danych z Tuya Pulsar, dashboard Streamlit oraz powiadomienia Telegram.

Fundament architektury: jedna funkcja `compute_energy()` jako jedyne źródło prawdy dla energii i jedna funkcja `compute_scop()` jako jedyne źródło wzoru SCOP — używane wszędzie (dashboard, raporty, Telegram), więc wyniki są zawsze spójne.

## Szybki start

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

Uruchomienie lokalne (collector + dashboard): patrz `start_local.bat` / `start.sh`.

## Struktura projektu

```
tuya_v2/
├── main.py                  — collector: 4 wątki (Tuya Pulsar, pogoda, watchdog, raport dzienny)
├── Panel.py                 — dashboard Streamlit: strona główna (Panel)
├── pages/                   — pozostałe strony dashboardu
│   ├── 1_Bilans.py          — bilans energii i SCOP
│   ├── 2_Analiza.py         — analiza parametrów (hydraulika, sprężarka, defrost, krzywa grzewcza)
│   ├── 3_Porownanie.py      — porównanie okresów
│   ├── 4_Licznik.py         — licznik energii (wykres mocy, ręczne odczyty)
│   └── 5_Wiedza.py          — baza wiedzy
├── app/
│   ├── config.py            — stałe, progi, parametry czujników, lista PUMPS, get_timezone_offset() (DST)
│   ├── core/                — czysty Python, bez zależności od Streamlit
│   │   ├── physics.py       — formuły fizyczne (P_el, P_th, COP, HDD)
│   │   ├── energy.py        — compute_energy() + compute_scop() (kanoniczne źródła prawdy)
│   │   ├── calibration.py   — kalibracja z licznika (hidden_power_w + sensor_factor)
│   │   ├── heating_curve.py — doradca krzywej grzewczej (epizody work_mode, duty cycle, rekomendacja)
│   │   └── models.py        — modele danych (EnergyResult)
│   ├── services/            — integracje i I/O
│   │   ├── tuya_client.py   — klient Tuya Pulsar, DeadbandFilter, deduplikacja add_ele
│   │   ├── database.py      — dostęp do SQLite, kalibracja (settings), licznik zdalny
│   │   ├── notifier.py      — powiadomienia Telegram (alerty + raport dzienny)
│   │   ├── analytics.py     — diagnostyka (cykle krótkie, inwerter, krzywa grzewcza)
│   │   └── exporter.py      — eksport do CSV
│   └── ui/                  — warstwa UI (labels, styles, helpers, cache Streamlit)
├── tests/                   — testy jednostkowe i integracyjne
├── data/                    — baza SQLite (tuya_telemetry.db, WAL)
├── requirements.txt
├── pyproject.toml
├── Dockerfile               — obraz produkcyjny (Python 3.12)
├── fly.toml                 — konfiguracja deploy Fly.io
└── README.md
```

## Kluczowe decyzje projektowe

- **Energia liczona z surowych danych** — nigdy z próbek po resamplingu (resampling wyłącznie do wizualizacji)
- **Jedna funkcja `compute_energy()`** — używana wszędzie (dashboard, raporty, Telegram)
- **Jedna funkcja `compute_scop()`** — jedyne źródło wzoru SCOP (scope: total/co/cwu, kind: real/nominal); wszystkie strony i silnik jej używają, więc wyniki są spójne
- **Wykrywanie pracy pompy po pompie wody** — `is_pump_running()` (`app/core/physics.py`, próg `flow_rate > FLOW_RATE_ON_THRESHOLD`) to jedno źródło stanu „agregat pracuje" (interwał pollingu, tło UI, status na żywo). Pompa wody rusza ~2 min przed sprężarką i pracuje ~2 min po niej, więc obejmuje pełny cykl. `comp_freq > 5` pozostaje osobno jako „sprężarka pracuje" (energia, SCOP, liczenie startów) — dwa różne pojęcia
- **Klasyfikacja CO/CWU po `work_mode`** (DP 109), nie po zaworze. `valve` (DP 117) to zawór **4-drożny** (rewers grzanie/chłodzenie) i koreluje ze sprężarką — NIE rozróżnia CO/CWU. Podział wyznacza `_classify_cwu_mask()` (`app/core/energy.py`, jedyne źródło prawdy): `heat`→CO, `hot_water`→CWU, `heat_hot_water`→podział wg **histerezy zasobnika** (wejście w CWU gdy `hot_water_temp_set − tank_temp > 5°C`, wyjście dopiero gdy tank osiągnie temperaturę zadaną, tj. różnica ≤ 0°C — CWU ma priorytet i jest grzane do końca). Chłodzenie (`cool`/`cool_hot_water`) wyłączone sprzętowo, pomijane w bilansie
- **Obsługa wielu pomp** — lista `PUMPS` w `config.py` (id/name/device_id/meter_id/thermo_id); wybór pompy renderowany wspólnym `render_pump_selector()` (`app/ui/helpers.py`) i zapamiętany trwale w trzech warstwach: `st.query_params` (`?pump=`, F5 i link), `st.session_state` (nawigacja między stronami), oraz **localStorage przeglądarki** (`streamlit-local-storage`) — wybór przeżywa zamknięcie i ponowne otwarcie przeglądarki, PER URZĄDZENIE (dwie osoby na różnych sprzętach mają niezależny wybór; świadomie nie trzymany globalnie w bazie). Licznik energii powiązany z pompą (`meter_id`) — pompa bez licznika (`meter_id=None`) pokazuje „brak", SCOP z sondy prądowej działa normalnie. Wszystkie funkcje odczytu przyjmują `device_id`/`meter_id`. Opcjonalny **zewnętrzny termometr** temp. powietrza w pomieszczeniu powiązany z pompą (`thermo_id`, to samo konto Tuya → istniejący strumień Pulsar) — temperatura (`va_temperature`, skala ×0.1 jak pompa) dostępna na wykresie „Przebieg parametrów" Panelu; pompa bez termometru (`thermo_id=None`) go nie pokazuje
- **Obliczenia w kawałkach (chunked)** — dla dużych zakresów (zima: 6M+ próbek); suma daily równa się total, single vs chunked daje ten sam SCOP
- **Brak dodatkowych tabel wyników w bazie** — wyniki obliczane na żądanie
- **Czysty Python w rdzeniu** — `app/core/` bez zależności od Streamlit; UI i usługi mogą używać Streamlit/requests
- **Addytywny model kalibracji** (`hidden_power_w` + `sensor_factor`) zamiast pojedynczego mnożnika — działa poprawnie zarówno latem, jak i zimą
- **Sonda prądowa mierzy tylko kompresor** — `hidden_power_w` kompensuje pompę obiegową, elektronikę i inne stałe odbiorniki
- **Automatyczne przeliczanie strefy czasowej** — `get_timezone_offset()` z `zoneinfo` dla `Europe/Warsaw` (CEST=+2, CET=+1), bez ręcznej zmiany przy DST
- **Konwersja dat na epoch UTC** unika `datetime.timestamp()` (problemy na Windows) — wzór `(dt - datetime(1970,1,1)).total_seconds()` minus offset; czas lokalny = UTC + offset
- **Doradca krzywej grzewczej** (`app/core/heating_curve.py`) — dobiera 2-punktową krzywą pogodową (temp. wody przy **−15°C** i **+15°C**) tak, by pompa grzała ciągle z minimalną wodą. Pompa nie raportuje temp. wody z krzywej → odtwarzamy ją z nastaw formularza. Żądanie grzania termostatu pokojowego (brak DP) czytane z `work_mode`: `hot_water`→OFF, `heat_hot_water`→ON; przejście = epizod grzania. Analiza liczy **duty cycle** per przedział temp. zewnętrznej (cel > 85% = praca ciągła); niski duty = woda za ciepła → rekomendacja „obniż T_low/T_high o X°C". Krzywa dotyczy **strefy 1** — epizody samej strefy 2 (stała temp. wody) pomijane (`zone_select`). Jakość rekomendacji wg rozpiętości temp. (< 8°C: tylko jeden koniec; ≥ 15°C: oba końce). Analiza krótkoterminowa (wybrany zakres) i długoterminowa (całe dane), liczona na żądanie — bez tabel wyników. Nastawy krzywej (T_low/T_high/temp. pokojowa) **powiązane z pompą** — osobne klucze `settings` per `pump_id` (każda pompa ma własną krzywą fizyczną). **Data ostatniej zmiany krzywej** (`curve_changed_at_<pump_id>`, auto-ustawiana przy zapisie, edytowalna) ogranicza obie analizy do danych wygenerowanych przez AKTUALNĄ krzywą (`since_ts` w `detect_heating_episodes`) — bez mieszania starych i nowych nastaw. Pusta data = cała historia

## Model kalibracji

Sonda prądowa mierzy tylko pobór kompresora. Aby uzyskać rzeczywiste zużycie całej pompy ciepła (kompresor + pompa obiegowa + elektronika), stosujemy model addytywny:

```
E_el_real = E_el_sensor × sensor_factor + hidden_power_w × total_hours / 1000
```

Parametry:

- **`hidden_power_w`** — stała moc ukryta (pompa obiegowa, sterownik, zawory), kalibrowana z fizycznego licznika energii
- **`sensor_factor`** — ~1.0, koryguje błąd proporcjonalny sondy prądowej

Kalibracja ma **jedno źródło prawdy**: tabela `settings` w bazie, czytana przez `load_calibration()` w `app/services/database.py`. Dashboard (suwaki) i raport Telegram czytają te same wartości (`DEFAULT_*` w `config.py` to tylko fallback dla pustej bazy).

### Dlaczego nie pojedynczy mnożnik?

Pojedynczy współczynnik korekcyjny (`E_real = E_sensor × factor`) nie działa poprawnie w obu sezonach:

| Sezon | Kompresor | Ukryta moc | Udział ukrytej mocy |
|-------|-----------|------------|---------------------|
| **Zima** | ~2000W | ~20W | ~0.1% — mnożnik ≈ 1.001 |
| **Lato** (CWU) | ~100W krótko | ~20W ciągle | ~15% — mnożnik ≈ 1.15 |

Jeden mnożnik skalibrowany zimą zaniża latem (i odwrotnie). Model addytywny rozdziela te dwa źródła błędu i działa poprawnie przez cały rok.

## Licznik energii Tuya

Do obwodu pompy podłączony jest inteligentny licznik energii Tuya (odczyt zdalny tym samym strumieniem Pulsar). Dane współistnieją z pompą w tabeli `telemetry`, rozróżniane po `device_id`.

- **`add_ele`** — przyrost energii; skala potwierdzona empirycznie: 1 jednostka = 1 Wh (×0.001 kWh). Sumowany w oknie czasu przez `get_remote_meter_energy()` — pewniejsze źródło całkowitego zużycia niż całka ZOH z `cur_power` (całkuje sam licznik, odporny na dziury w telemetrii).
- **Deduplikacja retransmisji** — licznik wysyła każdy raport `add_ele` podwojony (ts ±1 s). Collector (`tuya_client.py`) pomija duplikat po czasie zdarzenia (`ADD_ELE_DEDUP_SEC = 3`); realne raporty dzieli ~1800 s.
- **`cur_power`** — moc czynna [W] (skala ×0.1 W), przez `DeadbandFilter`; używana do wykresu mocy chwilowej.

## Testy

198 testów pokrywających:

- formuły fizyczne (COP, moc cieplna, przepływ, HDD)
- wykrywanie pracy pompy po pompie wody (`is_pump_running()` — próg flow_rate, None/typy)
- obsługę wielu pomp (`get_pump`/`list_pumps`, zbiory device_id, pompa bez licznika, termometr `thermo_id`/`THERMO_DEV_IDS`)
- trwały wybór pompy (`get_selected_pump`/`persist_pump_choice` — priorytety query_params > session_state > localStorage > default, przetrwanie zamknięcia przeglądarki)
- konfigurowalny wykres parametrów (`get_chart_params`/`persist_chart_params` — localStorage, ręczny zapis, fallback na default)
- obliczenia energii (`compute_energy()`) i kalibrację (addytywny model, nie mnożnik)
- wzór SCOP (`compute_scop()` — scope total/co/cwu, kind real/nominal, znak defrostu)
- obsługę cykli rozmrażania (defrost) i filtrowanie trybów pracy
- rozbicie dzienne (daily breakdown)
- spójność SCOP (single vs chunked, suma daily == total)
- klient Tuya (deduplikacja `add_ele`)
- licznik zdalny (`get_remote_meter_energy` — skala Wh→kWh)
- raport dzienny Telegram (`build_daily_report`)
- doradcę krzywej grzewczej (`app/core/heating_curve.py`): model krzywej (interpolacja/ekstrapolacja, nachylenie), wykrywanie epizodów grzania z `work_mode`, filtr strefy (Z1/obie liczone, sama Z2 pomijana), duty cycle per przedział temp., rekomendacja T_low/T_high (progi rozpiętości 8/15°C, cel duty > 85%), nastawy per pompa i filtr daty zmiany krzywej (`since_ts`)

## Deploy

Produkcja: Fly.io (app `scop`, region `ams`, wolumen `/data`), obraz Docker na Pythonie 3.12. Baza kierowana na wolumen przez `DB_FILE`. Sekrety Tuya/Telegram jako Fly.io secrets.

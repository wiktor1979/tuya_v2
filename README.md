# Tuya Heat Pump Monitor v2 — Silnik Obliczeniowy

Rdzeń obliczeniowy do monitorowania zużycia energii pompy ciepła. Jedna funkcja `compute_energy()` jako jedyne źródło prawdy dla energii, a `compute_scop()` jako jedyne źródło wzoru SCOP.

## Szybki start

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Struktura projektu

```
tuya_v2/
├── app/
│   └── core/
│       ├── physics.py       — formuły fizyczne (COP, moc, przepływ ciepła)
│       ├── energy.py        — compute_energy() + compute_scop() (kanoniczny wzór SCOP)
│       ├── calibration.py   — kalibracja z licznika (hidden_power_w + sensor_factor)
│       ├── models.py        — modele danych (dataclasses / TypedDict)
│       └── config.py        — stałe konfiguracyjne, progi, parametry czujników
│           └── get_timezone_offset() — automatyczne wyliczanie strefy czasowej (DST)
├── tests/                   — testy jednostkowe i integracyjne
│   └── test_calibration.py  — testy modelu kalibracji
├── requirements.txt
├── pyproject.toml
└── README.md
```

## Kluczowe decyzje projektowe

- **Energia liczona z surowych danych** — nigdy z próbek po resamplingu
- **Jedna funkcja `compute_energy()`** — używana wszędzie (API, raporty, dashboard)
- **Jedna funkcja `compute_scop()`** — jedyne źródło wzoru SCOP (scope: total/co/cwu, kind: real/nominal); wszystkie strony i silnik jej używają, więc wyniki są spójne
- **Obliczenia w kawałkach (chunked)** — dla dużych zakresów (zima: 6M+ próbek)
- **Brak dodatkowych tabel w bazie** — wyniki obliczane na żądanie
- **Czysty Python** — rdzeń bez zależności od Streamlit ani frameworków webowych
- **Addytywny model kalibracji** (`hidden_power_w` + `sensor_factor`) zamiast pojedynczego mnożnika — działa poprawnie zarówno latem JAK I zimą
- **Sonda prądowa mierzy tylko kompresor** — `hidden_power_w` kompensuje pompę obiegową, elektronikę i inne stałe odbiorniki
- **Automatyczne przeliczanie strefy czasowej** — `get_timezone_offset()` z `zoneinfo` dla `Europe/Warsaw` (CEST=+2, CET=+1), bez ręcznej zmiany przy DST

## Testy

74 testów pokrywających:

- formuły fizyczne (COP, moc cieplna, przepływ)
- obliczenia energii (`compute_energy()`)
- wzór SCOP (`compute_scop()` — scope total/co/cwu, kind real/nominal, znak defrostu)
- obsługę cykli rozmrażania (defrost)
- filtrowanie trybów pracy
- rozbicie dzienne (daily breakdown)
- kalibrację czujników
- spójność SCOP (single vs chunked, suma daily == total)

## Model kalibracji

Sonda prądowa mierzy tylko pobór kompresora. Aby uzyskać rzeczywiste zużycie całej pompy ciepła (kompresor + pompa obiegowa + elektronika), stosujemy model addytywny:

```
E_el_real = E_el_sensor × sensor_factor + hidden_power_w × total_hours / 1000
```

Parametry:

- **`hidden_power_w`** — ~20W stała moc ukryta (pompa obiegowa, sterownik, zawory), kalibrowana z fizycznego licznika energii
- **`sensor_factor`** — ~1.0, koryguje błąd proporcjonalny sondy prądowej

### Dlaczego nie pojedynczy mnożnik?

Pojedynczy współczynnik korekcyjny (`E_real = E_sensor × factor`) nie działa poprawnie w obu sezonach:

| Sezon | Kompresor | Ukryta moc | Udział ukrytej mocy |
|-------|-----------|------------|---------------------|
| **Zima** | ~2000W | ~20W | ~0.1% — mnożnik ≈ 1.001 |
| **Lato** (CWU) | ~100W krótko | ~20W ciągle | ~15% — mnożnik ≈ 1.15 |

Jeden mnożnik skalibrowany zimą zaniża latem (i odwrotnie). Model addytywny rozdziela te dwa źródła błędu i działa poprawnie przez cały rok.

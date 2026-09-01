"""Podstrona: Baza Wiedzy — opisy i analizy dot. wydajności pompy ciepła."""
import streamlit as st

from app.ui.styles import inject_css, render_about

st.set_page_config(page_title="Baza Wiedzy", layout="wide", page_icon="📚")
inject_css()

with st.sidebar:
    render_about()

st.markdown('<h3 style="margin:0;padding:0.2rem 0;">📚 Baza Wiedzy</h3>', unsafe_allow_html=True)

st.info("""
**Analiza opłacalności pompy ciepła vs kocioł gazowy — Łódź**

Dla instalacji hybrydowej w Łodzi analiza liczby dni, w których praca pompy ciepła będzie tańsza od kotła gazowego, opiera się na granicznym progu opłacalności COP = 3,07 oraz historycznym rozkładzie temperatur w sezonie grzewczym.

Większość sezonu grzewczego w Łodzi (trwającego średnio ok. 200–210 dni) przypada na łagodne temperatury, co sprzyja pracy pompy ciepła.

---

**Rozkład dni grzewczych w Łodzi według temperatur**

Na podstawie wieloletnich danych meteorologicznych średni rozkład temperatur w sezonie grzewczym wygląda następująco:

- **Powyżej +3°C** (okresy przejściowe — jesień, wiosna): ok. 135–145 dni (ok. 65–70% sezonu)
- **Od 0°C do +3°C**: ok. 30–35 dni (ok. 15% sezonu)
- **Poniżej 0°C** (mrozy): ok. 30–40 dni (ok. 15–20% sezonu)

---

**Podział dni pracy: Pompa ciepła vs Kocioł gazowy**

Liczba dni, w których dany agregat jest tańszy, zależy bezpośrednio od temperatury zasilania instalacji c.o.:

🔵 **Instalacja podłogowa (zasilanie 30–35°C):**
- Pompa tańsza (COP > 3,07): przy temperaturze zewnętrznej powyżej **-3°C**
- Czas pracy pompy: ok. **185–195 dni** w roku (ok. 90% sezonu)
- Czas pracy kotła: ok. **15–20 dni** w roku (tylko podczas fal mrozów poniżej -3°C)

🟡 **Instalacja mieszana / średniotemperaturowa (zasilanie 40–45°C):**
- Pompa tańsza (COP > 3,07): przy temperaturze zewnętrznej powyżej **+2°C do +3°C**
- Czas pracy pompy: ok. **140–150 dni** w roku (ok. 70% sezonu)
- Czas pracy kotła: ok. **55–65 dni** w roku (głównie w najzimniejszych miesiącach)

🔴 **Instalacja grzejnikowa (zasilanie 50–55°C):**
- Pompa tańsza (COP > 3,07): przy temperaturze zewnętrznej powyżej **+5°C**
- Czas pracy pompy: ok. **110–120 dni** w roku (ok. 55% sezonu)
- Czas pracy kotła: ok. **85–95 dni** w roku

---

**Kluczowy wniosek ekologiczno-ekonomiczny**

Warto pamiętać o różnicy między liczbą dni a realnym zużyciem energii:

- W instalacji mieszanej kocioł pracujący przez około 30% dni w roku pokrywa aż **50–60% rocznego zapotrzebowania na ciepło**, ponieważ zapotrzebowanie budynku na moc rośnie liniowo wraz ze spadkiem temperatury.
- Pompa ciepła idealnie sprawdza się w okresach przejściowych, pracując przez większość dni w roku na niskim obciążeniu.
- Dla automatyki sterującej w Łodzi optymalny punkt przełączenia źródła (bivalencji) przy instalacji mieszanej warto ustawić w okolicach **+2°C**.

---

**⚠️ Wpływ oblodzenia na wydajność**

Przy temperaturach bliskich 0°C (zakres od -3°C do +3°C) i dużej wilgotności powietrza — co jest częste w Polsce środkowej — dochodzi do intensywnego oblodzenia parownika pompy ciepła. Wymusza to częste cykle odszraniania (defrost), podczas których:

- Pompa **odwraca obieg** — zamiast grzać dom, pobiera ciepło z instalacji CO żeby roztopić lód na parowniku
- Energia elektryczna jest zużywana, ale **ciepło nie trafia do budynku** (a nawet jest z niego zabierane)
- SCOP realny znacząco spada — straty defrostu mogą obniżyć SCOP o **0.3–0.8** w porównaniu z SCOP nominalnym

To szczególnie istotne w Łodzi, gdzie zakres 0°C do +3°C stanowi ok. 15% sezonu grzewczego (30–35 dni), ale ze względu na częste defrosty te dni mogą generować **nieproporcjonalnie wysokie koszty** eksploatacji.
""")

# --- Artykuł: Parametry pompy i histereza ---
st.markdown("---")
st.subheader("📡 Parametry pompy ciepła i filtr histerezy")

st.markdown("""
Pompa ciepła raportuje dane przez Tuya Pulsar. Każda zmiana parametru jest zapisywana do bazy,
ale filtr histerezy (DeadbandFilter) odcina drobne wahania — zapisuje tylko gdy zmiana
przekroczy próg. Progi są różne gdy pompa pracuje (active) vs stoi (idle).

**Wymuszony heartbeat:** co 300s każdy parametr jest zapisywany nawet bez zmiany (wyjątek: flagi binarne).
""")

with st.expander("🌡️ Temperatury hydrauliczne", expanded=False):
    st.markdown("""
| Parametr | Opis | Histereza active | Histereza idle |
|----------|------|:---:|:---:|
| `in_water_temp` | Powrót CO — woda wracająca z instalacji | 0.2°C | 0.5°C |
| `out_water_temp` | Zasilanie CO — woda wychodząca na dom | 0.2°C | 0.5°C |
| `tank_temp` | Temperatura wody w zasobniku CWU | 0.2°C | 0.5°C |
| `amb_temp` | Temperatura zewnętrzna | 0.5°C | 0.8°C |
| `disc_temp` | Temperatura tłoczenia sprężarki | 0.5°C | 1.5°C |
| `back_temp` | Temperatura ssania sprężarki | 0.5°C | 1.5°C |
| `tidr` | Temperatura pokojowa (czujnik wewnętrzny) | 0.5°C | 0.5°C |

⚠️ Wartości temperatur w bazie są **już przeliczone** (dzielone przez 10 przez collector).
""")

with st.expander("⚡ Parametry elektryczne i mechaniczne", expanded=False):
    st.markdown("""
| Parametr | Opis | Skala | Histereza active | Histereza idle |
|----------|------|:---:|:---:|:---:|
| `ac_vol` | Napięcie zasilania | V | 2.0 | 3.0 |
| `ac_curr` | Prąd pobierany | ×0.1 A (35 = 3.5A) | 2.0 | 5.0 |
| `comp_freq` | Częstotliwość sprężarki | Hz (max 120) | 2.0 | 1.0 |
| `flow_rate` | Przepływ wody | ×0.1 m³/h (25 = 2.5) | 2.0 | 1.0 |
| `m_eev` | Główny zawór rozprężny EEV | 0-480 kroków | 5.0 | 20.0 |
| `dc_fan1` | Wentylator DC zewnętrzny | 0-1000 RPM | 15.0 | 50.0 |

⚠️ **ac_curr mierzy TYLKO sprężarkę!** Pompa obiegowa, wentylator standby i elektronika
idą osobnym obwodem, niewidocznym dla czujnika. Dlatego licznik fizyczny pokazuje ~20 Wh/h więcej
niż telemetria. Kompensujemy to parametrem `hidden_power_w` w kalibracji.
""")

with st.expander("🔀 Flagi binarne i tryby pracy", expanded=False):
    st.markdown("""
**Flagi** (zapisywane jako `val_str` "True"/"False", nie `val_num`!):

| Parametr | Opis |
|----------|------|
| `valve` | Zawór 3-drożny: **True = CWU**, False = CO |
| `defrost` | Cykl odszraniania parownika |
| `pump_sta` | Status pompy obiegowej (pracuje ~2 min po wyłączeniu sprężarki) |
| `fault_flag` | Flaga awarii |
| `freeze` | Ochrona antyzamrożeniowa |
| `mute` | Tryb cichy (Silent) |
| `holiday_sw` | Tryb urlopowy (Holiday) |

Flagi **nie mają heartbeatu** — zapisywane tylko przy zmianie wartości.

**Tryby pracy** (`work_mode`):
`cool`, `heat`, `auto`, `hot_water`, `cool_hot_water`, `heat_hot_water`, `auto_dhw`

**Strefy grzewcze** (`zone_select`): 0=brak, 1=Z1, 2=Z2, 3=obie
- Z1: `heat_temp_set` (np. 41°C — grzejniki)
- Z2: `heat_temp_set_z2` (np. 28°C — podłogówka)

**Kody błędów** (`fault`): bitmapa 30 bitów — E01-E16 (bit 0-15), P01-P14 (bit 16-29). 0 = brak.
""")

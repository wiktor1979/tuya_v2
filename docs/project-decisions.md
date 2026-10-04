# !!! ZASADY WSPÓŁPRACY (obowiązują zawsze, także po kompaktowaniu kontekstu)

1. DEPLOY: każdy `fly deploy` wymaga JASNEJ, WYRAZNEJ zgody użytkownika ZA KAŻDYM RAZEM.
   Poprawki/redeploye po nieudanym deployu NIE są kontynuacją poprzedniej zgody. Bez "tak, deployuj" — nie deployować.
2. WYBÓR OPCJI: gdy przedstawiam warianty (A/B/...), ZATRZYMUJĘ SIĘ i czekam na decyzję użytkownika.
   NIE implementuję żadnej opcji z automatu, nawet jeśli którąś rekomenduję. Najpierw wybór użytkownika, potem implementacja.
3. SYNC BAZY: zmiany w bazie LOKALNEJ (np. `settings`/kalibracja przez set_setting) trzeba TAKŻE nanieść
   na bazę PRODUKCYJNĄ na Fly.io. Lokalna jest robocza i bywa nadpisywana pobraniem z Fly, więc zmiana
   tylko lokalnie zostanie utracona. Konfigurację nanosić punktowo (fly ssh -C set_setting), NIE wysyłać
   całej bazy w drugą stronę (nadpisałoby świeżą telemetrię produkcyjną).
   KOLEJNOŚĆ (obowiązkowa): NAJPIERW zmiana LOKALNIE, potem ZATRZYMAĆ SIĘ i CZEKAĆ na wyraźne
   potwierdzenie użytkownika. Dopiero po "tak" aplikować na produkcji. Nigdy lokalnie i produkcyjnie
   w jednym kroku bez pytania.
4. ZMIANY KODU: przed każdą zmianą w kodzie (nawet małych) pytać: "Czy chcesz żebym nanieśł zmianę X?"
   Czekać na wyraźne "tak" przed wykonaniem. Nigdy zmieniać bez zgody.
5. PROPOZYCJA PRZED IMPLEMENTACJĄ (obowiązuje ZAWSZE, nadrzędna nad domyślnym zachowaniem agenta):
   Dla KAŻDEGO zadania NAJPIERW przedstawiam PROPOZYCJĘ rozwiązania (co i jak zamierzam zrobić,
   które pliki, jaki efekt) i ZATRZYMUJĘ SIĘ. Przechodzę do implementacji DOPIERO po jawnym
   zatwierdzeniu przez użytkownika ("tak", "rób", "zatwierdzam" itp.). Dotyczy to zmian w kodzie,
   konfiguracji, bazie i wszelkich modyfikacji plików. Wyjątek: operacje wyłącznie odczytowe
   (czytanie plików, zapytania SELECT do bazy, analiza, diagnostyka) — te wykonuję od razu.
   Samo pytanie użytkownika (np. "sprawdź X", "dlaczego Y") NIE jest zgodą na modyfikację.

---

## Migracja na Oracle Cloud (OCI) — W TOKU (2026-10-03)

CEL: przenieść projekt z Fly.io (app `scop`) na darmową VM Always Free w OCI.
Collector + dashboard w JEDNYM kontenerze Docker (jak na Fly), baza SQLite na dysku,
sekrety w env, dostęp po gołym IP:8501 (opcja A — bez reverse proxy/HTTPS na start).

STAN KONTA / ZASOBY OCI (konto `wiktor79 (root)`, region eu-frankfurt-1):
- VCN: `tuya`, CIDR 10.0.0.0/16, DNS domain `tuya.oraclevcn.com`. Utworzony kreatorem
  „VCN with Internet Connectivity" (ma Internet Gateway + trasę 0.0.0.0/0 — nie dodawać ręcznie).
- Podsieć publiczna: `public subnet-tuya` (10.0.0.0/24).
- Security List (Ingress) otwarte: TCP 22 (SSH, 0.0.0.0/0), TCP 8501 (dashboard, 0.0.0.0/0)
  + domyślne ICMP. SSH na 0.0.0.0/0 bo user ma ZMIENNE publiczne IP (logowanie tylko kluczem).
- Instancja: `tuya-vm`, kształt `VM.Standard.A1.Flex` (ARM/aarch64, Always Free-eligible),
  1 OCPU / 6 GB RAM, Canonical Ubuntu 22.04 (jammy). Boot volume domyślny ~46.6 GB
  (BEZ osobnego Block Volume — baza SQLite pójdzie na boot volume).
- Public IP: `130.162.51.207`. User SSH: `ubuntu`.
- Klucz SSH: własny ed25519 — priv `C:\Users\qwikkmi\.ssh\oci_tuya`, pub `oci_tuya.pub`
  (wgrany przy tworzeniu VM). Logowanie: `ssh -i C:\Users\qwikkmi\.ssh\oci_tuya ubuntu@130.162.51.207`.
- KOSZTY: kalkulator OCI pokazuje ~7,69 zł/mies za boot volume, ale to LIST PRICE bez
  uwzględnienia Always Free (napis „does not reflect any tier unit pricing"). Realnie 0 zł
  w limitach Free (A1 do 4 OCPU/24 GB, dyski do 200 GB). Zalecane: ustawić Budget z alertem w Billing.
- LIMITY Always Free A1 (z konsoli OCI, 2026-10-03): tenancy dostaje za darmo 3 000 OCPU-godzin
  i 18 000 GB-godzin / mies. na Ampere A1 Flex (= 4 OCPU i 24 GB działające ciągle) + dwie
  instancje VM.Standard.E2.1.Micro. Nasza VM (1 OCPU / 6 GB, 24/7) zużywa ~730 OCPU-h i ~4380 GB-h
  / mies. = ~24% limitu A1 — z dużym zapasem, compute = 0 zł.

ZROBIONE:
- VM działa, SSH działa.
- Docker zainstalowany (docker-ce + docker-ce-cli + containerd + buildx + compose-plugin),
  user `ubuntu` w grupie `docker`, `docker run --rm hello-world` OK na arm64v8.
- Repo APT Dockera `/etc/apt/sources.list.d/docker.list`:
  `deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu jammy stable`.
  (Przy zapisie: użyć JEDNEJ linii `sudo bash -c 'echo "..." > /etc/apt/sources.list.d/docker.list'` —
  łamanie linii / brak sudo przy `tee` powodowało „Malformed entry" i „No such file or directory".)

NASTĘPNY KROK (tu kończymy sesję): PRZENIESIENIE KODU na VM. DO DECYZJI UŻYTKOWNIKA:
1. Dostarczenie kodu: `git clone` (repo zdalne — podać URL; prywatne = token/klucz deploy)
   czy `scp` z komputera (bez repo; wysłać tylko kod, BEZ `.env` i `data/*.db`).
2. Czy tworzyć w repo pliki pomocnicze: `docker-compose.yml` (restart unless-stopped,
   montaż bazy na wolumen → `DB_FILE`, wczytanie `.env`, port 8501) + `docs/deploy-oci.md`.

POTEM (plan do końca):
- build obrazu z istniejącego `Dockerfile` (bez zmian — multi-arch),
- utworzyć `.env` na serwerze (TUYA_ACCESS_ID/KEY/DEVICE_IDS, TELEGRAM_BOT_TOKEN/CHAT_ID),
- `docker run`/compose z `DB_FILE=/data/tuya_telemetry.db` i montażem wolumenu na bazę,
- przenieść bazę PRODUKCYJNĄ z Fly na VM (ciągłość telemetrii/SCOP — jednorazowy transfer pliku),
- weryfikacja: dashboard na `http://130.162.51.207:8501`, collector łapie Pulsar, raport Telegram,
- DOPIERO po potwierdzeniu działania — wyłączyć/suspendować app `scop` na Fly (nieodwracalne — zgoda usera).

UWAGI TECHNICZNE:
- ARM: `python:3.12-slim` + pandas/numpy mają koła arm64 → build przejdzie BEZ zmian w kodzie.
- Firewall Ubuntu/OCI: domyślne iptables mogą blokować 8501 mimo otwartej Security List.
  Jeśli dashboard nie wejdzie → dodać wyjątek (iptables + netfilter-persistent) albo `--network host`.
- Kod aplikacji (app/, main.py, Panel.py) NIE wymaga zmian — architektura przenośna
  (SQLite + zmienne środowiskowe + wolumen działają identycznie jak na Fly).
- Pulsar/Tuya: strumień ten sam (to samo konto Tuya) — collector łapie ramki bez zmian u Tuya.
- Strefa czasowa: `get_timezone_offset()` liczy DST z zoneinfo — na VM wystarczy standardowe UTC
  (fallback `SERVER_TIMEZONE_OFFSET` jak w fly.toml, ale funkcja i tak nadpisuje dynamicznie).

---

## Ustalenia i zmiany — 2026-10-01

### Doradca Krzywej Grzewczej — analiza krótko- i długoterminowa (2026-10-01)
- CEL (user): dobrać 2-punktową krzywą pogodową tak, by pompa grzała możliwie
  CIĄGLE z MINIMALNĄ wymaganą temperaturą wody (max COP, bez taktowania). Pompa
  NIE raportuje temperatury wody wyliczonej z krzywej — trzeba ją odtworzyć z nastaw.
- PUNKTY KRZYWEJ (decyzja user): **−15°C i +15°C** (nie +20 jak w v1). Krzywa to
  funkcja liniowa dwóch punktów: `T_woda(amb) = T_low + slope·(amb−(−15))`,
  `slope = (T_high − T_low)/(15 − (−15))`. Interpolacja i ekstrapolacja.
- SYGNAŁ ŻĄDANIA GRZANIA (kluczowe — brak bezpośredniego DP termostatu pokojowego):
  termostat pokojowy ON/OFF czytany POŚREDNIO z `work_mode`:
  * `hot_water` → termostat OFF (pompa robi tylko CWU),
  * `heat_hot_water` (lub `heat`) → termostat ON (żąda grzania CO).
  Przejście `hot_water → heat_hot_water` = termostat ZAŻĄDAŁ grzania (start epizodu),
  powrót do `hot_water` = koniec epizodu. `work_mode` jest serią ZDARZENIOWĄ (zapis
  tylko przy zmianie, ~700 rekordów/pompę) — epizody odtwarzane z punktów zmiany.
- FILTR STREFY (decyzja user): krzywa dobierana dla STREFY 1. Strefa 2 ma STAŁĄ
  temperaturę wody (nie z krzywej) → epizody samej strefy 2 POMIJANE. Liczone tylko
  `zone_select ∈ {1 (Z1), 3 (obie)}`; `zone_select == 2` (sama Z2) odrzucane.
- ALGORYTM (czysty core `app/core/heating_curve.py`, bez Streamlit):
  1. `detect_heating_episodes(events, amb_samples, zone_samples, curve_zones)` —
     z sekwencji `work_mode` wykrywa epizody ON; dla każdego: czas trwania, średnia
     `amb` w oknie, czas OFF do następnego epizodu. Filtr strefy: epizod liczony,
     gdy większość okna należy do `curve_zones` (domyślnie {Z1, obie}). `MAX_GAP_SEC`
     = 24h — dłuższa przerwa OFF traktowana jako luka (nie zaniża duty cycle do zera).
  2. `compute_duty_bins(episodes, bin_size=3°C)` — grupuje epizody wg średniej `amb`
     i liczy DUTY CYCLE = czas ON / (ON + OFF) per przedział temperatury.
  3. `recommend_curve_adjustment(bins, t_low, t_high, target_room, …)` — rekomendacja.
- LOGIKA REKOMENDACJI (cel: duty cycle > 85% = praca ciągła z minimalną wodą):
  * Niski duty w danym przedziale `amb` = woda ZA CIEPŁA (termostat szybko osiąga
    nastawę i odcina → taktowanie). Przelicznik: ~7 pkt% niedoboru duty ≈ 1°C nadmiaru
    wody (`_duty_to_delta`). Rekomendacja: OBNIŻ wodę.
  * Korekta rzutowana na końce krzywej wagą liniową wg pozycji `amb` na osi −15…+15:
    biny bliżej −15°C korygują T_low, bliżej +15°C — T_high.
  * Duty ≥ 85% w całym obserwowanym zakresie → krzywa OK (brak zmian).
- JAKOŚĆ DANYCH wg ROZPIĘTOŚCI temperatur zewnętrznych (decyzja user — do wyznaczenia
  NACHYLENIA potrzeba szerokiego zakresu `amb`, nie całego sezonu):
  * rozrzut < 8°C (`AMB_RANGE_MIN_SLOPE`) → „narrow": nie da się wyznaczyć nachylenia,
    rekomendacja tylko dla końca bliższego obserwowanym temperaturom + ⚠️ ostrzeżenie,
  * 8–15°C → „slope_rough": nachylenie orientacyjne,
  * ≥ 15°C (`AMB_RANGE_RELIABLE_SLOPE`) → „slope_reliable": pełna rekomendacja obu końców.
- ANALIZA KRÓTKO- i DŁUGOTERMINOWA (obie w zakładce):
  * długoterminowa = całe dane (`df_pivot_all`) — dobór obu końców krzywej,
  * krótkoterminowa = wybrany zakres (`df_pivot_range`) — bieżące warunki.
- WARIANT A (decyzja user): obliczenia NA ŻĄDANIE z telemetrii, BEZ nowych tabel
  wyników (zgodne z zasadą projektu). `work_mode` jest tani w przeliczaniu.
- UI (`app/ui/tab_heating_curve.py`): FORMULARZ (T_low przy −15°C, T_high przy +15°C,
  zadana temp. pokojowa) zapisywany przez `set_setting`. Nastawy POWIĄZANE Z POMPĄ —
  klucze z sufiksem `pump_id` (`curve_low_temp_<pump_id>`, `curve_high_temp_<pump_id>`,
  `curve_room_target_<pump_id>`), budowane przez `_curve_setting_keys(pump_id)`. Każda
  pompa ma własną krzywą fizyczną, więc nastawy NIE są współdzielone (pompa2 ma osobne).
  `render()` dostaje `pump_id` z `2_Analiza.py` (`_sel_id`); `key` widgetów też per pompa
  (Streamlit nie miesza stanu). Podgląd krzywej (wartości wody dla kilku temperatur),
  kolorowe komunikaty, tabela duty cycle per przedział. Jasne komunikaty gdy: brak
  danych / za mały zakres temperatur / dominacja strefy 2.
- DATA ZMIANY KRZYWEJ (domyka pętlę „zmień krzywą → oceń"): pole `st.date_input`
  (`curve_changed_at_<pump_id>`) auto-ustawiane na dziś przy zapisie nastaw (edytowalne).
  Po zmianie krzywej dane sprzed i po mają RÓŻNE krzywe — mieszanie zafałszowałoby duty
  cycle. Rozwiązanie: `detect_heating_episodes(since_ts=...)` pomija epizody rozpoczęte
  przed datą zmiany. OBIE analizy (długo- i krótkoterminowa) respektują `since_ts`.
  Pusta data = cała historia. Konwersja daty lokalnej → epoch przez `_date_to_epoch`
  (wzór odporny na Windows, korekta `SERVER_TIMEZONE_OFFSET`).
- BŁĄD NAPRAWIONY (`app/ui/analiza_helpers.py`): `zone_select` zapisany w bazie jako
  `val_str` „0".."3"; `load_analiza_pivot` konwertował przez `BOOL_MAP` (zna tylko 0/1),
  więc wartości 2/3 ginęły (NaN) → filtr strefy nie dostawał danych. Dodano dedykowaną
  konwersję `zone_select` z `val_str` na liczbę (0/1/2/3) PRZED `BOOL_MAP`.
- STAN DANYCH (potwierdzone w bazie, pompa Wiktor): obecnie grzeje głównie STREFA 2
  (47 epizodów Z2 vs 6 epizodów Z1/obu). Strefa 2 ma stałą temp. wody → analiza krzywej
  (Z1) będzie wiarygodna dopiero gdy strefa 1 zacznie realnie grzać (sezon grzewczy).
  Zakładka wykrywa dominację Z2 (osobne wywołanie `detect_heating_episodes` z
  `curve_zones={Z2}`) i wyświetla 🔁 ostrzeżenie zamiast mylących rekomendacji.
- TESTY: `tests/test_heating_curve.py` (34): model krzywej (interpolacja/ekstrapolacja,
  slope), epizody (ON/OFF, heat, krótkie, otwarte na końcu, amb_avg, off_after,
  ciągły ON = jeden epizod), filtr strefy (Z1/obie liczone, sama Z2 pomijana,
  `curve_zones={Z2}` liczy tylko Z2, brak danych strefy = licz wszystko), duty cycle,
  rekomendacja (norma, za ciepło oba końce, wąski zakres jeden koniec, progi 8/15,
  brak formularza), klucze nastaw PER POMPA (`_curve_setting_keys` — pump_id w kluczu,
  rozłączność pompa1/pompa2, fallback 'default', klucz daty zmiany), filtr daty
  (`since_ts` — epizody przed datą zmiany pomijane, None=całość) i konwersja daty→epoch
  (`_date_to_epoch`/`_parse_iso_date`). Pakiet 198 PASS (było 158).

---

## Ustalenia i zmiany — 2026-09-30

### Zewnętrzny termometr temp. powietrza w pomieszczeniu — powiązany z pompą1 (2026-09-30)
- CEL (user): dodać obsługę zewnętrznego termometru Tuya (device_id
  `bf9134db09e1ea78cdskae`), powiązanego z pompa1 (Wiktor). To samo konto Tuya →
  dane płyną istniejącym strumieniem Pulsar (zero nowego pollingu).
- ROZPOZNANIE (wariant A — potwierdzenie danymi, nie zgadywanie): najpierw dodano
  device_id do whitelisty collectora, po spłynięciu ramek odczytano realne kody z bazy
  PRODUKCYJNEJ. Termometr wysyła 5 kodów (~co 15 min, czujnik bateryjny):
  - `temp_current` i `va_temperature` — DUPLIKAT tej samej temperatury (ten sam ts,
    ta sama wartość), skala ×0.1 (260 surowo = 26.0°C; zakres próbek 143–260 = 14.3–26.0°C).
  - `humidity_value` i `va_humidity` — duplikat wilgotności (×1, np. 75%).
  - `battery_percentage` — poziom baterii (×1, 100%).
- DECYZJE (user): (1) skala zgodna z pompą — kody temperatury w TEMP_CODES (dzielone
  ×0.1 przy ZAPISIE); (2) temperatura widoczna na wykresie „Przebieg parametrów" na
  Panelu; (3) kanoniczny kod `va_temperature`, duplikat `temp_current` UKRYTY na wykresie;
  (4) surowe próbki sprzed wdrożenia poprawić ÷10. Wilgotność/bateria zbierane, ale
  NIE pokazywane w UI (poza zakresem — user chciał tylko temperaturę).
- MODEL DANYCH (`config.py`): pozycja pompy w PUMPS rozszerzona o pole `thermo_id`
  (analogicznie do `meter_id`). pompa1 → `bf9134db09e1ea78cdskae`, pompa2 → None.
  Nowy zbiór `THERMO_DEV_IDS` (frozenset, bez None) do whitelisty collectora.
  `DEVICE_NAMES` → „Termometr Wiktor". Kody `va_temperature`/`temp_current` dodane do
  TEMP_CODES. Histereza `HISTERESIS_CONFIG` dla obu: active 0.2 / idle 0.3°C.
  PARAM_INFO: `va_temperature` → „Temp. pokojowa (termometr)"; `temp_current` BEZ
  etykiety (duplikat, dzięki czemu nie trafia do multiselektu wykresu).
- COLLECTOR (`tuya_client.py`): `THERMO_DEV_IDS` dodane do whitelisty w
  `handle_parsed_payload` (obok HEAT_PUMP_DEV_IDS/ENERGY_METER_DEV_IDS). Termometr
  idzie ścieżką `else` (jak pompa) przez DeadbandFilter — nie jest licznikiem.
- WATCHDOG (`main.py`): `THERMO_DEV_IDS` wykluczone z alertów utraty komunikacji
  (czujnik bateryjny raportuje rzadko ~15 min → cisza = norma, jak licznik energii).
- UI (`Panel.py`): `_load_chart_data` dostała param `thermo_id` — dla pompy z termometrem
  dociąga `va_temperature` (osobne urządzenie, więc osobne zapytanie po thermo_id) i
  scala z danymi pompy. Tylko kanoniczny kod (temp_current pomijany). Pompa2 (bez
  termometru) — parametr się nie pojawia. `sel_thermo_id` z selected_pump.get("thermo_id").
- KOREKTA PRÓBEK (produkcja): 14 surowych próbek va_temperature/temp_current sprzed
  deployu podzielono ÷10 (skrypt idempotentny — dzieli tylko val_num > 60, bo realna
  temp. pokojowa nigdy > 60°C; surowe to 143–260). Zakres po korekcie 14.3–26.0°C.
- TESTY: `tests/test_config.py` — thermo_id w pompach (pompa1 ma, pompa2 None),
  THERMO_DEV_IDS bez None (dokładnie 1 element), nazwa termometru, kody w TEMP_CODES,
  etykieta va_temperature + brak etykiety temp_current. 158 PASS (było 152).
- WDROŻENIE: zmiana w collectorze + config + dashboard → `fly deploy` (zgoda user).
  Skala TEMP_CODES działa dla NOWYCH zapisów (po deployu); historyczne poprawione skryptem.

### Panel — konfigurowalny wykres „Przebieg parametrów" z ręcznym zapisem (2026-09-30)
- CEL (user): wykres „Przebieg parametrów" był predefiniowany — umożliwić konfigurację
  wybranych parametrów, zapamiętaną i dostępną w tej samej konfiguracji na urządzeniu.
- DECYZJE (user, kolejno doprecyzowane): trwałość PER URZĄDZENIE/przeglądarka
  (localStorage, jak wybór pompy — NIE globalnie w bazie); zapis RĘCZNY przyciskiem
  (nie automatyczny przy każdej zmianie — przypadkowa zmiana nie nadpisuje układu);
  klucz WSPÓLNY dla obu pomp (jeden układ).
- ROZWIĄZANIE (wyłącznie UI, bez nowych zależności — `streamlit-local-storage` już jest):
  - `app/ui/helpers.py`: nowe `get_chart_params(default)` i `persist_chart_params(codes)`,
    klucz localStorage `tuya_chart_params_panel`, wartość jako lista kodów w JSON.
    Reużyty istniejący `_get_local_storage()`. `get_chart_params` ma pełny fallback na
    default: brak zapisu, zepsuty JSON, nie-lista, pusta lista, LS niedostępny.
  - `Panel.py`: domyślny wybór multiselektu z localStorage (przefiltrowany do kodów
    realnie obecnych w danych), przycisk „💾 Zapisz układ wykresu" → persist + st.toast.
    Bez kliknięcia zmiana działa tylko w bieżącym widoku.
- TESTY: `tests/test_helpers_pump.py` — klasa TestChartParams (6: fallback pusty,
  roundtrip zapis→odczyt, zepsuty JSON, nie-lista, pusta lista, LS niedostępny).

### Panel — przycisk „Bilans" w nagłówku (obrys, wyrównany) (2026-09-30)
- OBJAW (user): link do „Bilans" na górze Panelu był zwykłym `st.page_link` (nieładny
  napis, niewyrównany, CSS `.st-key-pump_header button` go nie obejmował — to `<a>`, nie
  `<button>`).
- ROZWIĄZANIE (`Panel.py`, tylko UI): `st.page_link` → `st.button` + `st.switch_page`
  (prawdziwy przycisk w tym samym kontenerze `pump_header`). CSS rozdzielony: reguła
  wspólna (wysokość/padding/wyrównanie) + osobno przycisk odświeżania (kolorowe tło
  stanu) i przycisk „📊 Bilans" (BEZ wypełnienia, ramka 2px w kolorze akcentu stanu +
  hover). `accent` dobierany do stanu (heating/running/off). Zawijanie na wąskim ekranie
  już obsłużone regułą `.st-key-pump_header nowrap` w styles.py.

### PARAM_INFO — poprawki opisów wg specyfikacji DP (2026-09-30)
- ŹRÓDŁO: `beko DP codes.py` (model GRUNDIG/Beko 000004wtcv) + weryfikacja odczytem bazy
  (realne kody obu pomp).
- POPRAWKA (potwierdzona danymi): `idr_temp_set` (DP 164) to „Indoor Room Temperature
  Setpoint" = NASTAWA POKOJOWA, nie „z krzywej". W bazie `idr_temp_set=25.0`, a
  `auto_heat_temp_set_curve` w ogóle nie występuje. Etykieta „Nastawa z krzywej" →
  „Nastawa pokojowa" (desc: „Zadana temperatura powietrza w pomieszczeniu").
- DODANE ETYKIETY (kody realnie wysyłane przez pompę, wcześniej surowe na wykresie):
  `a_eev`, `dc_fan2`, `ac_fan`, `auto_run_tar_mode`, nastawy chłodzenia/auto
  (`cool_temp_set`, `cool_temp_set_z2`, `auto_heat_temp_set_z1/z2`, `auto_cool_temp_set_z2`),
  flagi (`pump_sta`, `protect_flag`, `freeze`, `fault_flag`, `switch`, `mute`, `holiday_sw`).
  Pominięto czysto konfiguracyjne limity (`zone1_*`/`zone2_*`/`hw_*`/`indoor_temp_*`/
  `twc_*`/`mode_valid`/`no_twc_*`) — bez wartości diagnostycznej na wykresie przebiegu.
- POTWIERDZONO: `valve` (DP 117) = zawór 4-drożny (rewers grzanie/chłodzenie), NIE CO/CWU
  → decyzja o klasyfikacji CO/CWU po `work_mode` (nie po zaworze) jest słuszna.
- USUNIĘTO nieaktualny komentarz TODO przy device_id pompy2 (id jest realne, dane płyną).

### Baza produkcyjna — usunięto wpisy tidr (2026-09-30)
- POWÓD (user): `tidr` (temperatura pokojowa z czujnika wewnętrznego) nieużywana; część
  danych historycznych była niedzielona (max 2193 przy poprawnych ~21.3 — niespójna skala).
- WYKONANIE (produkcja Fly, decyzja user: bez backupu): skrypt Pythona przez sftp
  (sqlite3 CLI niedostępny w kontenerze) usunął WSZYSTKIE wpisy `tidr` z
  `/data/tuya_telemetry.db`. Usunięto 28352, pozostało 0.
- ZAKRES: tylko baza PRODUKCYJNA (lokalnej nie ruszano). `tidr` MA być nadal zbierany
  (bez zmian w collectorze) — usunięto tylko istniejące wpisy. `tidr` NIE jest w
  ENERGY_CODES → usunięcie nie wpływa na energię/SCOP.

---

## Ustalenia i zmiany — 2026-09-27

### HDD liczony z danych pogodowych, nie z czujnika amb_temp (2026-09-27)
- OBJAW: HDD na Bilansie znacząco różny dla pompa1 (Wiktor, od południa) i pompa2
  (Karol, od północy), mimo lokalizacji 500 m od siebie. HDD zależy tylko od temp.
  zewnętrznej → powinien być ~identyczny.
- DIAGNOZA (odczyt bazy, ostatnie 2 dni — wcześniejsze dane niewiarygodne):
  czujnik amb_temp jednostki P1 pokazuje STALE ~1.3–1.5°C więcej niż P2, także NOCĄ
  (noc 22-07: P1 12.07 vs P2 10.80, dP=+1.27; dzień 10-17: P1 18.19 vs P2 16.67, dP=+1.52).
  Skoro różnica utrzymuje się w nocy (bez słońca), to NIE efekt nasłonecznienia, lecz
  STAŁY OFFSET czujnika (kalibracja/mikrolokalizacja montażu). Słońce dokłada tylko ~0.2°C.
  KRYTYCZNE dla HDD: temperatury krążą wokół progu bazowego 15°C — P1 avg ~15.5 (nad progiem
  → HDD≈0), P2 avg ~14.1 (pod progiem → HDD≈0.8–1.0). Mały offset = skrajnie różny HDD.
- DECYZJA (user): liczyć HDD z DANYCH POGODOWYCH (Open-Meteo, weather_data.temperature) —
  wspólne źródło dla obu pomp → HDD identyczny i porównywalny, niezależny od czujnika.
  Parametry: A) baza HDD 15°C (bez zmian), B) zwykła średnia dobowa (dane ~co 1 h, równomierne),
  C) fallback na amb_temp czujnika gdy dla dnia brak danych pogodowych.
- IMPLEMENTACJA:
  - `database.get_weather_daily_avg(ts_from, ts_to, offset)`: średnia dobowa temperature
    z weather_data, grupowana po dniu LOKALNYM (epoch+offset), zwraca {date -> °C}.
  - `energy.compute_energy(..., weather_daily: Optional[dict])`: nowy param wstrzykiwany
    (rdzeń NIE importuje bazy — zasada „core bez I/O"). `_compute_from_pivot` i
    `_compute_chunked` przekazują dalej. HDD dnia przez `_hdd_for_day`: pogoda gdy dzień
    w weather_daily, inaczej fallback na średnią amb_temp dnia. weather_daily=None →
    zachowanie jak dotąd (kompatybilność). amb_temp czujnika zostaje jako amb_temp_avg.
  - Dostawcy pogody: `helpers.weather_daily_for_range()` (→ cached_energy → Bilans i in.),
    `Panel.py` (wywołanie bezpośrednie compute_energy we fragmencie live),
    `notifier.build_daily_report()` (raport Telegram — spójność z dashboardem).
    Konwersja dat→epoch wg konwencji projektu (bez datetime.timestamp(); UTC = lokalny − offset).
- TESTY: `tests/test_hdd_weather.py` (6: HDD z pogody vs amb, fallback bez pogody, fallback
  dnia spoza pogody; get_weather_daily_avg mapowanie/pusty/NULL). 146 PASS (było 140 + 6).
- WDROŻENIE: zmiana w silniku + dashboard + notifier → produkcja po redeployu (zgoda user).

### Panel — pompa BEZ obsługi CWU (druga pompa: tylko CO) (2026-09-27)
- KONFIGURACJA: instalacja ma 2 pompy o RÓŻNEJ konfiguracji — pompa1 (Wiktor) grzeje
  CO i CWU, pompa2 (Karol) grzeje TYLKO CO (brak zasobnika/czujnika CWU).
- ROZRÓŻNIENIE (odczytowa analiza bazy): sygnałem „brak CWU" jest UJEMNA `tank_temp`
  (czujnik zasobnika rozwarty/niepodłączony → raportuje stałe -30°C, surowo -300).
  P2: tank_temp = -30.0 dla 30993/30993 odczytów; P1: 19.7–50.5, 0 ujemnych/51912.
  UWAGA: to NIE `hot_water_temp_set` jest ujemne (u OBU pomp = 45) — użytkownik
  wskazał „temp. zadana CWU", ale dane pokazały że rozróżnia dopiero tank_temp.
  Próg: tank_temp < 0 → brak CWU (decyzja user: próg 0).
- ROZWIĄZANIE (wyłącznie UI, rdzeń/silnik nietknięty, bez nowych zależności):
  - NOWY helper `pump_supports_cwu(status)` w `app/ui/helpers.py`: False gdy tank_temp<0,
    True gdy >=0 lub brak danych (bezpieczny default — nie ukrywa istniejącej funkcji).
  - `render_scop_box()` (`app/ui/styles.py`): nowy param `show_cwu` (domyślnie True).
    show_cwu=False → USUWA cały dolny wiersz rozbicia CO/CWU i akcenty CWU; zostaje
    JEDNA wartość SCOP (Total). Domyślnie True → bez zmian dla pompy z CWU.
  - `Panel.py`: wyznacza `has_cwu = pump_supports_cwu(status)`, przekazuje show_cwu do
    kafelka SCOP i renderuje pasek temperatury CWU TYLKO gdy has_cwu.
- FIX PRZY OKAZJI (surowy HTML na kafelku SCOP): render_scop_box budował HTML f-stringiem
  z WCIĘCIAMI (8 spacji). Gdy active_mode=None (badge=""), powstawała pusta linia + wcięte
  linie <div> → Markdown traktował to jako BLOK KODU i wyświetlał surowy HTML zamiast go
  renderować. NAPRAWA: oba st.markdown w render_scop_box przepisane na HTML BEZ wiodących
  wcięć (linie sklejane od kolumny 0). Zasada: HTML w st.markdown(unsafe_allow_html=True)
  nie może mieć wcięć ≥4 spacje po pustej linii.
- TESTY: tests/test_live_heating_mode.py — TestPumpSupportsCwu (4: ujemny→False,
  dodatni/zero→True, brak danych→True). 140 PASS (było 136 + 4).
- WDROŻENIE: zmiana w dashboardzie → produkcja po redeployu (zgoda user).

### Panel — wizualizacja „co pompa grzeje TERAZ" (CO/CWU) na kafelku SCOP (2026-09-27)
- CEL (użytkownik): na Panelu widać, co jest grzane gdy pompa pracuje (CO vs CWU) —
  akcent na kafelku SCOP „dzisiaj" (kolor/pogrubienie CO lub CWU).
- ROZWIĄZANIE (wyłącznie warstwa UI, rdzeń/silnik nietknięty, bez nowych zależności):
  - NOWY helper `get_live_heating_mode(status)` w `app/ui/helpers.py`: zwraca
    'co'/'cwu'/None. None gdy sprężarka stoi (comp_freq ≤ próg) — obieg wody/postój/
    defrost/awaria. Klasyfikacja CO/CWU przez ISTNIEJĄCE `_live_is_cwu()` (to samo
    źródło prawdy co silnik `_classify_cwu_mask` i `get_pump_status`) — spójność.
  - `render_scop_box()` (`app/ui/styles.py`): nowy param `active_mode` ('co'/'cwu'/None).
    Badge „🔥 Teraz grzeje: CO" / „🚿 Teraz grzeje: CWU" w kolorze trybu; aktywna sekcja
    (CO albo CWU) pogrubiona + pełny kolor + ramka/tło, nieaktywna przygaszona (opacity).
    active_mode=None → wygląd jak dotychczas (neutralny). Dodano import `Optional`.
  - `Panel.py`: wyznacza `active_mode = get_live_heating_mode(status)`, przekazuje do
    render_scop_box; dodatkowo podświetla NAGŁÓWEK aktywnego paska temperatur
    („🔥 CO · grzeje" / „🚿 CWU · grzeje", pogrubione, w kolorze trybu) — label pasków
    to HTML (unsafe_allow_html), więc wystarczyło wzbogacić tekst labela.
- SEMANTYKA: tryb pokazywany TYLKO gdy sprężarka realnie grzeje. Faza obiegu wody
  (pompa wody ON, sprężarka OFF) → brak badge (nic nie jest aktywnie grzane) — spójne
  z rozróżnieniem flow_rate="agregat pracuje" vs comp_freq="sprężarka grzeje".
- TESTY: nowy `tests/test_live_heating_mode.py` (6: sprężarka off→None, heat→co,
  hot_water→cwu, tryb łączony zimny/ciepły zasobnik, nieznany work_mode→co). 136 PASS
  (było 130 + 6). Kompilacja Panel.py/styles.py/helpers.py OK.
- WDROŻENIE: zmiana w dashboardzie (nie collector) → produkcja po redeployu (zgoda user).

### Raport Telegram — per pompa, bez licznika (2026-09-27)
- OBJAW: użytkownik dostawał 3× ten sam raport dzienny (identyczny SCOP/energia),
  w tym raport dla licznika energii (bez sensu).
- PRZYCZYNA A (identyczny raport): build_daily_report(device_id) w notifier.py używał
  device_id tylko do nazwy i fault_history, a compute_energy() wołał BEZ device_id
  (liczył zawsze pompę domyślną) i get_remote_meter_energy() BEZ meter_id (licznik pompy 1).
- PRZYCZYNA B (raport dla licznika): daily_report_loop() w main.py iterował po
  _last_data_received (wszystkie urządzenia, w tym ENERGY_METER_DEV_ID).
- NAPRAWA:
  - notifier.build_daily_report(): wyznacza meter_id po device_id z list_pumps(),
    przekazuje device_id do compute_energy() i meter_id do get_remote_meter_energy().
  - main.daily_report_loop(): iteruje po list_pumps() (tylko pompy), nie po
    _last_data_received. Pompa bez danych za wczoraj → build_daily_report zwraca None
    (nie wysyła). Usunięto osobne send_daily_report(HEAT_PUMP_DEV_ID) + pętlę po urządzeniach.
- TESTY: tests/test_notifier.py — TestPerPumpReport (device_id → compute_energy,
  meter_id → get_remote_meter_energy, None dla pompy bez licznika). 2 nowe testy.
- WDROŻENIE: zmiana w collectorze (main.py) → działa na produkcji po redeployu (zgoda user).

### DeadbandFilter — stan per (device_id, code), nie per code (2026-09-27)
- OBJAW: w logach temperatury (back_temp, in_water_temp, out_water_temp, tank_temp,
  disc_temp) zapisywane co ~4 s dla OBU pomp mimo postoju sprężarki. ~5200 rek/h per pompa,
  temperatury po 885/h każda (comp_freq tylko 8/h).
- PRZYCZYNA: DeadbandFilter.should_save() kluczował stan (last_saved_val/last_saved_time)
  po samym `code`. Jeden klient Pulsar = jeden filtr obsługuje obie pompy o TYCH SAMYCH
  kodach, ale różnych wartościach (pompa1 out_water ~29.7, pompa2 ~21.8). Przeplot ramek
  → wzajemne nadpisywanie last_saved_val → każda ramka przechodziła próg. Regresja przy
  dodaniu drugiej pompy (wcześniej 1 urządzenie, klucz=code wystarczał).
- DIAGNOZA (odczyt bazy, przeplot obu pomp wg czasu, out_water_temp, 1 h):
  wspólny filtr (klucz=code) przepuszcza 1536/1770; filtr per device 8/1770.
- NAPRAWA (tuya_client.py):
  - should_save(dev_id, code, new_val, compressor_status) — stan po kluczu (dev_id, code).
  - last_add_ele_time: pojedyncza wartość → dict[dev_id -> float] (dedup add_ele per licznik;
    zamyka wcześniejszą notę „przy drugim liczniku rozbić dedup per device_id").
  - 4 wywołania should_save w handle_parsed_payload dostały dev_id.
- EFEKT: temperatury filtrowane niezależnie per pompa. Spodziewany spadek zapisów
  z ~5200/h do ~50–100/h per pompa. Działa po RESTARCIE collectora (stan w pamięci);
  dane historyczne w bazie zostają.
- TESTY: tests/test_tuya_client.py — TestDeadbandPerDevice (przeplot 2 urządzeń → 1 zapis
  każde; niezależność stanu), TestAddEleDedupPerDevice. Razem 130 PASS (było 127 + 3).
- WDROŻENIE: zmiana w collectorze → produkcja po redeployu (zgoda user).

---

## Ustalenia i zmiany — 2026-09-09

### Wybór pompy trwały między sesjami przeglądarki — localStorage (2026-09-09)
- OBJAW: po dodaniu 2 pomp wybór pompy wracał na "Pompa 1" przy przełączaniu stron. Dodatkowo
  wymóg użytkownika: wybór ma przetrwać ZAMKNIĘCIE i ponowne otwarcie przeglądarki, przy czym
  DWIE różne osoby monitorują tę samą instalację na RÓŻNYCH sprzętach JEDNOCZEŚNIE.
- PRZYCZYNA: mechanizm oparty wyłącznie na `st.query_params` (?pump=). Natywna nawigacja
  Streamlita między stronami (menu sidebara / page_link) gubi query param z URL → nowa strona
  czytała pusto i wracała do DEFAULT_PUMP_ID. URL sam w sobie i tak nie przetrwa zamknięcia
  przeglądarki.
- DECYZJA (wybór użytkownika: "zaimplementuj lepsze"): trwałość PER PRZEGLĄDARKA/URZĄDZENIE.
  Wykluczono zapis globalny do bazy (tabela settings) — dwie osoby przełączałyby sobie nawzajem
  pompę. Wybrano localStorage (biblioteka `streamlit-local-storage==0.0.25`) zamiast cookie:
  localStorage jest czysto kliencki (nie leci na serwer przy każdym żądaniu), a serwer tej
  wartości nie potrzebuje. To ODWRÓCENIE wcześniejszej decyzji z tej samej sekcji ("query_params
  zamiast cookie/biblioteki") — bo zmienił się wymóg (przetrwanie zamknięcia przeglądarki + wiele
  osób, nie tylko F5).
- MECHANIZM (3 warstwy, kolejność ODCZYTU w `get_selected_pump()`): 1) `st.query_params` (F5,
  współdzielenie linku); 2) `st.session_state` klucz `_selected_pump_id` (nawigacja między
  stronami w sesji — osobny klucz, NIE klucz widgetu `pump_select`, bo ten bywa resetowany per
  strona); 3) localStorage klucz `tuya_selected_pump` (przetrwanie zamknięcia przeglądarki, per
  urządzenie); 4) fallback DEFAULT_PUMP_ID. Po ustaleniu wyboru synchronizacja WSTECZ do
  session_state + URL (żeby kolejne strony miały skąd czytać). Do localStorage zapis TYLKO przy
  realnym wyborze użytkownika (`persist_pump_choice`), NIE w get_selected_pump — inaczej można by
  nadpisać wartość, zanim komponent LS zdąży ją wczytać (odczyt LS jest asynchroniczny z JS).
- HELPERS (`app/ui/helpers.py`): nowe `persist_pump_choice(pid)` (zapis do 3 warstw),
  `render_pump_selector()` (wspólny selectbox w sidebarze — USUWA duplikację identycznego bloku
  z 5 stron i centralizuje logikę trwałości), `_get_local_storage()` (leniwy singleton w
  session_state; import `streamlit_local_storage` LOKALNY w funkcji — rdzeń/testy nie wymagają
  pakietu; zwraca None gdy komponent niedostępny → graceful fallback), `_valid_pump_id()`
  (walidacja że id istnieje w PUMPS, inaczej fallback).
- STRONY: Panel, 1_Bilans, 2_Analiza, 3_Porownanie, 4_Licznik — powtórzony blok selectboxa
  zastąpiony jednym wywołaniem `render_pump_selector()` (zwraca dict wybranej pompy). Importy
  `get_pump`/`list_pumps`/`get_selected_pump` w stronach pozostały (nieużywane, nieszkodliwe —
  czystka poza zakresem tej naprawy).
- ZALEŻNOŚĆ: `streamlit-local-storage==0.0.25` w `requirements.txt` (warstwa UI; rdzeń nadal
  czysty pandas+numpy). API: `getItem(key)`, `setItem(itemKey, itemValue, key=...)`.
- UWAGA DEPLOY: nowa zależność UI → działa na produkcji (Fly.io) DOPIERO po redeployu
  (wymaga wyraźnej zgody użytkownika — nie deployowano).
- TESTY: nowy `tests/test_helpers_pump.py` (8 testów: priorytety query_params > session_state >
  localStorage > default; walidacja błędnego id; synchronizacja wstecz; zapis do 3 warstw;
  symulacja zamknięcia przeglądarki = URL/session puste, localStorage trwa). st i localStorage
  podmieniane atrapami (FakeSt/FakeLS) — testują czystą logikę priorytetów bez runtime Streamlita.
  113 PASS (było 105 + 8). Kompilacja 6 zmienionych plików OK.

### Obsługa wielu pomp ciepła (2 pompy) — wybór w UI + licznik per pompa (2026-09-09)
- CEL: monitorować niezależnie 2 pompy ciepła (to samo konto Tuya, jeden strumień Pulsar).
  Wybór pompy w Panelu, zapamiętany tak by przetrwał odświeżenie strony. Licznik energii
  jest tylko dla jednej pompy → powiązany z pompą; druga pompa bez licznika.
- MODEL DANYCH (`config.py`): pojedyncze stałe zastąpione listą `PUMPS` (dict:
  id/name/device_id/meter_id). Helpery `get_pump(id)` (fallback: pierwsza) i `list_pumps()`.
  Zbiory `HEAT_PUMP_DEV_IDS` i `ENERGY_METER_DEV_IDS` (frozenset, bez None) do whitelist collectora.
  ALIASY WSTECZNE: `HEAT_PUMP_DEV_ID`/`ENERGY_METER_DEV_ID` = pierwsza pompa — utrzymują
  kompatybilność (notifier, power_analysis, testy, domyślne argumenty funkcji).
  pompa2: device_id = placeholder `<DEVICE_ID_2>` (DO PODMIANY), meter_id=None (bez licznika).
- WYBÓR POMPY + TRWAŁOŚĆ: `get_selected_pump()` (helpers.py) czyta `st.query_params["pump"]`.
  Selectbox "Pompa:" w sidebarze KAŻDEJ strony zapisuje wybór do URL (?pump=...), więc przeżywa
  odświeżenie (F5) i przełączanie stron — BEZ nowej zależności (query_params, nie cookie).
  Wybrano query_params zamiast biblioteki cookie: użytkownikowi chodziło o przetrwanie odświeżenia,
  nie o trwałość między sesjami przeglądarki. Selectbox pokazuje się tylko gdy len(PUMPS) > 1.
- PROPAGACJA device_id: wszystkie strony (Panel, 1_Bilans, 2_Analiza, 3_Porownanie, 4_Licznik)
  przekazują wybraną pompę do compute_energy/cached_energy, load_latest_status, load_analiza_pivot,
  _load_chart_data, load_power_comparison. cached_energy dostała parametr device_id.
- LICZNIK PER POMPA: cached_meter_energy, cached_meter_energy_daily, database.get_remote_meter_energy,
  load_power_comparison przyjmują meter_id. meter_id=None (pompa bez licznika) → 0.0/{}/None BEZ
  zapytania do bazy. UI: metryka i tabela pokazują "brak", strona 4_Licznik komunikat "Pompa nie ma
  licznika" + tylko model mocy z telemetrii. SCOP z sondy prądowej działa normalnie (niezależny od licznika).
- COLLECTOR (`tuya_client.py`): whitelist przepuszcza WSZYSTKIE skonfigurowane pompy i liczniki
  (HEAT_PUMP_DEV_IDS ∪ ENERGY_METER_DEV_IDS) niezależnie od TUYA_DEVICE_IDS — druga pompa zbierana
  automatycznie. Obsługa licznika: `dev_id in ENERGY_METER_DEV_IDS` (było == pojedynczego).
  UWAGA: dedup add_ele (last_add_ele_time) jest współdzielony per klient — poprawny dla JEDNEGO
  licznika (obecny stan). Przy DRUGIM liczniku trzeba rozbić dedup per device_id (dict) — dodano notę w kodzie.
- main.py: `_heat_pump_active` pozostaje stanem zbiorczym (poll REST i tak wyłączony — Trial). Do
  dopracowania per-pompa gdy API wróci.
- TESTY: nowy `tests/test_config.py` (10 testów: list_pumps kopia/klucze, get_pump fallback/None/pompa2
  bez licznika, zbiory ID bez None, aliasy wsteczne, device_names). `test_database.py`: +1 test
  (meter_id=None → None bez zapytania do bazy). Razem 105 PASS (było 92 + 13). Składnia wszystkich
  9 zmienionych plików OK, import backendu OK.
- DO ZROBIENIA (użytkownik): podmienić `PUMPS[1]["device_id"]` w config.py na realne device_id
  drugiej pompy (jedno miejsce). Do tego czasu "Pompa 2" działa w UI, ale nie pokazuje danych.

### Wątek REST-owego pollingu licznika WYŁĄCZONY — Tuya Cloud API Trial wyczerpany (2026-09-09)
- OBJAW: brak w logach jakiejkolwiek informacji o pollingu licznika. Przyczyna dwojaka:
  (1) `energy_meter_poll_loop()` nie logował ani wysłania requestu, ani odpowiedzi — tylko błędy;
  (2) sam poll i tak porzucał wynik `openapi.get(status)` (używany jedynie do wykrycia kodu 1010/1011).
- DIAGNOSTYKA: dodano tymczasowo logi `[POLL] -> GET ...` (wysłanie, z interwałem i stanem pompy)
  oraz `[POLL] <- OK/BLAD/None` (odpowiedź, rozpisane pary code=value). Log natychmiast ujawnił:
  `code=28841004 msg=No permissions. Your quota of Trial Edition is used up.`
- ROZPOZNANIE: projekt Tuya IoT Platform jest na **Trial Edition** i wyczerpał limit/okres próbny —
  Cloud REST API (device status) zwraca 28841004 przy każdym zapytaniu. To ograniczenie KONTA Tuya,
  NIE błąd kodu. Wymaga po stronie Tuya: Extend Trial lub włączenie płatnego planu / autoryzacja API.
- POTWIERDZONO (użytkownik): **Pulsar DZIAŁA** — dane licznika (add_ele, cur_power, cur_voltage,
  cur_current) i pompy nadal napływają strumieniem przez `save_with_fault_detection`. Pulsar (message
  service) i Cloud REST API to osobne usługi; padło tylko REST.
- DECYZJA: wątek pollingu licznika WYŁĄCZONY w `main()` — uruchomienie zakomentowane, funkcja
  `energy_meter_poll_loop()` (wraz z logami diagnostycznymi) POZOSTAWIONA na wypadek przywrócenia
  płatnego API (wtedy odkomentować blok). Przy starcie collectora log:
  `Watek pollingu licznika ...: WYLACZONY (Cloud API Trial wyczerpany; dane ida przez Pulsar).`
- EFEKT: znikają błędy `[POLL] <- BLAD Tuya: code=28841004` z logów. Zbieranie danych bez zmian (Pulsar).
- Weryfikacja: `ast.parse(main.py)` OK.

### Wykrywanie pracy pompy po pompie wody (flow_rate) — is_pump_running() (2026-09-09)
- ANALIZA SEKWENCJI (odczytowa, 57 zimnych startów CWU, sierpień 2026): potwierdzono kolejność
  startu i stopu agregatu. START: pompa wody (flow_rate) rusza ~115 s PRZED sprężarką (najpierw
  wolny obieg kontrolny ~5–6, potem docelowy ~15–17) → wentylator (dc_fan1) ~22 s przed → sprężarka
  (comp_freq) t=0 → valve przełącza na CWU ~8 s PO starcie sprężarki (nie na początku!).
  STOP: sprężarka rampą w dół → 0 → wentylator zjeżdża ~35 s → valve wraca na CO ~65–110 s po →
  pompa wody zatrzymuje się ~115 s po sprężarce (dobieg/odbiór ciepła resztkowego).
  To zgodne z notą o `pump_sta` (pompa obiegowa pracuje ~120 s po sprężarce).
- NOWA FUNKCJA `is_pump_running(flow_rate_raw, threshold)` w `app/core/physics.py` (czysty core,
  bez zależności). Zwraca True gdy flow_rate_raw > threshold. Odporna na None i błędne typy.
- NOWA STAŁA `FLOW_RATE_ON_THRESHOLD = 3.0` w `config.py` (surowe, ×0.1 m³/h → 0.3 m³/h). Odcina szum,
  łapie fazę wolnego obiegu kontrolnego. Próg zduplikowany jako `_DEFAULT_FLOW_RATE_ON_THRESHOLD`
  w physics.py (core nie importuje config; wołający podaje próg z config).
- ROZRÓŻNIENIE SEMANTYCZNE (kluczowe): `flow_rate > 3` = "AGREGAT PRACUJE" (hydraulika aktywna, pełny
  cykl); `comp_freq > 5` = "SPRĘŻARKA PRACUJE" (pobór energii, liczenie startów, SCOP). NIE zamieniano
  comp_freq w energy.py / analytics.py / compute_scop — tam semantyka to praca sprężarki i zamiana
  zafałszowałaby energię/SCOP (pompa wody działa ~2 min przed i po sprężarce).
- ZASTOSOWANO is_pump_running() tam, gdzie chodzi o stan urządzenia:
  - `main.py`: `_heat_pump_active` (steruje interwałem pollingu licznika) teraz po flow_rate.
    PRZY OKAZJI naprawiono BŁĄD: były DWIE definicje `save_with_fault_detection` — pierwsza
    (śledzenie stanu po comp_freq) była NADPISYWANA przez drugą, więc śledzenie `_heat_pump_active`
    wcześniej W OGÓLE NIE DZIAŁAŁO (interwał pollingu licznika stał na IDLE). Scalono w jedną funkcję.
  - `Panel.py`: `running` we fragmencie live (interwał odświeżania 60/300 s + tło nagłówka).
  - `app/ui/helpers.py` `get_pump_status()`: nowy stan pośredni "💧 Obieg wody" (#8BC34A) — gdy pompa
    wody pracuje, a sprężarka jeszcze/już stoi (faza pre-start i dobiegu). CO/CWU/Defrost/AWARIA bez zmian.
- TESTY: `tests/test_physics.py` — nowa klasa `TestIsPumpRunning` (9 testów: przepływ typowy, obieg
  kontrolny, postój, None, poniżej progu, granica progu, tuż nad progiem, własny próg, błędny typ).
  92 PASS (było 83 + 9 nowych). main.py importuje się poprawnie.

---

## Ustalenia i zmiany — 2026-09-04

### Operacje na produkcji Fly.io z Windows/PowerShell (2026-09-04) — POWTARZALNE
- PowerShell PSUJE escaping cudzysłowów w `fly ssh console -C "..."` — znaki `*` i `\` z zapytania
  SQL są interpretowane przez shell (błąd typu "The term '*' is not recognized..."). To ta sama
  pułapka co inline `python -c "..."`.
- ROZWIĄZANIE 1 (proste komendy): cały argument `-C` w POJEDYNCZYCH cudzysłowach, wewnątrz podwójne
  dla SQL, a apostrofy w SQL PODWOIĆ. Przykład:
  `fly ssh console -a scop -C 'sqlite3 db "SELECT * FROM t WHERE code=''add_ele'';"'`
- ROZWIĄZANIE 2 (pewniejsze, złożone skrypty): wgrać plik `.py` i uruchomić zdalnie:
  `fly ssh sftp put _skrypt.py /data/_skrypt.py -a scop`
  `fly ssh console -a scop -C 'python /data/_skrypt.py'`
  potem posprzątać: `fly ssh console -a scop -C 'rm /data/_skrypt.py'`.
- UWAGA: `sqlite3` CLI NIE jest zainstalowany w kontenerze produkcyjnym (obraz Pythona 3.12).
  Operacje na bazie robić modułem `sqlite3` z Pythona (Rozwiązanie 2), nie przez sqlite3 CLI.
- Komunikat `Error: The handle is invalid.` przy `fly ssh` na Windows to ARTEFAKT zamykania sesji
  SSH, NIE błąd — wyjście komendy wypisuje się poprawnie mimo tego komunikatu.

### Licznik — energia liczona z add_ele zamiast ZOH z cur_power (2026-09-04)
- ZMIANA: `cached_meter_energy()` w `helpers.py` teraz liczy zużycie z sumy przyrostów
  `add_ele` (×0.001 kWh), nie z całki ZOH po `cur_power`. Skala add_ele potwierdzona
  empirycznie: 1 jednostka = 1 Wh.
- NOWA FUNKCJA: `get_remote_meter_energy(ts_from, ts_to)` w `database.py` — suma add_ele
  w oknie czasu, zwraca kWh lub None przy braku danych.
- TELEGRAM: `build_daily_report()` używa teraz `get_remote_meter_energy` (licznik zdalny Tuya)
  zamiast funkcji ręcznego licznika. Stara funkcja (get_meter_energy_consumption) pozostawiona
  nieużywana do czasu czystki Zakresu 2.
- DLACZEGO: add_ele jest pewniejszym źródłem całkowitego zużycia niż ZOH z cur_power —
  całkuje sam licznik, odporny na dziury w telemetrii (przy postoju i restartach).
  ZOH z cur_power zaniżał przy brakujących próbkach.
- UWAGA: wykres mocy na stronie 4_Licznik pozostaje na cur_power (wizualizacja mocy
  chwilowej). Formularz ręcznego licznika (Zakres 2) nietknięty.

### Licznik — deduplikacja add_ele w collectorze (2026-09-04)
- POTWIERDZONO (analiza odczytowa), że duplikaty add_ele pochodzą z TUYA, NIE z naszego kodu:
  - pary mają RÓŻNE `id` i RÓŻNE `timestamp` (±1 s, sporadycznie dts=0) — dwie osobne ramki Pulsar,
    a nie jeden rekord zapisany dwa razy (ten miałby identyczny event_time);
  - `cur_power` w tym samym oknie NIE jest dublowany (62 próbki, brak par) — gdyby collector podwajał
    całe wiadomości, dublowałby też cur_power. Dubluje się WYŁĄCZNIE add_ele → to retransmisja Tuya;
  - ścieżka zapisu (client.listen → handle_parsed_payload → save_callback) przetwarza każdą ramkę raz
    i potwierdza acknowledge_cumulative. add_ele ma bypass filtra (zapis zawsze), więc obie ramki-bliźniaki
    trafiały do bazy.
- ZMIANA (`app/services/tuya_client.py`): deduplikacja add_ele po CZASIE ZDARZENIA (event_time z ramki).
  - Stała `ADD_ELE_DEDUP_SEC = 3`. Pomiń add_ele, jeśli (event_time - last_add_ele_time) < 3 s.
  - Dedup po CZASIE, nie po wartości — bo dwa RÓŻNE realne raporty też mogą mieć tę samą wartość (2 i 2),
    ale dzieli je ~1800 s. Próg 3 s odsiewa tylko parę-retransmisję (0–1 s), nie tnie realnych przyrostów.
  - Użyto event_time (czas zdarzenia Tuya), NIE time.time() — odporność na opóźnienia przetwarzania;
    to właśnie ts ±1 s odróżnia duplikat.
  - Nowe pole `DeadbandFilter.last_add_ele_time` (`__slots__` + init).
- EFEKT: w bazie 1 ramka add_ele na raport. Zadziała dopiero PO RESTARCIE collectora (stan w pamięci).
  Historyczne pary add_ele już w bazie ZOSTAJĄ — analizy danych sprzed zmiany nadal muszą deduplikować
  przy sumowaniu.
- TESTY: nowy `tests/test_tuya_client.py` (4 testy: duplikat ±1 s pomijany, duplikat dts=0 pomijany,
  realne raporty ~1800 s zachowane oba, granica progu). 78/78 PASS (było 74 + 4 nowe).

### Licznik — USTALONO znaczenie i skalę add_ele (2026-09-04, analiza odczytowa)
- CEL: rozszyfrować co oznacza `add_ele` z licznika energii (ENERGY_METER_DEV_ID).
- METODA (tylko odczyt): porównanie okno-po-oknie sumy `add_ele` z energią ZOH liczoną z
  `cur_power` (skala ×0.1 W) w tym samym oknie czasu. Dane nocne 2026-09-03 22:00 → 2026-09-04 ~08:00
  (postój pompy, pobór ~4 W).
- WYNIK — `add_ele` to PRZYROST energii w Wh. Skala = ×0.001 kWh (1 jednostka = 1 Wh).
  - Dowód globalny: ZOH/add_ele = 1.022 Wh na jednostkę (praktycznie 1:1).
  - Suma nocna: add_ele 34 jednostki vs ZOH 34.75 Wh → zbieżność ~98%.
  - Per okno: add_ele=2 ↔ ZOH ~1.7–2.0 Wh; add_ele=1 ↔ ZOH ~1.2–1.8 Wh (zgodne w granicach zaokrągleń).
- KOREKTA wcześniejszej hipotezy: w decyzjach z 2026-09-02 zapisano "prawdopodobnie ×0.01 kWh" —
  to BŁĄD. Poprawna skala to ×0.001 kWh (Wh).
- CHARAKTER: delta (przyrost), NIE stan skumulowany. Wartości oscylują 1–2, nie rosną monotonicznie.
- INTERWAŁ raportowania: stałe 30 min w postoju/niskim poborze. Wcześniejsze "~600 s (10 min)"
  dotyczyło innego trybu/obciążenia — interwał nie jest stały, zależy od poboru.
- DUPLIKATY: każda ramka `add_ele` przychodzi PODWOJONA (ten sam timestamp ±1 s, ta sama wartość) —
  duplikat transmisji Pulsar. Collector zapisuje add_ele ZAWSZE (bypass filtra), więc w bazie leżą
  pary. Przy SUMOWANIU trzeba DEDUPLIKOWAĆ (inaczej suma podwojona: 72 zamiast 36 za noc).
- OGRANICZENIE: rozdzielczość 1 Wh. Przy bardzo niskim poborze (postój ~4 W → ~2 Wh/30 min) błąd
  zaokrąglenia jest względnie duży (±0.5 Wh/okno). Pod obciążeniem (kWh/h) pomijalny.
- IMPLIKACJA: add_ele jest potencjalnie PEWNIEJSZYM źródłem całkowitego zużycia niż ZOH z cur_power
  (całkuje wewnętrznie licznik → brak dziur po restartach collectora). NIE zmieniano metody liczenia
  energii — cached_meter_energy() nadal ZOH/cur_power (decyzja użytkownika). Do rozważenia w sezonie
  grzewczym: potwierdzić skalę pod obciążeniem (add_ele dziesiątki–setki Wh/okno, bez szumu zaokrągleń)
  i ewentualnie wpiąć add_ele jako źródło całkowitego zużycia.

### Krzywa grzewcza — stan i wymagania (2026-09-04)
- FUNKCJA: `analyze_heating_curve()` w `analytics.py` — ANALIZUJE krzywą grzewczą
  na podstawie duty cycle sprężarki w trybie CO, uwzględnia nasłonecznienie
  (`weather_df` z `direct_radiation`/`diffuse_radiation`).
- UI: strona Analiza → zakładka Krzywa Grzewcza (2_Analiza.py → tab_heating_curve).
- WYMAGANIA DALSZEJ PRACY:
  - Funkcja obecnie ANALIZUJE (pokazuje rekomendacje), ale NIE STOSUJE automatycznie
    nastaw do pompy — brak write-back do Tuya/API.
  - Jakość rekomendacji zależy od pokrycia danych (min. 4h CO per przedział
    temperaturowy). Latem dane są ubogie (pompa głównie w trybie CWU).
  - Pełna kalibracja krzywej (dobór curve_low/curve_high) będzie możliwa dopiero
    w sezonie grzewczym, gdy są dostępne dane z CO przy zróżnicowanych temperaturach
    zewnętrznych.
  - Brak mechanizmu auto-apply (zmiana nastaw bezpośrednio w pompie).

---

## Ustalenia i zmiany — 2026-09-08

### UI — Panel: przycisk odświeżania i Bilans (2026-09-08)
- ZMIANA `Panel.py` (lines ~111-120): przycisk odświeżania teraz zawiera TYLKO ikonę i godzinę (`🔥 HH:MM:SS`).
  Usunięto tekst "Pompa Ciepła" i "odświeżanie co..." — oszczędność miejsca u góry strony.
- ZMIANA `Panel.py`: obok przycisku odświeżania dodano przycisk "Bilans" (link do pages/1_Bilans.py).
  Oba przyciski w jednym wierszu: `st.columns([1, 1])`.
- ZMIANA `app/ui/styles.py`: CSS `.st-key-pump_header { flex-wrap: nowrap }` — zapobiega zawinięciu
  przycisków w różnych wierszach na małych ekranach.

### UI — Bilans: combobox z pełnym zakresem (2026-09-08)
- ZMIANA `pages/1_Bilans.py` (lines ~30-55): combobox zakresu teraz zawiera pełną informację:
  `📅 7 dni (01-09 — 08-09)` (bez oddzielnego podpisu `st.caption`).
- Format dat: `dd-mm` (bez roku) — kompaktowy i czytelny.
- ZMIANA `app/ui/styles.py`: CSS `.st-key-bilans_range { flex-wrap: nowrap }` — unika zawinięcia.

### Licznik: naprawa importu (2026-09-08)
- ZMIANA `pages/4_Licznik.py`: naprawiono import z `from db import ...` na `from app.services.database import ...`.
  Funkcje używane: `save_manual_energy_reading`, `update_manual_energy_reading`, `delete_manual_energy_reading`.
- Użytkownik potwierdził: strona 4_Licznik działa poprawnie.

### Licznik: zbieranie napięcia i prądu (2026-09-08)
- ZMIANA `app/config.py` HISTERESIS_CONFIG: dodano `cur_voltage` (active=2.0, idle=3.0) i `cur_current` (active=2.0, idle=5.0).
  `cur_power` pozostaje z histerezą active=5.0, idle=5.0 (0.5 W).
- ZMIANA `app/services/tuya_client.py` (lines ~220-260): dla `ENERGY_METER_DEV_ID` collector zbiera 4 parametry:
  - `add_ele`: ZAWSZE (bypass filtra) — przyrost energii, skala 1 jednostka = 1 Wh.
  - `cur_power`: przez DeadbandFilter (histereza 0.5 W).
  - `cur_voltage`: przez DeadbandFilter (histereza 2/3 V).
  - `cur_current`: przez DeadbandFilter (histereza 2/5 mA).
  Dodatkowe kody licznika są ignorowane.
- Uwaga: surowe dane są trzymane w bazie, skale aplikowane są przy odczycie (jak `ac_curr`, `flow_rate`).

### Analiza: nowy moduł power_analysis.py (2026-09-08)
- NOWY MODUŁ `app/services/power_analysis.py`: funkcja `analyze_power_breakdown()` analizuje
  rozkład mocy ukrytej (hidden_power) na pompę obiegową i wiatrak.
- ZAŁOŻENIA:
  - `P_elektronika = 4W` (stały, 24/7)
  - `P_pompa = k * flow_rate`, gdzie `k=4` (max 80W przy flow_rate=20)
  - `P_wiatrak = P_total - P_sprzarka - P_pompa - P_elektronika`
- UŻYCIE: `add_ele` (licznik) i `ac_curr/ac_vol/flow_rate/dc_fan1` (pompa) z tych samych okien.
  Obliczenia energii w oknach 30-minutowych (zgodne z interwałem add_ele).
- WYNIK: rozkład energii na sprężarkę, pompę, wiatrak i elektronikę, porównanie z licznikiem.
  Przykład: `e_sprzarka_kwh`, `e_pompa_kwh`, `e_wiatrak_kwh`, `e_elec_kwh`, `total_e_kwh`, `add_ele_kwh`, `diff_pct`.
- Uwaga: moduł nieaktywny bez danych zimowych (wymaga współczesnych raportów `add_ele` i parametrów pompy).

### Testy i weryfikacja (2026-09-08)
- Kompilacja wszystkich zmodyfikowanych plików: OK (`python -m py_compile`).
- Import-check wszystkich modułów: OK.

---

## Ustalenia i zmiany — 2026-09-03

### Watchdog komunikacji (2026-09-03)
- Próg utraty komunikacji: 900s (15 minut), był 1500s (25 min).
- Licznik energii (ENERGY_METER_DEV_ID) wyłączony z watchdoga — w postoju (0 W) Tuya nie
  raportuje przez 1-2 h; heartbeat nie tworzy zapisów, więc cisza = normalny stan, nie utrata łączności.
- Zmiana: `main.py`: import `ENERGY_METER_DEV_ID` + `continue` w pętli watchdoga.

### Licznik — histereza cur_power + diagnoza gubionych danych (2026-09-03, sesja wieczorna)
- OBJAW: dashboard (strona Bilans, metryka "Prąd pobrany (licznik)") pokazał dzienny pobór 2.49 kWh,
  a zewnętrzna aplikacja licznika ~2.73 kWh. Wykres sugerował gubione ramki.
- DIAGNOZA (tylko odczyt, bez zmiany metody obliczeń): metoda ZOH z `cur_power` jest POPRAWNA.
  Rozbieżność wynika z BRAKU DANYCH — collector gubił ~12.9 h `cur_power` dziennie (53.9% doby),
  46 przerw > 360s. ZOH na niepełnych danych zaniża pobór. To nie błąd wzoru, tylko dziura w telemetrii.
- ZMIANA: `config.py` HISTERESIS_CONFIG["cur_power"] z {active:20.0, idle:20.0} (=2.0 W)
  na {active:5.0, idle:5.0} (=0.5 W). Mniejszy próg = więcej zapisów = mniej dziur po restarcie collectora.
  UWAGA: histereza to stan w pamięci DeadbandFilter — zmiana zadziała dopiero po RESTARCIE collectora
  (obecne dane w bazie już zapisane, nie zmienią się). Gubione ramki to też przerwy w połączeniu Pulsar,
  których sam próg nie usunie w 100%.
- SKALA add_ele (obserwacja diagnostyczna, NIE wdrożone): suma przyrostów `add_ele` × 0.001 kWh (= Wh)
  daje wyniki zbliżone do ZOH z cur_power (wczoraj: ZOH 0.99 kWh vs add_ele 2.02 kWh — add_ele wyżej,
  bo bez dziur). add_ele jest zapisywany ZAWSZE (bypass filtra), więc nie ma przerw. Potencjalne
  pewniejsze źródło całkowitego zużycia. NIE zmieniono cached_meter_energy() — pozostaje ZOH/cur_power
  (decyzja użytkownika: nie zmieniać sposobu wyliczania energii). Do rozważenia w sezonie grzewczym.
- 74/74 testy PASS po zmianie histerezy (zmiana nie dotyka logiki energii).

### Zasady współpracy — dodano regułę nr 5 (2026-09-03)
- Dodano zasadę "PROPOZYCJA PRZED IMPLEMENTACJĄ" do docs/project-decisions.md (reguła 5) ORAZ do
  definicji agenta `.kiro/agents/tuya-dev.json` (sekcja SPOSÓB PRACY, nadrzędna nad domyślnym zachowaniem).
- Treść: dla KAŻDEGO zadania najpierw propozycja rozwiązania i zatrzymanie; implementacja dopiero po
  jawnym "tak". Wyjątek: operacje wyłącznie odczytowe (czytanie, SELECT, analiza) — od razu.

### Kalibracja — parametry (2026-09-03)
- Standby_power_w: baza 15 → 4.0. Pomiar licznika: pompa w postoju ~4W (obwód = tylko pompa).
- Active_power_w: 300 (maksymalne obciążenie: pompa obiegowa + wentylator max + elektronika).
  Wartość potwierdzona (2026-09-11) jako realny dodatkowy pobór podczas pracy agregatu.
- Hidden_power_w: 0.0 (brak danych do rozdzielenia od sensor_factor w sezonie letnim).
- Sensor_factor: 0.98, cos_phi: 0.95.
- Priorytet: tabela `settings` (baza) > `DEFAULT_*` w `config.py`. Fallback tylko przy braku klucza.

### Model kalibracji — ZOH (Zero-Order Hold)
- Energię licznika (cur_power) całkuje się metodą lewego prostokąta (ZOH): `E = Σ (P_poprzednia × Δt)`.
- Brak `dt_max` — długie przerwy = wartość się nie zmieniła, trwa ostatnia moc.
- Odrzucono `add_ele` (skala niepotwierdzona).
- Zmiana: `app/ui/helpers.py`: `cached_meter_energy()` — ZOH, `df.ffill()` po concat,
  deduplikacja indeksu (`keep="last"`), `line_shape="hv"` w wykresie (4_Licznik.py).

### UI Bilans (2026-09-03)
- Nowa metryka: "⚡ Prąd pobrany (licznik)" — energia ZOH z `cur_power`, ten sam zakres co SCOP.
- Nowa kolumna w tabeli "Podział energii wg trybu": "E_el licznik [kWh]" — wartość tylko w wierszu Σ Total.
- Etykieta: `METRICS["e_el_meter"]` w `app/ui/labels.py`.

### Wykres mocy (4_Licznik.py, 2026-09-03)
- Surowe próbki zamiast resample 1min (średnia uśredniała schodki).
- `line_shape="hv"` + `connectgaps=True` — wykres schodkowy: wartość trzyma się do kolejnego raportu.
- Ffill po concat (dla wszystkich okresów, w tym gdy nie było danych licznika).
- Deduplikacja timestampów (`keep="last"`) przed concat — uniknięcie "cannot reindex on duplicate labels".

### Model moc pompy vs licznik
- Pompa raportuje tylko prąd sprężarki (ac_curr).
- Licznik mierzy całość (sprężarka + pompa obiegowa + wentylator + elektronika).
- Różnica przy pełnym obciążeniu: ~400W — to realny pobór obwodów pomocniczych.
- `active_power_w` = dodatek stały podczas pracy (300W — pompa obiegowa + wentylator + elektronika, potwierdzone 2026-09-11).


# Tuya Heat Pump Monitor v2 — Decyzje i Ustalenia

## Lokalizacje projektów
- v1 (produkcja): C:\tuya
- v2 (nowy silnik): C:\kiro\tuya_v2 (katalog bywa przenoszony tuya_v2<->tuva_v2; aktualnie tuya_v2)
- Dokumentacja: C:\tuya\docs\v2-plan.html, C:\kiro\tuya_v2\docs\ui-spec.html
- Baza danych: data/tuya_telemetry.db (SQLite, WAL)
- Deploy: Fly.io (1GB RAM, shared CPU)

## Deploy Fly.io (przygotowanie 2026-09-01)
- !!! ZASADA (2026-09-01): KAZDY deploy (`fly deploy`) wymaga JASNEJ, WYRAZNEJ zgody uzytkownika
  ZA KAZDYM RAZEM. Dotyczy takze poprawek/redeployow po nieudanym deployu — nie sa "kontynuacja"
  poprzedniej zgody. Bez wyraznego "tak, deployuj" — NIE deployowac.
- App: 'scop', region 'ams', wolumen 'tuya_data' montowany pod /data
- ZROBIONE:
  - .dockerignore: wyklucza .env (sekret!), data/*.db, __pycache__, .pytest_cache, *.png, docs/, .git
    (bez tego .env i baza trafialy do obrazu — wyciek sekretu + nadpisanie wolumenu)
  - fly.toml [env] DB_FILE = "/data/tuya_telemetry.db" — kieruje baze na wolumen (inaczej dane
    zapisywalyby sie do efemerycznego ./data w kontenerze i znikaly przy restarcie)
  - fly.toml healthcheck GET /_stcore/health (endpoint zdrowia Streamlit)
  - Zweryfikowano: caly runtime uzywa DB_FILE/db_file (brak hardkodowanych sciezek poza testami)
- DAILY_REPORT_HOUR: DECYZJA (uzytkownik) = 8. fly.toml ma "8" — zgodne, bez zmian. (v1 mial 21.)
- STAN FLY.IO (sprawdzone 2026-09-01, konto wiktorkmieciak@gmail.com):
  - Apka 'scop' JUZ WDROZONA (nie pierwszy deploy, tylko aktualizacja). v1 = 'hpmonitor' (suspended).
  - Wszystkie 5 sekretow ustawione: TUYA_ACCESS_ID/KEY/DEVICE_IDS, TELEGRAM_BOT_TOKEN/CHAT_ID.
  - Wolumen tuya_data (1GB) w regionie 'ams', maszyna w 'ams'.
  - FIX: fly.toml primary_region 'arn' -> 'ams' (bylo niezgodne z wolumenem! deploy w arn = brak danych).
  - FIX: Dockerfile python:3.11 -> 3.12 (pyproject wymaga >=3.12; bylo niezgodne).
  - Import-check wszystkich modulow OK na 3.12. Docker lokalnie niedostepny (build tylko na Fly).
- Token Telegram: DECYZJA 2026-09-01 — nie rotujemy, zostaje obecny token na Fly.io.

## Strefa czasowa (naprawione 2026-09-01, zaktualizowane 2026-09-03)

### Automatyczna obsługa DST (od 2026-09-03)
- Funkcja `get_timezone_offset()` w `app/config.py` używa `zoneinfo` do automatycznego
  wyliczania offsetu dla strefy `Europe/Warsaw`:
    - Latem (CEST): +2
    - Zimą (CET): +1
- `SERVER_TIMEZONE_OFFSET` jest inicjalizowane przy starcie aplikacji
- Fallback w `.env` lub `fly.toml` jest ignorowany — funkcja zawsze liczy dynamicznie
- Brak potrzeby ręcznej zmiany przy przejściu na czas zimowy

### Historyczne informacje (dla dokumentacji)
- USTALENIE (zweryfikowane empirycznie): timestampy w bazie to epoch UTC (time.time() / Tuya ms/1000).
  Niezalezne od strefy procesu serwera. Ostatni zapis 06:41 UTC = 08:41 czasu PL (CEST).
- KONWENCJA (jednolita): SERVER_TIMEZONE_OFFSET / time_offset_hours = offset czasu LOKALNEGO vs UTC.
  Polska: +2 (CEST lato), +1 (CET zima). czas_lokalny = UTC + offset.
- BLAD: fly.toml mial "-2" (stara konwencja v1 "uzytkownik vs serwer-UTC"). Powodowal podzial dob
  w energy.py po UTC-2 zamiast UTC+2 -> energia w zlej dobie, daily_breakdown przesuniety.
- FIX:
  - fly.toml: SERVER_TIMEZONE_OFFSET = "2" (bylo "-2").
  - notifier.build_daily_report(): "wczoraj" i granice dob liczone z datetime.now(timezone.utc)+offset,
    granica doby epoch = combine(dzien, tz=utc) - offset*3600. Niezalezne od strefy procesu.
  - main.daily_report_loop(): godzina wysylki utc_hour = (DAILY_REPORT_HOUR - offset) % 24,
    porownanie z datetime.now(timezone.utc). Bylo (HOUR + offset) i now() lokalny.
  - Panel._load_chart_data i 2_Licznik: '+2 hours' zamienione na dynamiczne '{offset:+d} hours'
    (poprawne rowniez zima CET=+1).
  - 2_Licznik reczne dodanie odczytu: wpisany czas traktowany jako lokalny -> epoch UTC (- offset).
- Weryfikacja: raport dzienny bierze poprawna dobe (24.0h, 31.08 00:00-24:00 lokalnie), 74/74 testy PASS.

## Kalibracja — ustalenie domyslnych parametrow (2026-09-01)
- ANALIZA: porownano telemetrie z fizycznym licznikiem na 14 czystych parach odczytow
  (15.08-01.09, dokladne dopasowanie okien co do godziny odczytu).
  Suma: licznik 49.80 kWh vs telemetria 52.26 kWh -> ratio 0.953 (telemetria zawyza ~5%).
  Optymalny fit: sensor_factor ~0.98, hidden_power ~0.
- USTAWIONO domyslne: DEFAULT_SENSOR_FACTOR=0.98, DEFAULT_HIDDEN_POWER_W=0.0 (config.py).
  Zmienione tez: 3 sidebary (Panel/Bilans/Porownanie value=0.98), notifier fallback "0.98",
  domyslne parametry compute_energy()/cached_energy().
- Efekt: E_el all-time 60.72 -> 59.51 kWh (-2%), SCOP_real 3.277 -> 3.344. 74/74 testy PASS.
- WAZNE ograniczenie: na danych LETNICH (tylko CWU) NIE da sie rozdzielic sensor_factor od
  hidden_power (wspolliniowosc, least squares daje bezsens: factor=1.25/hidden=-37W).
  Rozdzielenie mozliwe dopiero z danymi zimowymi (CO, rozne proporcje praca/postoj).
- BLAD W PIERWSZEJ ANALIZIE (odrzucony): wczesniejszy wniosek o "zawyzaniu 1.8x, sensor_factor 0.55"
  byl skutkiem 2 bledow skryptu: (1) obcinanie godzin odczytow do daty %Y-%m-%d = niedopasowane okna,
  (2) pary sprzed startu telemetrii pompy (06-14.08). Po poprawie telemetria zgadza sie z licznikiem w ~95%.
- 2026-09-01: uzytkownik USUNAL z bazy 2 odczyty licznika sprzed startu telemetrii (przed 14.08).
  Zostalo 15 odczytow, wszystkie w okresie pokrytym telemetria (pierwszy 15.08 12:44). Upraszcza porownania.
- DECYZJA (2026-09-01): Faza 3 (auto-kalibracja) WSTRZYMANA. Wracamy gdy:
  (1) bedzie licznik Tuya (czeste odczyty co minute zamiast 1/dzien -> lepsza separacja parametrow),
  (2) ruszy sezon grzewczy (dane CO -> rozne proporcje praca/postoj -> mozna rozdzielic sensor_factor od hidden_power).
  Do tego czasu: recznie ustawione sensor_factor=0.98, hidden_power=0 (dobre dla lata CWU).
- compute_calibration() (faza 1) szuka DODATNIEGO hidden_power — na tych danych zawodzi
  (zwraca 1.0/0). Przy dokanczaniu Fazy 3 zmienic podejscie na fit sensor_factor. Rozwazyc tez
  dopasowanie okien co do godziny odczytu (obecnie compute_calibration liczy per data %Y-%m-%d).
- CENTRALIZACJA (2026-09-01): wszystkie 5 parametrow kalibracji (cos_phi, standby_power_w,
  active_power_w, hidden_power_w, sensor_factor) pochodzi z JEDNEGO zrodla = config.py
  (DEFAULT_*). Importuja je: energy.compute_energy(), helpers.cached_energy(),
  3 sidebary (Panel/Bilans/Porownanie), notifier fallback (str(DEFAULT_*)).
  Zmiana w config propaguje wszedzie PO restarcie procesu (Python wiaze defaults przy imporcie).
  Wyjatek: calibration.py CalibrationResult/apply_calibration maja 0.0/1.0 — to znaczy
  "brak wyniku kalibracji", nie domyslna wartosc aplikacji (celowo nie z config).
- KOREKTA (2026-09-02): "jedno zrodlo prawdy" jest NIESCISLE. load_calibration() daje
  PRIORYTET tabeli settings (baza), a DEFAULT_* z config to tylko FALLBACK gdy klucza brak
  w bazie (kod: raw=get_setting(key,None); cal[key]=float(raw) if raw is not None else default).
  WNIOSEK: zmiana DEFAULT_* w config NIE zadziala, jesli klucz istnieje w settings.
  Zeby zmienic wartosc uzywana realnie -> set_setting(key, val) do bazy.
  Dla spojnosci warto trzymac config==baza (config = wartosc startowa dla pustej instalacji).
- ZMIANY PARAMETROW (2026-09-02, ustawione w BAZIE + zsynchronizowany config):
  - standby_power_w: baza 15 -> 4 (pomiar licznika, obwod = tylko pompa). Config DEFAULT tez 4.
  - active_power_w:  baza 20 -> 60 (2026-09-02), później -> 300 (2026-09-11: realny pobór
    pompa obiegowa + wentylator + elektronika). Config DEFAULT_ACTIVE_POWER_W zsynchronizowany na 300.
  Weryfikacja: load_calibration() zwraca standby=4.0, active=300.0.
- FIX (2026-09-02): Panel.py _load_chart_data mial zahardkodowane device_id="bf874f7ae72aca1fc23op0".
  Zamienione na import HEAT_PUMP_DEV_ID z config (jak reszta plikow). Bylo jedyne miejsce z hardkodem.

## Licznik pradu Tuya — ZAMONTOWANY (2026-09-02)
- Zamontowano inteligentny licznik pradu z odczytem zdalnym. device_id = bf215e9c483af020b12cak
- To samo konto Tuya co pompa -> dane plyna tym samym strumieniem Pulsar (zero nowego pollingu).
- Stala ENERGY_METER_DEV_ID w app/config.py.
- Collector (tuya_client.py): dla tego device_id BYPASS DeadbandFilter — zapis KAZDEJ ramki
  surowo (etap rozpoznania). Licznik zawsze przepuszczany, nawet gdy TUYA_DEVICE_IDS ogranicza.
- database.save_properties_to_db: regula pomijania ac_vol (gdy pompa stoi) NIE dotyczy licznika.
- Dane wspolistnieja z pompa w tabeli telemetry, rozrozniane przez device_id
  (wszystkie SELECT filtruja po device_id — zweryfikowane).
- KODY DP i SKALE (zaobserwowane 2026-09-02, wartosci trzymane SUROWO w bazie):
  - cur_voltage: napiecie sieci, skala ×0.1 V  (raw 2393 -> 239.3 V)
  - cur_current: prad, skala ×0.001 A / mA     (raw 254 -> 0.254 A prad POZORNY/RMS)
  - cur_power:   moc CZYNNA [W], skala ×0.1 W   (raw 40 -> 4.0 W) — POTWIERDZONE przez uzytkownika
  - add_ele:     przyrost energii (raw 1,2 co ~10 min) — skala/charakter DO USTALENIA
    (przyrost vs total, prawdopodobnie ×0.01 kWh). Wymaga dluzszej obserwacji pod obciazeniem.
- USTALENIE (2026-09-02): cur_power != cur_voltage × cur_current. Zweryfikowane na 33 parach:
  V×I ~= 60 VA (moc POZORNA), a cur_power ~= 4 W (moc CZYNNA), ratio cosphi ~= 0.06.
  cur_current to prad pozorny (RMS); licznik osobno mierzy moc czynna z cosphi.
  NIE da sie odtworzyc cur_power z V×I — redukcja zawyzylaby pobor ~15x.
- CZESTOTLIWOSC (zmierzone 2026-09-02, tryb surowy): ~260 rek/h, ~6200/dobe, ~2.3 mln/rok.
  cur_power co ~29s, cur_voltage ~31s, cur_current ~39s, add_ele ~600s (10 min).
- DECYZJA (2026-09-02): WARIANT MINIMALNY — koniec etapu rozpoznania. Collector zapisuje z licznika:
  - cur_power: przez DeadbandFilter, HISTERESIS_CONFIG["cur_power"]={active:20,idle:20} = 2.0 W (surowo).
    (AKTUALIZACJA 2026-09-03 wieczor: zmieniono na {active:5,idle:5} = 0.5 W — patrz wpis
    "Licznik — histereza cur_power + diagnoza gubionych danych" powyzej.)
  - add_ele:   ZAWSZE (bypass filtra) — inaczej powtorzone przyrosty energii zostalyby zgubione.
  - cur_voltage, cur_current: POMIJANE w collectorze (tylko diagnostyka, nie energia).
  Logika w tuya_client.handle_parsed_payload (whitelist per dev_id == ENERGY_METER_DEV_ID).
- Skale aplikowac przy ODCZYCIE (jak ac_curr/flow_rate), nie przy zapisie — surowe dane zostaja.
  UWAGA: WYJATEK to temperatury (TEMP_CODES) — te SA dzielone /10 przy ZAPISIE (round(raw/10,1)),
  w bazie leza juz przeskalowane. Reszta liczb (ac_curr, flow_rate, ac_vol, comp_freq, cur_*)
  trzymana SUROWO. Zweryfikowane 2026-09-02 na realnych danych: out_water_temp=28.7 (juz /10),
  ac_curr=126 / flow_rate=50 (surowe).
- TODO (sezon grzewczy): potwierdzic skale cur_power pod obciazeniem (pompa zima),
  ustalic add_ele (przyrost vs total, skala), wpiac licznik jako fizyczne zrodlo
  do kalibracji hidden_power_w + sensor_factor.

## Architektura v2
- Jedna funkcja compute_energy() jako jedyne zrodlo prawdy dla SCOP/energii
- Jedna funkcja compute_scop() jako jedyne zrodlo WZORU SCOP (wszystkie strony i silnik jej uzywaja)
- Liczy ZAWSZE z surowych danych (tabela telemetry), NIGDY po resample
- Resample sluzy WYLACZNIE do wizualizacji wykresow
- Czysty Python w core/ (bez Streamlit) - cache naklada warstwa UI
- Chunked computation (7-dniowe kawalki) dla zakresow >14 dni - stale ~15MB RAM

## Problem SCOP (glowne odkrycie)
- SCOP zmienial sie z agregacja bo energia byla liczona po resample
- Resample 5min daje blad 42% na E_th, 7% na E_el
- Na surowych danych (mediana dt=3s) metoda calkowania nie ma znaczenia (prostokatki vs Simpson = +/-0.002 SCOP)
- Jedyny problem to resample - rozwiazanie: liczyc raz z surowych

## Ujednolicenie SCOP (2026-09-01)
- Kanoniczna funkcja compute_scop(e_el_co, e_el_cwu, e_el_standby, e_th_co, e_th_cwu, e_th_defrost, scope, kind)
  w app/core/energy.py — JEDYNE zrodlo wzoru. Wszystkie strony UI i silnik ja wolaja.
- scope: "total" | "co" | "cwu". kind: "real" (z defrostem) | "nominal" (bez defrostu).
- Definicje (wariant real, z odliczeniem defrostu):
  - SCOP total = (E_th_CO + E_th_CWU + E_th_defrost) / (E_el_CO + E_el_CWU + E_el_standby)
  - SCOP CO    = (E_th_CO + E_th_defrost) / E_el_CO   (defrost obciaza TYLKO CO)
  - SCOP CWU   = E_th_CWU / E_el_CWU                  (defrost NIE dotyczy CWU)
- Standby (sprezarka OFF) wchodzi do mianownika WYLACZNIE dla scope="total".
- E_th_defrost jest ujemne — w kodzie DODAJEMY je (dodanie liczby ujemnej = odjecie strat).
  W kodzie: numerator = e_th_co + e_th_cwu + e_th_defrost. W opisach UI: "cieplo - straty defrostu".
- wrapper scop_from_result(result, scope, kind) dla wygody z obiektu EnergyResult.
- POWOD ujednolicenia: rozne strony liczyly SCOP 4 roznymi wzorami (m.in. karty Porownania nie
  odejmowaly defrostu, tabela odejmowala; single vs chunked mial inny mianownik standby).
- BUGFIX: _compute_chunked (>14 dni) pomijal standby w mianowniku — teraz identycznie jak single.
- Karty (Panel, Bilans, Porownanie) pokazuja SCOP real (z defrostem + standby w mianowniku).
- SCOP nominal zostawiony jako pozycja edukacyjna/diagnostyczna (Bilans + strona Wiedza).

## Czujnik pradu (wazne odkrycie)
- ac_curr mierzy TYLKO sprezarke, nie caly agregat
- Pompa obiegowa, wentylator standby, elektronika - osobny obwod, niewidoczny w czujniku
- pump_sta pracuje ~2min po wylaczeniu sprezarki, ale ac_curr=0 natychmiast
- Licznik fizyczny widzi ~20 Wh/h wiecej niz telemetria
- NIE DA SIE naprawic progami ani histereza - to ograniczenie hardware
- AKTUALIZACJA (2026-09-02): licznik Tuya (ENERGY_METER_DEV_ID) jest na obwodzie
  gdzie JEST TYLKO POMPA CIEPLA -> cur_power = pelny realny pobor pompy (moc czynna).
  W standby (sprezarka OFF) licznik pokazuje ~4 W (nie 25). Zmieniono
  DEFAULT_STANDBY_POWER_W: 25.0 -> 4.0 w config.py. To pierwszy pomiar prawdy absolutnej
  niezaleznej od sondy. IMPLIKACJA: model liczy mniejszy pobor w postojach -> nizsza
  E_el_standby, wyzszy SCOP total. Poprzednia kalibracja standby=25 nieaktualna.
  TODO: przy sezonie grzewczym zweryfikowac tez active_power_w i sensor_factor wzgledem licznika.

## Model kalibracji (kluczowa decyzja)
- E_el_real = E_el_sensor x sensor_factor + hidden_power_w x total_hours / 1000
- hidden_power_w (~20W): staly pobor niewidoczny w czujniku, ADDYTYWNY, 24/7
- sensor_factor (~1.0): korekcja proporcjonalna czujnika, MULTIPLIKATYWNY
- DLACZEGO NIE staly factor: latem hidden=15% energii, zima=0.1% - staly x1.21 zawyzy E_el zima o 21%
- cos_phi=0.95 zostawiony jako parametr, sensor_factor kompensuje reszte
- Kalibracja z licznika: okno [ts_odczytu_N-1, ts_odczytu_N], nie doba kalendarzowa

## Parametry pompy
- Device ID: bf874f7ae72aca1fc23op0
- Temperatury w bazie JUZ przeliczone (dzielone przez 10 przez collector) - NIE dzielic ponownie!
- comp_freq > 5 = sprezarka pracuje
- valve >= 0.5 = CWU, < 0.5 = CO
- Sezon grzewczy: 1 wrzesnia - 30 kwietnia
- DeadbandFilter: heartbeat 300s, histereza active/idle per parametr

## Detale implementacyjne
- dt_max = 360s (nie 300! heartbeat jitter daje 301-318s)
- Seed ffill: pobierz ostatni stan sprzed date_from dla kazdego kodu
- e_th_defrost ZAWSZE ujemne (konwencja znaku) — gwarantowane warunkiem p_th < 0 w energy.py.
  compute_scop() DODAJE te wartosc (dodanie ujemnej = odjecie strat). Opisy UI mowia "minus straty".
- compute_energy() w core/ bez @st.cache_data (Telegram thread nie ma kontekstu Streamlit)
- time_offset_hours=2 (CEST) do podzialu dob w daily_breakdown

## Stan implementacji (2026-09-01)
- 74/74 testow PASS (bylo 64; +10 dot. compute_scop i spojnosci chunked/daily)
- Silnik core/: config.py, models.py, physics.py, energy.py, calibration.py
- Silnik: dodana kanoniczna compute_scop() + scop_from_result() w energy.py
- UI: Panel.py + pages/ (1_Bilans, 2_Licznik, 3_Wiedza, 4_Porownanie) — ZAIMPLEMENTOWANE
- Warstwa UI: app/ui/ (labels.py, styles.py, helpers.py)
- Serwisy: app/services/ (analytics.py, notifier.py, database.py, tuya_client.py, exporter.py)
- Testy: test_physics.py, test_energy.py, test_calibration.py
- Ostatnia zmiana: ujednolicenie liczenia SCOP przez jedna funkcje + bugfix standby w chunked
- DEPLOY: wykonuje UZYTKOWNIK samodzielnie (poza asystentem). Asystent NIE deployuje.
- Nastepny krok: (do ustalenia z uzytkownikiem)

## Powiadomienia Telegram (zweryfikowane 2026-09-01)
- app/services/notifier.py: send_telegram(), send_fault_alert(), send_fault_resolved(),
  send_communication_lost(), build_daily_report(), send_daily_report()
- Alerty krytyczne (awaria, rozwiazanie, utrata komunikacji) z throttlem 10 min (ALERT_COOLDOWN_SEC)
- Raport dzienny uzywa compute_energy() i result.scop_real (= compute_scop total/real) — spojny z UI
- TELEGRAM_ENABLED = True tylko gdy USTAWIONE OBA: TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID
- Sekrety trzymane jako Fly.io secrets (produkcja); lokalnie w .env (w .gitignore)
- config.py NIE ma load_dotenv() — lokalnie .env trzeba wczytac recznie lub ustawic env.
  Na Fly.io nieistotne (sekrety wstrzykiwane do srodowiska). Decyzja: zostawic jak jest.
- Test wysylki 2026-09-01: send_telegram OK, raport dzienny OK (SCOP 3.66 za 2026-08-31).
- UWAGA chat_id: prywatny czat ma ~10 cyfr (dodatnie), grupy ujemne (-100...).
  Poprawny chat_id odczytac z GET /getUpdates po napisaniu do bota (bot nie pisze pierwszy).

## Collector (main.py) - NIE RUSZAC
- Dziala poprawnie, zbiera dane z Tuya Pulsar
- 4 watki: collector, pogoda, watchdog, raport dzienny
- DeadbandFilter daje geste probki (mediana 3s podczas pracy)

## UI v2 (zaimplementowane 2026-09-01)
- Strony: Panel Glowny (Panel.py), Bilans i SCOP, Licznik, Baza Wiedzy, Porownanie Okresow, Analiza Parametrow
- Mobile-first: metryki + kolorowy status na telefonie, wykresy tylko desktop
- Baza Wiedzy jako osobna strona (pages/3_Wiedza.py) + About/Help w sidebarze
- Status pompy kolorami: CO=niebieski, CWU=pomaranczowy, Postoj=szary, Awaria=czerwony, Defrost=cyan
- Wszystkie strony licza SCOP przez compute_scop() — spojne wyniki
- SCOP na kartach: real (z defrostem + standby w mianowniku); tabele: total/CO/CWU + nominal

## Strona Analiza Parametrow (2026-09-01, pages/5_Analiza.py)
- Przepisana z v1 na architekture v2. 4 zakladki: Hydraulika/ΔT, Sprezarka/Taktowanie,
  Defrost/Obieg Chlodniczy, Krzywa Grzewcza. Zakladka COP z v1 POMINIETA (SCOP jest w Bilansie).
- KLUCZOWA ROZNICA vs v1: v1 uzywal process_telemetry() (pivot z resample). v2 nie ma tego modulu.
  Zamiast tego app/ui/analiza_helpers.py::load_analiza_pivot() buduje pivot 'v1-compatible'
  z SUROWYCH danych (ffill, konwersja val_str bool, skale flow ×0.1, COP chwilowy, delta_t,
  Tryb CO/CWU, comp_on, work_period, defrost_num/start, dt_hours).
- WAZNE: ten pivot sluzy TYLKO do wizualizacji/diagnostyki (wykresy, cykle, COP chwilowy).
  NIE liczy energii/SCOP — od tego jest compute_energy(). Zgodne z zasada v2 (resample tylko do wykresow).
- app/ui/tab_heating_curve.py: port z v1 (logika bez zmian). analyze_heating_curve() +
  HeatingCurveAnalysis/HeatingCurveBin JUZ istnialy w analytics v2. Wymaga pivotu z kolumnami
  amb_temp/comp_freq/Tryb/heat_temp_set/out_water_temp/COP/czas — dostarcza je load_analiza_pivot.
- Weryfikacja 2026-09-01: pivot all-time 47108 wierszy, wszystkie wymagane kolumny OK,
  COP mediana 4.06, 39 cykli sprezarki, 0 defrostow (lato). Kompilacja 3 plikow OK.

## Zmiany UI i produkcja (2026-09-01, wdrozone na scop v132)
- Kolejnosc stron: Panel -> Bilans -> Analiza -> Porownanie -> Licznik -> Wiedza
  (pliki: 1_Bilans, 2_Analiza, 3_Porownanie, 4_Licznik, 5_Wiedza; referencje page_link/switch_page zaktualizowane).
- About/Help: wspolny komponent render_about() w styles.py, na KAZDEJ stronie w sidebarze
  (opcja 1 — Wiedza zostaje w menu + link w About).
- Panel: auto-refresh przez @st.fragment(run_every=) — 60s praca / 300s postoj + przycisk "Odswiez dane".
  UWAGA: we fragmencie wolamy compute_energy() BEZPOSREDNIO (nie cached_energy) — @st.cache_data
  w kontekscie @st.fragment rzucalo UnserializableReturnValueError na EnergyResult (Streamlit 1.61.1).
- Panel: fix klasyfikacji trybu — valve/defrost czytane z val_str (bool), nie val_num (helper _flag_value).
  Wczesniej zawsze pokazywalo "CO — Grzeje" nawet podczas CWU.
- Panel layout: SCOP box po LEWEJ (Total duzy z oznaczeniem opłacalnosci ≥3.1 zielony/<3.1 czerwony +
  CO/CWU pod spodem), 3 metryki (COP/Energia/Cieplo) pionowo po prawej. Temperatury: 2 paski CO/CWU
  z markerem nastawy (render_temp_bar_setpoint) zamiast 4 osobnych. Usunieto duplikat metryki SCOP.
- Bilans: naglowek "Bilans i SCOP" (krotszy na telefon) + podpis okresu (📅 widoczny bez sidebaru) +
  wykres "COP chwilowy w czasie" przed SCOP dziennym.
- Analiza/Hydraulika: jeden kafelek ΔT aktualny wg biezacego trybu + ΔT sredni; strefa normy na
  wykresie ΔT zsynchronizowana z trybem (CO 3-7, CWU 5-10). Wykres przeplywu + linia "moc generowana"
  (P_th_kw, druga os Y). Dodano kolumny P_th_kw/P_el_kw w load_analiza_pivot.
- Analiza/Sprezarka: PRZEBUDOWA taktowania. Alarm taktowania liczony TYLKO z cykli CO i PER DOBA
  (>15 startow CO/dobe), nie ze sredniej po oknie (mylace latem). Mediana czasu cyklu CO (odporna na
  outliery), cykle CWU informacyjnie bez oceny (CWU nie taktuje). Wykres startow CO/dobe + histogram CO vs CWU.
- requirements: streamlit 1.44.1 -> 1.61.1 (width="stretch" nie dzialal w 1.44; podniesiono do wersji lokalnej).
  Dockerfile python:3.11 -> 3.12 (zgodnie z pyproject requires-python>=3.12).

## Responsywny uklad metryk — Panel i Bilans (2026-09-01, sesja wieczorna)
- ZASADA: Streamlit renderuje HTML po stronie serwera i NIE zna szerokosci ekranu przegladarki
  (brak informacji "telefon vs desktop" w Pythonie). Roznicowanie ukladu robimy CSS-em (media queries),
  a nie logika Pythona. Wybrano to zamiast komponentu mierzacego szerokosc (dodatkowa zaleznosc + migotanie).
- MECHANIZM: bloki metryk owijane w st.container(key="...") -> Streamlit generuje wrapper
  .st-key-<key>. CSS scope'owany do tego klucza celuje w wewnetrzne [data-testid="stHorizontalBlock"]
  (to jest st.columns) i jego dzieci [data-testid="stColumn"]. Reguly w inject_css() (styles.py).
- Panel (Panel.py):
  - Nowe metryki chwilowe: ⚡ Pobor pradu (p_el_kw) i 🔥 Moc pompy (p_th_kw), oba w kW,
    liczone z p_el_raw/p_th_raw (/1000), "—" gdy pompa stoi (p_el_raw<=100). Etykiety w labels.py:
    METRICS["p_el_instant"], METRICS["p_th_instant"].
  - Uklad gornego bloku owiniety w st.container(key="panel_top"): st.columns([2,3]) = SCOP lewo,
    metryki prawo (COP pelna szer. + 2 pary 2-kolumnowe: moce, energia/cieplo).
  - CSS responsywny: DESKTOP = SCOP + metryki obok siebie (domyslne Streamlit). TELEFON (max-width:640px)
    = .st-key-panel_top [stHorizontalBlock] { flex-direction: column } -> SCOP pelna szer., metryki pod spodem.
    Wewnetrzne pary metryk wymuszone nowrap (row) TAKZE na telefonie -> zostaja 2 obok siebie.
  - Licznik odswiezania (interwal + godzina) PRZENIESIONY z osobnego st.caption do etykiety przycisku
    "🔥 Pompa Ciepla · odswiezanie co 1 min · HH:MM:SS" (oszczednosc miejsca u gory). font-size przycisku
    1.3rem -> 1.05rem + line-height, by dluzszy tekst sie miescil. Emoji stanu 🟢/⚪ usuniete (tlo przycisku
    juz sygnalizuje prace ogien / postoj szare).
- Bilans (1_Bilans.py):
  - Bloki KPI owiniete: st.container(key="bilans_kpi") = 4 SCOP + 3 metryki energii;
    st.container(key="bilans_stats") = 4 statystyki. (Dwa osobne klucze — Streamlit nie pozwala na 2
    kontenery z tym samym kluczem.)
  - CSS TELEFON (max-width:640px): flex-wrap: WRAP + flex-basis calc(50%-0.25rem) -> metryki zawijaja
    sie PO 2 na rzad (4 boxy => 2x2, 3 boxy => 2+1). Rozni sie od Panelu (tam nowrap = stale 2 obok siebie),
    bo Bilans ma rzedy po 3 i 4 boxy.
- WAZNE ograniczenie: CSS oparty na wewnetrznych [data-testid] Streamlita (stHorizontalBlock/stColumn/stMetric).
  Dziala na 1.61.1; przy wiekszej aktualizacji Streamlita selektory moga wymagac korekty.
- Breakpoint 640px stosowany jednolicie (Panel + Bilans + zmniejszenie czcionki metryki na waskim ekranie).
- Env lokalny: Python 3.14.4 (produkcja/Docker = 3.12). Zaobserwowano dump watkow watchdog/streamlit przy
  disconnect_session (observer.join()) — NIE crash, aplikacja dzialala; user potwierdzil "nic sie nie stalo".
  Nie zmieniano nic. Ewentualne obejscie na przyszlosc: --server.fileWatcherType none (lokalnie).


## Parametry pompy ciepla — opisy i histereza DeadbandFilter

### Temperatury hydrauliczne (wartosci w bazie JUZ dzielone przez 10 przez collector)
| Kod | Opis | Histereza active | Histereza idle |
|-----|------|-----------------|----------------|
| in_water_temp | Temperatura wody powracajacej z instalacji (powrot CO) | 0.2°C | 0.5°C |
| out_water_temp | Temperatura wody wychodzacej na dom (zasilanie CO) | 0.2°C | 0.5°C |
| tank_temp | Temperatura wody w zasobniku CWU | 0.2°C | 0.5°C |

### Temperatury otoczenia i wewnetrzne
| Kod | Opis | Histereza active | Histereza idle |
|-----|------|-----------------|----------------|
| amb_temp | Temperatura powietrza na zewnatrz budynku | 0.5°C | 0.8°C |
| tidr | Temperatura pokojowa (czujnik wewnetrzny, NIE ssania) | 0.5°C | 0.5°C |

### Temperatury ukladu chlodniczego
| Kod | Opis | Histereza active | Histereza idle |
|-----|------|-----------------|----------------|
| disc_temp | Temperatura gazu na wylocie sprezarki (tloczenie) | 0.5°C | 1.5°C |
| back_temp | Temperatura czynnika na ssaniu sprezarki | 0.5°C | 1.5°C |

### Nastawy temperatur (wartosci historyczne mogl byc niedzielone, >100 = /10)
| Kod | Opis |
|-----|------|
| heat_temp_set | Nastawa CO strefa 1 (np. 41°C) |
| heat_temp_set_z2 | Nastawa CO strefa 2 / podlogowka (np. 28°C) |
| hot_water_temp_set | Nastawa CWU |
| idr_temp_set | Nastawa wyliczona z krzywej grzewczej |
| cool_temp_set | Nastawa chlodzenia Z1 |
| cool_temp_set_z2 | Nastawa chlodzenia Z2 |
| auto_heat_temp_set_z1/z2 | Nastawy auto grzanie Z1/Z2 |
| auto_cool_temp_set_z2 | Nastawa auto chlodzenie Z2 |

### Parametry elektryczne i mechaniczne (surowe wartosci, NIE dzielone)
| Kod | Opis | Skala | Histereza active | Histereza idle |
|-----|------|-------|-----------------|----------------|
| ac_vol | Napiecie zasilania | V (surowe) | 2.0 | 3.0 |
| ac_curr | Prad pobierany (TYLKO sprezarka!) | x0.1 A (35=3.5A) | 2.0 | 5.0 |
| comp_freq | Czestotliwosc sprezarki | Hz, max 120 | 2.0 | 1.0 |
| flow_rate | Przeplyw wody | x0.1 m3/h (25=2.5) | 2.0 | 1.0 |
| m_eev | Glowny zawor rozprezny EEV | 0-480 krokow | 5.0 | 20.0 |
| a_eev | Dodatkowy zawor EEV | 0-480 krokow | 5.0 | 20.0 |
| dc_fan1 | Wentylator DC jednostki zewnetrznej | 0-1000 RPM | 15.0 | 50.0 |
| dc_fan2 | Drugi wentylator DC | RPM | 50.0 | 50.0 |

### Flagi binarne (zapisywane jako val_str "True"/"False", NIE val_num!)
| Kod | Opis | Heartbeat |
|-----|------|-----------|
| valve | Zawor 3-drozny CO/CWU (True=CWU, False=CO) | Tylko przy zmianie |
| defrost | Cykl odszraniania parownika | Tylko przy zmianie |
| pump_sta | Status pompy obiegowej wody | Tylko przy zmianie |
| fault_flag | Flaga awarii | Tylko przy zmianie |
| freeze | Ochrona antyzamrozeniowa | Tylko przy zmianie |
| protect_flag | Flaga ochrony urzadzenia | Tylko przy zmianie |
| switch | Glowny wylacznik pompy | Tylko przy zmianie |
| mute | Tryb cichy (Silent) | Tylko przy zmianie |
| holiday_sw | Tryb urlopowy (Holiday) | Tylko przy zmianie |

### Tryby pracy i strefy
| Kod | Opis | Wartosci |
|-----|------|---------|
| work_mode | Tryb pracy pompy | cool, heat, auto, hot_water, cool_hot_water, heat_hot_water, auto_dhw |
| zone_select | Aktywna strefa grzewcza | 0=brak, 1=Z1, 2=Z2, 3=obie (val_str, wymaga konwersji) |
| auto_run_tar_mode | Co pompa robi w trybie auto (read-only) | 0=chlodzenie, 1=ogrzewanie |

### Kody bledow (fault)
- Bitmapa 30 bitow: E01-E16 (bit 0-15), P01-P14 (bit 16-29)
- 0 = brak bledow
- Funkcja decode_fault_bitmap() w analytics.py parsuje na kody

### Konfiguracja DeadbandFilter
- MAX_HEARTBEAT_SEC = 300 (wymuszony zapis co 5 min nawet bez zmiany)
- NO_HEARTBEAT_CODES: ac_fan, dc_fan2, defrost, fault_flag, freeze, protect_flag, pump_sta, valve
  (te kody zapisywane TYLKO przy zmianie wartosci, bez heartbeatu co 300s)
- Histereza "active" stosowana gdy comp_freq > 5 (sprezarka pracuje)
- Histereza "idle" stosowana gdy comp_freq <= 5 (postoj)
- Idle histereza jest wieksza = mniej zapisow w standby = mniejsza baza

### WAZNE odkrycia dot. parametrow
- ac_curr mierzy TYLKO sprezarke (pompa obiegowa, wentylator, elektronika = osobny obwod)
- valve jest val_str ("True"/"False"), NIE val_num — wymaga konwersji BOOL_MAP
- zone_select jest val_str — wymaga konwersji na float
- Temperatury w bazie JUZ przeliczone (collector dzieli przez 10) — NIE dzielic ponownie!
- Historyczne heat_temp_set_z2/idr_temp_set moga miec surowe wartosci (350 zamiast 35.0)
- pump_sta zmienia sie ~120s PO wylaczeniu sprezarki (pompa obiegowa pracuje dluzej)

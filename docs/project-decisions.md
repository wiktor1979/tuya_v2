# !!! ZASADY WSPÓŁPRACY (obowiązują zawsze, także po kompaktowaniu kontekstu)

1. DEPLOY: każdy `fly deploy` wymaga JASNEJ, WYRAZNEJ zgody użytkownika ZA KAŻDYM RAZEM.
   Poprawki/redeploye po nieudanym deployu NIE są kontynuacją poprzedniej zgody. Bez "tak, deployuj" — nie deployować.
2. WYBÓR OPCJI: gdy przedstawiam warianty (A/B/...), ZATRZYMUJĘ SIĘ i czekam na decyzję użytkownika.
   NIE implementuję żadnej opcji z automatu, nawet jeśli którąś rekomenduję. Najpierw wybór użytkownika, potem implementacja.

---


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

## Strefa czasowa (naprawione 2026-09-01)
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
- UWAGA: offset jest STALY (nie DST-aware). Przy zmianie czasu na zimowy ustawic "1" w fly.toml/.env.

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

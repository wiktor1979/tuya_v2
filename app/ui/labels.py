"""Centralne definicje etykiet, hintów i tekstów UI.

Jedno źródło prawdy dla wszystkich napisów w dashboardzie.
Ułatwia: edycję, spójność, ewentualne tłumaczenia.
"""

# --- Metryki energetyczne ---
METRICS: dict[str, dict[str, str]] = {
    "cop_instant": {
        "label": "COP chwilowy",
        "help": (
            "Chwilowa efektywność: ciepło / prąd w tej chwili.\n\n"
            "Zmienia się co sekundę. '—' gdy pompa stoi."
        ),
    },
    "scop_range": {
        "label": "SCOP {range}",
        "help": (
            "Całkowity SCOP systemu za wybrany okres (realny).\n\n"
            "(ciepło CO+CWU − straty defrostu) / (prąd CO+CWU+standby).\n\n"
            "SCOP ≥ 3.1 = opłacalniejsza niż gaz.\n\n"
            "Liczony z surowych danych — ta sama wartość niezależnie od wykresu."
        ),
    },
    "scop_nominal": {
        "label": "SCOP nominalny",
        "help": (
            "SCOP bez uwzględnienia strat defrostu (teoretyczny).\n\n"
            "(ciepło CO+CWU) / (prąd CO+CWU+standby).\n\n"
            "Zawsze ≥ SCOP realny. Różnica = koszt odszraniania."
        ),
    },
    "scop_real": {
        "label": "SCOP realny",
        "help": (
            "Całkowity SCOP systemu z uwzględnieniem strat defrostu.\n\n"
            "(ciepło CO+CWU − straty defrostu) / (prąd CO+CWU+standby).\n\n"
            "To jest prawdziwa efektywność pompy.\n\n"
            "Próg opłacalności vs gaz = 3.1."
        ),
    },
    "scop_co": {
        "label": "SCOP CO",
        "help": (
            "SCOP tylko dla ogrzewania (CO), realny.\n\n"
            "(ciepło CO − straty defrostu) / prąd CO.\n\n"
            "Defrost obciąża wyłącznie CO (odszranianie dotyczy grzania)."
        ),
    },
    "scop_co_empty": {
        "label": "SCOP CO",
        "help": (
            "Za mało danych CO w tym okresie (<1 kWh ciepła).\n\n"
            "Latem pompa pracuje głównie na CWU."
        ),
    },
    "scop_cwu": {
        "label": "SCOP CWU",
        "help": (
            "SCOP tylko dla ciepłej wody użytkowej (CWU).\n\n"
            "ciepło CWU / prąd CWU (defrost nie dotyczy CWU)."
        ),
    },
    "scop_cwu_empty": {
        "label": "SCOP CWU",
        "help": "Za mało danych CWU w tym okresie (<1 kWh ciepła).",
    },
    "e_el": {
        "label": "⚡ Prąd pobrany",
        "help": "Całkowita energia elektryczna:\n\npraca sprężarki + standby.",
    },
    "e_el_short": {
        "label": "Energia",
        "help": (
            "Prąd zużyty przez pompę w wybranym okresie.\n\n"
            "Sprężarka + standby."
        ),
    },
    "e_th": {
        "label": "🔥 Ciepło oddane",
        "help": (
            "Ciepło oddane do instalacji CO + CWU.\n\n"
            "Im więcej ciepła na kWh prądu, tym wyższy SCOP."
        ),
    },
    "e_th_short": {
        "label": "Ciepło",
        "help": "Ciepło oddane do instalacji CO i CWU\n\nw wybranym okresie.",
    },
    "e_th_defrost": {
        "label": "❄️ Strata defrostu",
        "help": (
            "Ciepło odebrane z instalacji podczas odszraniania parownika.\n\n"
            "Wartość ujemna = strata.\n\n"
            "Latem = 0, zimą 1-5% ciepła."
        ),
    },
    "comp_starts": {
        "label": "Starty sprężarki",
        "help": (
            "Ile razy sprężarka się włączyła.\n\n"
            ">15/dobę = taktowanie (za częste starty)."
        ),
    },
    "comp_hours": {
        "label": "Czas pracy",
        "help": (
            "Łączny czas pracy sprężarki.\n\n"
            "Latem 1-2h/dobę (CWU), zimą 12-20h/dobę (CO)."
        ),
    },
    "defrost_count": {
        "label": "Defrosty",
        "help": (
            "Liczba cykli odszraniania parownika.\n\n"
            "Normalnie 0-10/dobę zimą. Latem = 0."
        ),
    },
    "amb_temp_avg": {
        "label": "Śr. temp. zewn.",
        "help": (
            "Średnia temperatura zewnętrzna z czujnika pompy.\n\n"
            "Wpływa na COP i HDD."
        ),
    },
    "hdd_total": {
        "label": "Σ HDD",
        "help": (
            "Heating Degree Days — miara zapotrzebowania na ogrzewanie.\n\n"
            "Baza 15°C. Im wyższe HDD, tym więcej ciepła potrzebne."
        ),
    },
    "scop_total_period": {
        "label": "SCOP",
        "help": (
            "Całkowity SCOP systemu za okres (realny).\n\n"
            "(ciepło CO+CWU − straty defrostu) / (prąd CO+CWU+standby)."
        ),
    },
    "cost_total": {
        "label": "Σ Koszt",
        "help": "Koszt prądu przy podanej cenie za kWh.",
    },
}


def e_el_help_with_standby(e_el_standby: float, e_el_total: float) -> str:
    """Generuje hint dla E_el z informacją o udziale standby."""
    if e_el_total > 0 and e_el_standby > 0:
        pct = e_el_standby / e_el_total * 100
        return (
            f"Praca sprężarki + standby.\n\n"
            f"W tym standby: {e_el_standby:.2f} kWh ({pct:.0f}% total)."
        )
    return METRICS["e_el"]["help"]


# --- Delty SCOP ---

def scop_delta(scop_real: float) -> tuple[str | None, str]:
    """Zwraca (delta_text, delta_color) dla metryki SCOP."""
    if scop_real >= 3.1:
        return "✓ > 3.1", "normal"
    elif scop_real > 0:
        return "✗ < 3.1", "inverse"
    return None, "off"

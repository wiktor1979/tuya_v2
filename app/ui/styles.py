"""Style CSS, kolory statusu pompy, PARAM_INFO."""
import streamlit as st

from app.config import PARAM_INFO

# Re-export
def get_param_label(code: str) -> str:
    """Zwraca etykietę parametru z kodem w nawiasie."""
    info = PARAM_INFO.get(code)
    return f"{info['label']} ({code})" if info else code


# Kolory statusu pompy
STATUS_COLORS = {
    "co": "#2196F3",
    "cwu": "#E67E22",
    "idle": "#555555",
    "defrost": "#00BCD4",
    "fault": "#e94560",
}

# Plotly dark theme defaults
PLOTLY_DEFAULTS = {
    "template": "plotly_dark",
    "legend": dict(orientation="h", yanchor="bottom", y=-0.3),
}


def inject_css() -> None:
    """Wstrzykuje CSS: kompaktowy header, responsywne metryki."""
    st.markdown("""
    <style>
    /* Kompaktowy header — z marginesem na badge */
    header[data-testid="stHeader"] { height: 2.5rem; }
    .block-container { padding-top: 3.5rem !important; }

    /* Status bar */
    .pump-status {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.3rem 1rem;
        border-radius: 20px;
        font-size: 0.9rem;
        font-weight: 700;
        border-width: 2px;
        border-style: solid;
    }

    /* Temperature bars */
    .temp-bar-container {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.3rem 0;
    }
    .temp-bar-label {
        font-size: 0.8rem;
        color: #aaa;
        width: 55px;
        flex-shrink: 0;
    }
    .temp-bar {
        height: 26px;
        border-radius: 4px;
        display: flex;
        align-items: center;
        padding-left: 10px;
        font-size: 0.8rem;
        color: #fff;
        font-weight: 600;
        transition: width 0.3s ease;
    }
    .temp-bar-co { background: linear-gradient(90deg, #2196F3, #1565C0); }
    .temp-bar-cwu { background: linear-gradient(90deg, #E67E22, #E65100); }
    .temp-bar-set { opacity: 0.5; }

    /* SCOP box */
    .scop-box {
        background: #16213e;
        border-radius: 8px;
        padding: 0.8rem;
    }
    .scop-row {
        display: flex;
        justify-content: space-between;
        padding: 0.25rem 0;
        font-size: 0.95rem;
    }
    .scop-label { color: #aaa; }
    .scop-value { font-weight: 700; color: #e0e0e0; }
    .scop-total {
        border-top: 1px solid #333;
        padding-top: 0.4rem;
        margin-top: 0.2rem;
    }

    /* Metryki — czytelne etykiety */
    [data-testid="stMetric"] {
        background: #16213e;
        border-radius: 8px;
        padding: 0.6rem;
        text-align: center;
    }
    [data-testid="stMetric"] label,
    [data-testid="stMetric"] label p,
    [data-testid="stMetric"] label div,
    [data-testid="stMetric"] [data-testid="stMetricLabel"] {
        color: #ccc !important;
        opacity: 1 !important;
    }
    [data-testid="stMetric"] [data-testid="stMetricValue"],
    [data-testid="stMetric"] [data-testid="stMetricValue"] div {
        color: #fff !important;
        font-size: 1.8rem !important;
    }
    /* Help icon (?) widoczna na ciemnym tle */
    [data-testid="stMetric"] [data-testid="stTooltipHoverTarget"] {
        filter: brightness(2);
    }
    </style>
    """, unsafe_allow_html=True)


def render_status_badge(label: str, color: str, emoji: str) -> None:
    """Renderuje badge statusu pompy (kolorowy chip)."""
    st.markdown(
        f'<span class="pump-status" style="background: {color}22; color: {color}; border: 1px solid {color};">'
        f"{emoji} {label}</span>",
        unsafe_allow_html=True,
    )


def render_temp_bar(
    label: str,
    value: float | None,
    bar_class: str = "temp-bar-co",
    max_temp: float = 55.0,
    min_temp: float = 15.0,
    is_setpoint: bool = False,
) -> None:
    """Renderuje pasek temperatury (label + kolorowy bar z wartością).
    
    Szerokość proporcjonalna do zakresu [min_temp, max_temp].
    """
    if value is None:
        st.markdown(
            f'<div class="temp-bar-container">'
            f'<span class="temp-bar-label">{label}</span>'
            f'<span style="color:#666;">N/A</span></div>',
            unsafe_allow_html=True,
        )
        return

    # Proporcja w zakresie [min, max], zaklampowana do [15%, 95%]
    ratio = (value - min_temp) / (max_temp - min_temp) if max_temp > min_temp else 0.5
    width_pct = min(95, max(15, ratio * 100))
    extra_class = " temp-bar-set" if is_setpoint else ""
    st.markdown(
        f'<div class="temp-bar-container">'
        f'<span class="temp-bar-label">{label}</span>'
        f'<div class="temp-bar {bar_class}{extra_class}" style="width: {width_pct}%;">'
        f'{value:.1f} °C</div></div>',
        unsafe_allow_html=True,
    )


def render_temp_bar_setpoint(
    label: str,
    value: float | None,
    setpoint: float | None,
    bar_class: str = "temp-bar-co",
    max_temp: float = 55.0,
    min_temp: float = 15.0,
) -> None:
    """Jeden pasek: wypełnienie = wartość aktualna, pionowa kreska = nastawa (cel).

    Zastępuje dwa osobne paski (wartość + nastawa). Kreska pokazuje do jakiej
    temperatury pompa dąży, wypełnienie — gdzie jest teraz.
    """
    if value is None:
        st.markdown(
            f'<div class="temp-bar-container">'
            f'<span class="temp-bar-label">{label}</span>'
            f'<span style="color:#666;">N/A</span></div>',
            unsafe_allow_html=True,
        )
        return

    span = (max_temp - min_temp) if max_temp > min_temp else 1.0
    ratio = (value - min_temp) / span
    width_pct = min(95, max(6, ratio * 100))

    # Marker nastawy (pozycja w % szerokości toru)
    marker_html = ""
    set_txt = ""
    if setpoint is not None:
        set_ratio = (setpoint - min_temp) / span
        set_pct = min(99, max(1, set_ratio * 100))
        marker_html = (
            f'<div style="position:absolute;left:{set_pct}%;top:-2px;bottom:-2px;'
            f'width:3px;background:#fff;box-shadow:0 0 3px rgba(0,0,0,0.6);" '
            f'title="Nastawa {setpoint:.1f} °C"></div>'
        )
        set_txt = f'<span style="color:#aaa;font-size:0.75rem;margin-left:0.4rem;">🎯 {setpoint:.1f}°C</span>'

    st.markdown(
        f'<div class="temp-bar-container">'
        f'<span class="temp-bar-label">{label}</span>'
        f'<div style="position:relative;flex:1;background:#0f1730;border-radius:4px;height:26px;">'
        f'<div class="temp-bar {bar_class}" style="width:{width_pct}%;height:26px;">'
        f'{value:.1f} °C</div>'
        f'{marker_html}'
        f'</div>'
        f'{set_txt}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_scop_box(scop_co: float, scop_cwu: float, scop_total: float, label: str = "SCOP") -> None:
    """Renderuje box SCOP: Total duży na górze z oznaczeniem opłacalności (próg 3.1),
    pod spodem rozbicie CO / CWU."""
    co_color = STATUS_COLORS["co"]
    cwu_color = STATUS_COLORS["cwu"]

    def fmt(v: float) -> str:
        return f"{v:.2f}" if v > 0 else "—"

    no_data = scop_co <= 0 and scop_cwu <= 0 and scop_total <= 0

    # Kolor i status Total wg progu opłacalności 3.1
    if scop_total <= 0:
        total_color = "#888"
        status_txt = ""
    elif scop_total >= 3.1:
        total_color = "#2ECC71"
        status_txt = '<span style="color:#2ECC71;font-size:0.85rem;font-weight:600;">✓ Opłacalny (≥ 3.1)</span>'
    else:
        total_color = "#e94560"
        status_txt = '<span style="color:#e94560;font-size:0.85rem;font-weight:600;">✗ Poniżej progu 3.1</span>'

    hint = '<div style="font-size:0.75rem;color:#666;margin-top:0.4rem;text-align:center;">Pompa nie pracowała w wybranym zakresie</div>' if no_data else ""

    st.markdown(f"""
    <div class="scop-box">
        <div style="font-size: 0.8rem; color: #aaa; text-align:center;">{label}</div>
        <div style="text-align:center;line-height:1.1;margin:0.2rem 0 0.1rem 0;">
            <span style="font-size:2.2rem;font-weight:800;color:{total_color};">{fmt(scop_total)}</span>
        </div>
        <div style="text-align:center;margin-bottom:0.5rem;">{status_txt}</div>
        <div style="display:flex;justify-content:space-around;border-top:1px solid #333;padding-top:0.5rem;">
            <div style="text-align:center;">
                <div style="font-size:0.75rem;color:#aaa;">🏠 CO</div>
                <div style="font-size:1.2rem;font-weight:700;color:{co_color};">{fmt(scop_co)}</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:0.75rem;color:#aaa;">🚿 CWU</div>
                <div style="font-size:1.2rem;font-weight:700;color:{cwu_color};">{fmt(scop_cwu)}</div>
            </div>
        </div>
        {hint}
    </div>
    """, unsafe_allow_html=True)



def render_about() -> None:
    """Wspólna sekcja About / Help w sidebarze — jednakowa na wszystkich stronach.

    Umieszczać wewnątrz `with st.sidebar:` na każdej stronie.
    """
    with st.expander("ℹ️ About / Help"):
        st.markdown("""
        **Tuya Heat Pump Monitor v2**

        Silnik obliczeniowy: `compute_energy()`
        - Energia z surowych danych (bez resample)
        - SCOP liczony jedną funkcją `compute_scop()` (realny, z odliczeniem defrostu)
        - Ta sama wartość na każdej stronie
        """)
        st.page_link("pages/5_Wiedza.py", label="📚 Baza Wiedzy")

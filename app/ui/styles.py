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
        width: 110px;
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
    .scop-value { font-weight: 700; }
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


def render_scop_box(scop_co: float, scop_cwu: float, scop_total: float, label: str = "SCOP") -> None:
    """Renderuje box z rozbiciem SCOP na CO/CWU/Total."""
    co_color = STATUS_COLORS["co"]
    cwu_color = STATUS_COLORS["cwu"]

    def fmt(v: float) -> str:
        return f"{v:.2f}" if v > 0 else "—"

    no_data = scop_co <= 0 and scop_cwu <= 0 and scop_total <= 0
    hint = '<div style="font-size:0.75rem;color:#666;margin-top:0.4rem;">Pompa nie pracowała w wybranym zakresie</div>' if no_data else ""

    st.markdown(f"""
    <div class="scop-box">
        <div style="font-size: 0.8rem; color: #aaa; margin-bottom: 0.3rem;">{label}</div>
        <div class="scop-row">
            <span class="scop-label">🏠 CO</span>
            <span class="scop-value" style="color: {co_color};">{fmt(scop_co)}</span>
        </div>
        <div class="scop-row">
            <span class="scop-label">🚿 CWU</span>
            <span class="scop-value" style="color: {cwu_color};">{fmt(scop_cwu)}</span>
        </div>
        <div class="scop-row scop-total">
            <span class="scop-label"><strong>Σ Total</strong></span>
            <span class="scop-value">{fmt(scop_total)}</span>
        </div>
        {hint}
    </div>
    """, unsafe_allow_html=True)

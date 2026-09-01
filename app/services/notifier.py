"""Powiadomienia Telegram — alerty krytyczne i raport dzienny."""
import time
import threading
import requests
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

from app.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED,
    HEAT_PUMP_DEV_ID, DB_FILE,
    DEFAULT_COS_PHI, DEFAULT_STANDBY_POWER_W, DEFAULT_ACTIVE_POWER_W,
    DEFAULT_HIDDEN_POWER_W, DEFAULT_SENSOR_FACTOR,
)
from app.services.analytics import decode_fault_bitmap


# --- Throttle: zapobiega spamowaniu identycznymi alertami ---
_last_alert_time: Dict[str, float] = {}
ALERT_COOLDOWN_SEC = 600  # min 10 minut między identycznymi alertami


def send_telegram(message: str, parse_mode: str = "Markdown") -> bool:
    """
    Wysyła wiadomość na Telegram przez Bot API.
    
    Returns:
        True jeśli wysłano pomyślnie, False w razie błędu.
    """
    if not TELEGRAM_ENABLED:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            print(f"[Telegram] Błąd HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            return False
    except requests.exceptions.RequestException as e:
        print(f"[Telegram] Błąd połączenia: {e}", flush=True)
        return False


def _should_send_alert(alert_key: str) -> bool:
    """Sprawdza czy alert nie jest throttlowany (cooldown 10 min)."""
    now = time.time()
    last = _last_alert_time.get(alert_key, 0)
    if now - last < ALERT_COOLDOWN_SEC:
        return False
    _last_alert_time[alert_key] = now
    return True


# --- Alerty krytyczne (natychmiast) ---

def send_fault_alert(device_id: str, fault_codes: List[str], fault_bitmap: int) -> bool:
    """Wysyła natychmiastowy alert o awarii pompy."""
    alert_key = f"fault:{device_id}:{fault_bitmap}"
    if not _should_send_alert(alert_key):
        return False

    codes_str = ", ".join(fault_codes)
    msg = (
        f"🚨 *AWARIA POMPY CIEPŁA*\n"
        f"Urządzenie: `{device_id}`\n"
        f"Kody błędów: *{codes_str}*\n"
        f"Bitmapa: {fault_bitmap}\n"
        f"Czas: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    sent = send_telegram(msg)
    if sent:
        print(f"[Telegram] Wyslano alert awarii: {codes_str} ({device_id})", flush=True)
    return sent


def send_fault_resolved(device_id: str, resolved_codes: List[str]) -> bool:
    """Wysyła informację o rozwiązaniu awarii."""
    alert_key = f"resolved:{device_id}:{','.join(resolved_codes)}"
    if not _should_send_alert(alert_key):
        return False

    codes_str = ", ".join(resolved_codes)
    msg = (
        f"✅ *Awaria rozwiązana*\n"
        f"Urządzenie: `{device_id}`\n"
        f"Rozwiązane kody: *{codes_str}*\n"
        f"Czas: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    sent = send_telegram(msg)
    if sent:
        print(f"[Telegram] Wyslano info o rozwiazaniu: {codes_str} ({device_id})", flush=True)
    return sent


def send_communication_lost(device_id: str, minutes_silent: int) -> bool:
    """Wysyła alert o utracie komunikacji z pompą."""
    alert_key = f"comm_lost:{device_id}"
    if not _should_send_alert(alert_key):
        return False

    msg = (
        f"📡 *Utrata komunikacji*\n"
        f"Urządzenie: `{device_id}`\n"
        f"Brak danych od: *{minutes_silent} min*\n"
        f"Czas: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    sent = send_telegram(msg)
    if sent:
        print(f"[Telegram] Wyslano alert utraty komunikacji: {minutes_silent} min ({device_id})", flush=True)
    return sent


# --- Raport dzienny (ważne + informacyjne) ---

def build_daily_report(device_id: str) -> Optional[str]:
    """
    Buduje raport dzienny na podstawie danych z bazy.
    Używa compute_energy() — tego samego silnika co dashboard.
    
    Returns:
        Tekst raportu Markdown lub None jeśli brak danych.
    """
    from app.services.database import db_cursor, get_fault_history, get_setting
    from app.core.energy import compute_energy
    from app.config import SERVER_TIMEZONE_OFFSET
    import pandas as pd
    import sqlite3

    # Kalibracja — te same wartości co sidebar (persystowane w settings)
    cos_phi = float(get_setting("cos_phi", str(DEFAULT_COS_PHI)))
    standby_power_w = float(get_setting("standby_power_w", str(DEFAULT_STANDBY_POWER_W)))
    active_power_w = float(get_setting("active_power_w", str(DEFAULT_ACTIVE_POWER_W)))
    hidden_power_w = float(get_setting("hidden_power_w", str(DEFAULT_HIDDEN_POWER_W)))
    sensor_factor = float(get_setting("sensor_factor", str(DEFAULT_SENSOR_FACTOR)))

    # Czas lokalny = UTC + SERVER_TIMEZONE_OFFSET (offset strefy lokalnej vs UTC, np. +2 dla CEST).
    # Liczymy z UTC, żeby wynik nie zależał od strefy procesu na serwerze.
    now_local = datetime.now(timezone.utc) + timedelta(hours=SERVER_TIMEZONE_OFFSET)
    today = now_local.date()
    yesterday = today - timedelta(days=1)

    # Użyj compute_energy() — jedno źródło prawdy
    result = compute_energy(
        date_from=yesterday.isoformat(),
        date_to=today.isoformat(),
        cos_phi=cos_phi,
        standby_power_w=standby_power_w,
        active_power_w=active_power_w,
        hidden_power_w=hidden_power_w,
        sensor_factor=sensor_factor,
    )

    if result.e_el_total <= 0:
        return None

    scop = result.scop_real
    e_el = result.e_el_total
    comp_starts = result.comp_starts
    hours_work = result.comp_hours
    defrost_count = result.defrost_count

    # Zakres timestamp (epoch UTC) dla fault_history.
    # Północ lokalna dnia D w epoch UTC = epoch(D 00:00 UTC) - offset*3600
    # (bo czas lokalny = UTC + offset  =>  UTC = lokalny - offset).
    offset_sec = SERVER_TIMEZONE_OFFSET * 3600
    ts_start = int(datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc).timestamp()) - offset_sec
    ts_end = int(datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp()) - offset_sec

    # Awarie z fault_log za wczoraj
    with db_cursor() as cursor:
        pass
    fault_history = get_fault_history(device_id, limit=100)
    faults_yesterday = []
    for _, ts, code, bitmap, resolved, resolved_at in fault_history:
        if ts_start <= ts < ts_end:
            status = "rozwiazana" if resolved else "AKTYWNA"
            faults_yesterday.append(f"{code} ({status})")

    # --- Budowanie raportu ---
    date_str = yesterday.strftime("%Y-%m-%d")
    lines = [f"📊 *Raport dzienny — {date_str}*", f"Urządzenie: `{device_id}`", ""]

    # SCOP dzienny
    if scop is not None and scop > 0.5:
        scop_icon = "✅" if scop >= 3.1 else "⚠️"
        lines.append(f"{scop_icon} SCOP dzienny: *{scop:.2f}*")
    else:
        lines.append("SCOP dzienny: _brak danych_")

    # Zużycie energii
    if e_el > 0:
        lines.append(f"⚡ Zużycie energii: *{e_el:.2f} kWh*")
    else:
        lines.append("⚡ Zużycie energii: _brak danych_")

    # Czas pracy sprężarki
    lines.append(f"⏱ Czas pracy: *{hours_work:.1f} h*")

    # Starty sprężarki
    takt_icon = "⚠️" if comp_starts > 12 else ""
    lines.append(f"🔄 Starty: *{comp_starts}* {takt_icon}")

    # Defrosty
    lines.append(f"❄️ Defrosty: *{defrost_count}*")

    # Awarie
    if faults_yesterday:
        lines.append("")
        for fault_str in faults_yesterday:
            lines.append(f"🚨 {fault_str}")
    else:
        lines.append("✅ Brak awarii")

    return "\n".join(lines)


def send_daily_report(device_id: str) -> bool:
    """Generuje i wysyła raport dzienny dla urządzenia."""
    report = build_daily_report(device_id)
    if report is None:
        print(f"[Telegram] Brak danych do raportu dziennego ({device_id})", flush=True)
        return False

    sent = send_telegram(report)
    if sent:
        print(f"[Telegram] Wyslano raport dzienny ({device_id})", flush=True)
    return sent

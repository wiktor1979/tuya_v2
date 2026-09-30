"""Weryfikacja danych za 2026-09-10 — rozjazd model vs licznik.

Używa kanonicznego compute_energy() (jedyne źródło prawdy) + surowe zapytania
do bazy do rozłożenia rozjazdu na czynniki. Skrypt tymczasowy (do usunięcia).
"""
import sqlite3
from datetime import datetime, timezone

from app.config import (
    DB_FILE, HEAT_PUMP_DEV_ID, ENERGY_METER_DEV_ID, SERVER_TIMEZONE_OFFSET,
)
from app.core.energy import compute_energy
from app.services.database import load_calibration, get_remote_meter_energy

DATE = "2026-09-10"
DATE_NEXT = "2026-09-11"

offset = SERVER_TIMEZONE_OFFSET
offset_sec = offset * 3600
print(f"SERVER_TIMEZONE_OFFSET = {offset}h")

# Zakres epoch UTC dnia lokalnego (jak w notifier.py)
d = datetime(2026, 9, 10, tzinfo=timezone.utc)
dn = datetime(2026, 9, 11, tzinfo=timezone.utc)
ts_start = int(d.timestamp()) - offset_sec
ts_end = int(dn.timestamp()) - offset_sec
print(f"ts_start={ts_start} ts_end={ts_end} (okno {(ts_end-ts_start)/3600:.1f}h)")

cal = load_calibration()
print("\n--- KALIBRACJA (tabela settings) ---")
for k, v in cal.items():
    print(f"  {k} = {v}")

# --- compute_energy: pelny model (z kalibracja) ---
r = compute_energy(
    date_from=DATE, date_to=DATE_NEXT,
    cos_phi=cal["cos_phi"],
    standby_power_w=cal["standby_power_w"],
    active_power_w=cal["active_power_w"],
    hidden_power_w=cal["hidden_power_w"],
    sensor_factor=cal["sensor_factor"],
)
print("\n--- MODEL (compute_energy z kalibracja) ---")
print(f"  e_el_total = {r.e_el_total:.3f} kWh")
print(f"    e_el_co      = {r.e_el_co:.3f}")
print(f"    e_el_cwu     = {r.e_el_cwu:.3f}")
print(f"    e_el_standby = {r.e_el_standby:.3f}")
print(f"  e_th_total       = {r.e_th_total:.3f} kWh")
print(f"  e_th_defrost     = {r.e_th_defrost:.3f} kWh")
print(f"  scop_real    = {r.scop_real:.3f}")
print(f"  scop_nominal = {r.scop_nominal:.3f}")
print(f"  comp_starts  = {r.comp_starts}")
print(f"  comp_hours   = {r.comp_hours:.3f}")
print(f"  defrost_count= {r.defrost_count}")
print(f"  amb_temp_avg = {r.amb_temp_avg:.2f}")
print(f"  sample_count = {r.sample_count}")
print(f"  gaps_skipped = {r.gaps_skipped}")

# --- compute_energy: SUROWA energia (bez kalibracji) ---
r_raw = compute_energy(
    date_from=DATE, date_to=DATE_NEXT,
    cos_phi=cal["cos_phi"],
    standby_power_w=cal["standby_power_w"],
    active_power_w=cal["active_power_w"],
    hidden_power_w=0.0,
    sensor_factor=1.0,
)
print("\n--- MODEL SUROWY (hidden=0, factor=1.0) ---")
print(f"  e_el_total (surowy z czujnika) = {r_raw.e_el_total:.3f} kWh")
print(f"  wklad hidden_power_w = {cal['hidden_power_w']} W * {r.comp_hours:.1f}h??")
# hidden liczy sie od TOTAL hours (24h), nie comp_hours
hidden_contrib = cal["hidden_power_w"] * 24.0 / 1000.0
factor_effect = r_raw.e_el_total * (cal["sensor_factor"] - 1.0)
print(f"  wklad hidden (24h) ~= {hidden_contrib:.3f} kWh")
print(f"  efekt sensor_factor ~= {factor_effect:+.3f} kWh")

# --- LICZNIK ---
meter = get_remote_meter_energy(ts_start, ts_end, ENERGY_METER_DEV_ID)
print("\n--- LICZNIK (add_ele) ---")
print(f"  get_remote_meter_energy = {meter} kWh")

conn = sqlite3.connect(DB_FILE)
row = conn.execute("""
    SELECT COUNT(*), MIN(val_num), MAX(val_num), SUM(val_num),
           MIN(timestamp), MAX(timestamp)
    FROM telemetry
    WHERE device_id=? AND code='add_ele' AND timestamp>=? AND timestamp<=?
""", (ENERGY_METER_DEV_ID, ts_start, ts_end)).fetchone()
print(f"  add_ele: count={row[0]} min={row[1]} max={row[2]} sum={row[3]}")
if row[4]:
    print(f"  pierwszy raport: {datetime.utcfromtimestamp(row[4]+offset_sec)} (lok)")
    print(f"  ostatni raport:  {datetime.utcfromtimestamp(row[5]+offset_sec)} (lok)")

# Pokrycie telemetrii pompy — czy sa dziury
prow = conn.execute("""
    SELECT COUNT(DISTINCT timestamp), MIN(timestamp), MAX(timestamp)
    FROM telemetry
    WHERE device_id=? AND code='comp_freq' AND timestamp>=? AND timestamp<=?
""", (HEAT_PUMP_DEV_ID, ts_start, ts_end)).fetchone()
print("\n--- POKRYCIE TELEMETRII POMPY (comp_freq) ---")
print(f"  distinct ts = {prow[0]}")
if prow[1]:
    print(f"  zakres: {datetime.utcfromtimestamp(prow[1]+offset_sec)} -> {datetime.utcfromtimestamp(prow[2]+offset_sec)} (lok)")
    span_h = (prow[2]-prow[1])/3600
    print(f"  rozpietosc danych: {span_h:.2f}h (z 24h okna)")

# Najwieksze dziury w comp_freq
ts_list = [t[0] for t in conn.execute("""
    SELECT DISTINCT timestamp FROM telemetry
    WHERE device_id=? AND code='comp_freq' AND timestamp>=? AND timestamp<=?
    ORDER BY timestamp
""", (HEAT_PUMP_DEV_ID, ts_start, ts_end)).fetchall()]
gaps = []
for i in range(1, len(ts_list)):
    dt = ts_list[i] - ts_list[i-1]
    if dt > 360:
        gaps.append((ts_list[i-1], dt))
print(f"  dziury > 360s: {len(gaps)}")
for t0, dt in sorted(gaps, key=lambda x: -x[1])[:8]:
    print(f"    o {datetime.utcfromtimestamp(t0+offset_sec)} przerwa {dt/60:.1f} min")
tot_gap = sum(dt for _, dt in gaps)
print(f"  suma przerw: {tot_gap/3600:.2f}h")

conn.close()

print("\n=== PODSUMOWANIE ROZJAZDU (single-day compute) ===")
if meter:
    diff = r.e_el_total - meter
    print(f"  model={r.e_el_total:.2f}  licznik={meter:.2f}  roznica={diff:+.2f} kWh ({diff/meter*100:+.1f}%)")

# --- REPRODUKCJA STRONY BILANS: daily_breakdown na zakresie 7 dni ---
print("\n\n########## REPRODUKCJA BILANS (daily_breakdown, zakres 7 dni) ##########")
r_daily = compute_energy(
    date_from="2026-09-04", date_to=None,  # 7 dni wstecz od teraz, do teraz (jak strona)
    daily_breakdown=True,
    cos_phi=cal["cos_phi"],
    standby_power_w=cal["standby_power_w"],
    active_power_w=cal["active_power_w"],
    hidden_power_w=cal["hidden_power_w"],
    sensor_factor=cal["sensor_factor"],
)
if r_daily.daily is not None and not r_daily.daily.empty:
    d = r_daily.daily.copy()
    d["date_str"] = d["date"].astype(str)
    row10 = d[d["date_str"] == "2026-09-10"]
    print("Wiersz 2026-09-10 z daily_breakdown:")
    if not row10.empty:
        rr = row10.iloc[0]
        for c in d.columns:
            if c == "date_str":
                continue
            print(f"  {c} = {rr[c]}")
    else:
        print("  BRAK wiersza 2026-09-10 w daily!")
        print("  Dostepne daty:", list(d["date_str"]))
else:
    print("daily puste")

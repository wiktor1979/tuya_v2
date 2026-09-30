"""Przerzedzenie historycznych danych temperatur w tabeli telemetry.

Usuwa NADMIAROWE rekordy temperatur, ktore naprawiony DeadbandFilter i tak by
odrzucil (bug histerezy per-code przy 2 pompach - patrz decyzje 2026-09-27).
Stosuje IDENTYCZNA logike filtra per (device_id, code): pierwszy zapis / heartbeat
co MAX_HEARTBEAT_SEC / zmiana >= prog (idle). Reszta = do usuniecia.

Dotyka WYLACZNIE kodow temperatur - nie rusza comp_freq/flow/energii/flag/licznika.

Uzycie:
    python cleanup_telemetry.py            # DRY-RUN: tylko raport, nic nie usuwa
    python cleanup_telemetry.py --apply    # realne usuniecie + VACUUM
    python cleanup_telemetry.py --db PATH  # wskaz baze (domyslnie z DB_FILE/config)

Bezpieczenstwo:
    - DRY-RUN domyslnie. --apply wymagane do zmian.
    - Przy --apply skrypt NAJPIERW liczy SCOP all-time (przed), usuwa, VACUUM,
      liczy SCOP (po) i porownuje - ostrzega, jesli roznica > 1%.
    - ZROB KOPIE bazy przed --apply (skrypt przypomina; na Fly.io patrz instrukcja).
"""
import argparse
import sqlite3
import sys
import time

# Progi histerezy i heartbeat - importujemy z configu (jedno zrodlo prawdy).
try:
    from app.config import HISTERESIS_CONFIG, MAX_HEARTBEAT_SEC, DB_FILE
except Exception:
    # Fallback, gdyby skrypt uruchamiano bez PYTHONPATH projektu (np. na serwerze).
    MAX_HEARTBEAT_SEC = 300
    DB_FILE = "data/tuya_telemetry.db"
    HISTERESIS_CONFIG = {
        "out_water_temp": {"active": 0.2, "idle": 0.5},
        "in_water_temp":  {"active": 0.2, "idle": 0.5},
        "tank_temp":      {"active": 0.2, "idle": 0.5},
        "amb_temp":       {"active": 0.5, "idle": 0.8},
        "disc_temp":      {"active": 0.5, "idle": 1.5},
        "back_temp":      {"active": 0.5, "idle": 1.5},
    }

# Kody temperatur do przerzedzenia (tylko te - reszta nietknieta).
TEMP_CODES = ("out_water_temp", "in_water_temp", "tank_temp",
              "back_temp", "disc_temp", "amb_temp")


def _ids_to_delete(conn: sqlite3.Connection, dev: str, code: str) -> list:
    """Zwraca liste id rekordow do usuniecia dla (dev, code) wg logiki filtra.

    Zostawia: pierwszy zapis, heartbeat (>= MAX_HEARTBEAT_SEC od ostatnio
    zachowanego), oraz zmiane wartosci >= prog idle. Wartosci NULL zawsze zostaja.
    Heartbeat liczony po CZASIE ZDARZENIA (timestamp z bazy), nie po zegarze.
    """
    cfg = HISTERESIS_CONFIG.get(code, {"idle": 0.0})
    thr = cfg["idle"]
    rows = conn.execute(
        "SELECT id, timestamp, val_num FROM telemetry "
        "WHERE device_id=? AND code=? ORDER BY timestamp, id",
        (dev, code),
    ).fetchall()

    to_del = []
    last_val = None
    last_t = None
    for rid, ts, v in rows:
        if v is None:
            continue  # NULL nie ruszamy
        if last_val is None:
            last_val, last_t = v, ts
            continue
        if (ts - last_t) >= MAX_HEARTBEAT_SEC:
            last_val, last_t = v, ts
            continue
        if v == last_val:
            to_del.append(rid)
            continue
        if abs(v - last_val) < thr:
            to_del.append(rid)
            continue
        last_val, last_t = v, ts
    return to_del


def _scop_all_time(db_path: str):
    """SCOP all-time (real) - do porownania przed/po. None gdy silnik niedostepny."""
    try:
        from app.core.energy import compute_energy
        r = compute_energy(db_file=db_path)
        return r.scop_real, r.e_el_total, r.e_th_total, r.sample_count
    except Exception as e:
        print(f"  (pominieto weryfikacje SCOP: {e})")
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Przerzedzenie nadmiarowych temperatur w telemetry.")
    ap.add_argument("--db", default=DB_FILE, help="Sciezka do bazy SQLite.")
    ap.add_argument("--apply", action="store_true", help="Wykonaj usuniecie (domyslnie dry-run).")
    ap.add_argument("--vacuum", action="store_true", default=True, help="VACUUM po usunieciu (domyslnie tak).")
    ap.add_argument("--no-vacuum", dest="vacuum", action="store_false")
    args = ap.parse_args()

    db_path = args.db
    print(f"Baza: {db_path}")
    print(f"Tryb: {'APPLY (usuwanie)' if args.apply else 'DRY-RUN (tylko raport)'}")

    conn = sqlite3.connect(db_path)
    try:
        total_before = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
        devices = [r[0] for r in conn.execute(
            "SELECT DISTINCT device_id FROM telemetry WHERE code IN (%s)"
            % ",".join("?" * len(TEMP_CODES)), TEMP_CODES).fetchall()]

        all_ids = []
        print("\ndev/code             usun")
        for dev in devices:
            for code in TEMP_CODES:
                ids = _ids_to_delete(conn, dev, code)
                if ids:
                    all_ids.extend(ids)
                    print(f"  {dev[-6:]} {code:16s} {len(ids):7d}")

        print(f"\nRekordow w telemetry (przed): {total_before}")
        print(f"Do usuniecia (temperatury):   {len(all_ids)} "
              f"({100*len(all_ids)/total_before:.1f}% calosci)")

        if not args.apply:
            print("\nDRY-RUN - nic nie usunieto. Uruchom z --apply aby wykonac.")
            return 0

        if not all_ids:
            print("\nBrak rekordow do usuniecia.")
            return 0

        # Weryfikacja SCOP PRZED
        print("\nWeryfikacja SCOP all-time (przed)...")
        before = _scop_all_time(db_path)
        if before:
            print(f"  SCOP={before[0]:.4f} E_el={before[1]:.2f} E_th={before[2]:.2f} n={before[3]}")

        # Usuwanie w partiach (unikamy zbyt dlugiego IN(...))
        t0 = time.time()
        CH = 5000
        conn.execute("PRAGMA foreign_keys=OFF")
        cur = conn.cursor()
        for i in range(0, len(all_ids), CH):
            batch = all_ids[i:i + CH]
            cur.execute(
                "DELETE FROM telemetry WHERE id IN (%s)" % ",".join("?" * len(batch)),
                batch,
            )
        conn.commit()
        total_after = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
        print(f"\nUsunieto: {total_before - total_after} rekordow w {time.time()-t0:.1f}s")
        print(f"Rekordow (po): {total_after}")

        if args.vacuum:
            print("VACUUM (kompaktowanie pliku)...")
            conn.execute("VACUUM")
            conn.commit()

        # Weryfikacja SCOP PO
        print("\nWeryfikacja SCOP all-time (po)...")
        after = _scop_all_time(db_path)
        if before and after:
            print(f"  SCOP={after[0]:.4f} E_el={after[1]:.2f} E_th={after[2]:.2f} n={after[3]}")
            if before[0] > 0:
                diff = abs(after[0] - before[0]) / before[0] * 100
                flag = "OK" if diff <= 1.0 else "UWAGA (>1%)"
                print(f"  roznica SCOP: {diff:.3f}% -> {flag}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

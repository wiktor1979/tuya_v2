"""Analiza rozkładu mocy ukrytej (hidden_power) na pompa obiegowa + wiatrak.

Założenia:
- P_elektronika = 4W (stały, 24/7)
- P_pompa = k * flow_rate, gdzie k=4 (max 80W przy flow_rate=20)
- P_wiatrak = P_total - P_sprzarka - P_pompa - P_elektronika

Używa add_ele (licznik) i ac_curr/ac_vol/flow_rate (pompa) z tych samych okien.
Obliczenia energii w oknach 30-minutowych (zgodne z interwałem add_ele).
"""
import sqlite3
import pandas as pd
import numpy as np

from app.config import DEFAULT_COS_PHI, ENERGY_METER_DEV_ID, HEAT_PUMP_DEV_ID


def analyze_power_breakdown(
    ts_from: int,
    ts_to: int,
    cos_phi: float = DEFAULT_COS_PHI,
    pump_k: float = 4.0,
    elec_power_w: float = 4.0,
) -> dict:
    """
    Analizuje rozkład mocy na sprężarkę, pompę obiegową i wiatrak.
    
    Obliczenia energii w oknach 30-minutowych (zgodne z interwałem add_ele).
    
    Args:
        ts_from, ts_to: Zakres czasu (epoch UTC)
        cos_phi: Współczynnik mocy
        pump_k: Współczynnik P_pompa = k * flow_rate (max 80W przy flow_rate=20)
        elec_power_w: Stały pobór elektroniki [W]
    
    Returns:
        Dict z wynikami analizy.
    """
    conn = sqlite3.connect('data/tuya_telemetry.db')
    
    # Pobierz add_ele z licznika
    add_ele_df = pd.read_sql_query(f"""
        SELECT timestamp, val_num as add_ele
        FROM telemetry
        WHERE device_id = '{ENERGY_METER_DEV_ID}' AND code = 'add_ele'
          AND timestamp >= {ts_from} AND timestamp <= {ts_to}
        ORDER BY timestamp
    """, conn)
    
    if add_ele_df.empty:
        conn.close()
        return {"error": "Brak danych add_ele w wybranym zakresie"}
    
    # Oblicz przyrosty energii i czasy
    add_ele_df['add_ele_wh'] = add_ele_df['add_ele'].diff().fillna(0)
    add_ele_df['dt_sec'] = add_ele_df['timestamp'].diff().fillna(30 * 60)
    add_ele_df['p_total_w'] = add_ele_df['add_ele_wh'] / (add_ele_df['dt_sec'] / 3600)
    
    # Ustaw środek okna jako timestamp
    add_ele_df['ts_mid'] = add_ele_df['timestamp'] - add_ele_df['dt_sec'] / 2
    
    # Pobierz parametry z pompy
    pump_df = pd.read_sql_query(f"""
        SELECT timestamp, code, val_num
        FROM telemetry
        WHERE device_id = '{HEAT_PUMP_DEV_ID}'
          AND code IN ('ac_curr', 'ac_vol', 'flow_rate', 'dc_fan1')
          AND timestamp >= {ts_from} AND timestamp <= {ts_to}
    """, conn)
    
    conn.close()
    
    if pump_df.empty:
        return {"error": "Brak danych pompy"}
    
    # Pivot pump_df
    pump_pivot = pump_df.pivot_table(index="timestamp", columns="code", values="val_num")
    pump_pivot = pump_pivot.fillna(0)
    
    # Złącz add_ele (mid) z pump_pivot po najbliższych timestampach
    add_ele_df['ts_mid_s'] = add_ele_df['ts_mid'].astype(int)
    pump_pivot_reset = pump_pivot.reset_index()
    pump_pivot_reset['timestamp'] = pump_pivot_reset['timestamp'].astype(int)
    
    merged = pd.merge_asof(
        add_ele_df.sort_values('ts_mid_s'),
        pump_pivot_reset.sort_values('timestamp'),
        left_on='ts_mid_s',
        right_on='timestamp',
        direction='nearest',
        tolerance=60
    )
    
    if merged.empty:
        return {"error": "Brak współczesnych raportów (dopasowanie timestampów)"}
    
    # Oblicz moce (tylko gdzie ac_vol > 0)
    merged['P_sprzarka'] = merged.apply(
        lambda r: r['ac_curr'] / 10.0 * r['ac_vol'] * cos_phi if r['ac_vol'] > 0 else 0,
        axis=1
    )
    merged['P_pompa'] = pump_k * (merged['flow_rate'] / 10.0)
    merged['P_wiatrak'] = merged['p_total_w'] - merged['P_sprzarka'] - merged['P_pompa'] - elec_power_w
    
    # Filtruj sensowne wartości
    valid = merged[
        (merged['P_wiatrak'] >= 0) &
        (merged['P_pompa'] >= 0) &
        (merged['P_pompa'] <= 80) &
        (merged['P_wiatrak'] < 200)  # max 200W wiatrak
    ].copy()
    
    if valid.empty:
        return {"error": "Brak sensownych raportów (P_wiatrak < 0 lub P_pompa > 80W lub P_wiatrak > 200W)"}
    
    # Sumy energii (całka prostokątami)
    dt_h = valid['dt_sec'] / 3600.0
    
    e_sprzarka = (valid['P_sprzarka'] * dt_h).sum()
    e_pompa = (valid['P_pompa'] * dt_h).sum()
    e_wiatrak = (valid['P_wiatrak'] * dt_h).sum()
    e_elec = elec_power_w * dt_h.sum()
    
    total_e = e_sprzarka + e_pompa + e_wiatrak + e_elec
    
    # Zsumuj add_ele (z licznika) dla porównania
    conn = sqlite3.connect('data/tuya_telemetry.db')
    add_ele_sum_wh = conn.execute(f"""
        SELECT COALESCE(SUM(val_num), 0)
        FROM telemetry
        WHERE device_id = '{ENERGY_METER_DEV_ID}' AND code = 'add_ele'
          AND timestamp >= {ts_from} AND timestamp <= {ts_to}
    """).fetchone()[0]
    conn.close()
    
    add_ele_sum_kwh = add_ele_sum_wh / 1000.0
    
    # Porównanie
    diff_kwh = total_e - add_ele_sum_kwh
    diff_pct = (diff_kwh / add_ele_sum_kwh * 100) if add_ele_sum_kwh > 0 else 0
    
    # Średnie moce
    avg_sprzarka = valid['P_sprzarka'].mean()
    avg_pompa = valid['P_pompa'].mean()
    avg_wiatrak = valid['P_wiatrak'].mean()
    
    return {
        "sample_count": len(valid),
        "time_range_s": valid['dt_sec'].sum(),
        "avg_sprzarka_w": avg_sprzarka,
        "avg_pompa_w": avg_pompa,
        "avg_wiatrak_w": avg_wiatrak,
        "avg_total_w": avg_sprzarka + avg_pompa + avg_wiatrak + elec_power_w,
        "e_sprzarka_kwh": e_sprzarka,
        "e_pompa_kwh": e_pompa,
        "e_wiatrak_kwh": e_wiatrak,
        "e_elec_kwh": e_elec,
        "total_e_kwh": total_e,
        "add_ele_kwh": add_ele_sum_kwh,
        "diff_kwh": diff_kwh,
        "diff_pct": diff_pct,
        "pompa_max_w": pump_k * 2.0,
        "fan1_avg": valid['dc_fan1'].mean() if 'dc_fan1' in valid.columns else None,
    }


if __name__ == "__main__":
    ts_from = 1788366717
    ts_to = 1788852540
    
    result = analyze_power_breakdown(ts_from, ts_to)
    
    print("\n=== ANALIZA ROZKŁADU MOCY ===")
    if "error" in result:
        print(f"Błąd: {result['error']}")
    else:
        print(f"Raportów: {result['sample_count']}")
        print(f"Czas zakresu: {result['time_range_s'] / 3600:.1f} h")
        print()
        print("ŚREDNIE MOCY [W]:")
        print(f"  Sprężarka:   {result['avg_sprzarka_w']:.1f} W")
        print(f"  Pompa:       {result['avg_pompa_w']:.1f} W")
        print(f"  Wiatrak:     {result['avg_wiatrak_w']:.1f} W")
        print(f"  Elektronika: 4.0 W")
        print(f"  SUMA:        {result['avg_total_w']:.1f} W")
        print()
        print("ZUŻYCIE ENERGII [kWh]:")
        print(f"  Sprężarka:   {result['e_sprzarka_kwh']:.2f}")
        print(f"  Pompa:       {result['e_pompa_kwh']:.2f}")
        print(f"  Wiatrak:     {result['e_wiatrak_kwh']:.2f}")
        print(f"  Elektronika: {result['e_elec_kwh']:.2f}")
        print(f"  SUMA (model): {result['total_e_kwh']:.2f}")
        print(f"  Licznik:     {result['add_ele_kwh']:.2f}")
        print(f"  RÓŻNICA:     {result['diff_kwh']:+.2f} ({result['diff_pct']:+.1f}%)")
        print()
        print("POMPA: max 80W przy flow_rate=20 (skala ×0.1)")
        print(f"  Fan1 avg: {result['fan1_avg']:.0f} RPM")

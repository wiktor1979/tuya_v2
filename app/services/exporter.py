"""Eksport danych do CSV."""
import pandas as pd
from datetime import datetime
from typing import Tuple


def export_to_csv(df: pd.DataFrame, export_type: str = "raw") -> Tuple[str, str]:
    """
    Eksportuje dane do formatu CSV.
    
    Args:
        df: DataFrame z danymi
        export_type: Typ eksportu - 'raw', 'processed', 'daily'
    
    Returns:
        Tuple containing (csv_string, filename)
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if export_type == "raw":
        csv_data = df.to_csv(sep=';', decimal=',', index=False)
        filename = f"tuya_raw_data_{timestamp}.csv"
    elif export_type == "processed":
        # Filtrowanie kolumn do eksportu
        cols_to_export = [c for c in df.columns if c in [
            'czas', 'out_water_temp', 'in_water_temp', 'flow_rate', 
            'ac_vol', 'ac_curr', 'comp_freq', 'disc_temp', 'amb_temp',
            'Tryb', 'P_el_kw', 'P_th_kw', 'COP', 'E_th_kwh', 'E_el_kwh'
        ]]
        csv_data = df[cols_to_export].to_csv(sep=';', decimal=',', index=False)
        filename = f"tuya_processed_data_{timestamp}.csv"
    elif export_type == "daily":
        csv_data = df.to_csv(sep=';', decimal=',', index=False)
        filename = f"tuya_daily_summary_{timestamp}.csv"
    else:
        raise ValueError(f"Nieznany typ eksportu: {export_type}")
    
    return csv_data, filename

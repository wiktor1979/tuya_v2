#!/bin/sh 

# 1. Uruchomienie zbierania danych w tle
python main.py &

# 2. Glowny proces (Streamlit) na pierwszym planie
exec python -m streamlit run Panel.py --server.port 8501 --server.address 0.0.0.0

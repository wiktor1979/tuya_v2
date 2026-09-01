FROM python:3.12-slim

WORKDIR /app

# Instalacja zależności systemowych (jeśli potrzebne)
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Instalacja bibliotek Pythona
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Skopiowanie kodu aplikacji
COPY . .

# Nadanie uprawnień do skryptu startowego
RUN chmod +x start.sh

# Otwarcie portu dla Streamlita
EXPOSE 8501

# Uruchomienie skryptu łączącego procesy
CMD ["./start.sh"]

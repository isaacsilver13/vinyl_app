# syntax=docker/dockerfile:1
FROM python:3.12-slim

# System deps (for cloudscraper / bcrypt native wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Persistent data lives on a Fly volume mounted at /data
ENV VINYL_DATA_DIR=/data

# Expose Streamlit default port
EXPOSE 8501

# Streamlit config: disable telemetry, listen on all interfaces
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true

CMD ["streamlit", "run", "app.py"]

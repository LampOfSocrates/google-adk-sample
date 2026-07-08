# Single image running BOTH processes: the FastAPI agent server (uvicorn) and the
# Streamlit client. Single-user deploy — fine for one person vibing. The client
# talks to the server over localhost inside the container.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    PDF_DATA_DIR=/app/data \
    LLM_BACKEND=gemini

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x deploy/docker-entrypoint.sh

# Only Streamlit is exposed; uvicorn stays on localhost:8000 inside the container.
EXPOSE 8501

ENTRYPOINT ["deploy/docker-entrypoint.sh"]

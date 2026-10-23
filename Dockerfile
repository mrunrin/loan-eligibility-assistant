FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NLTK_DATA=/usr/local/share/nltk_data

WORKDIR /app

COPY requirements-docker.txt .

RUN pip install --no-cache-dir --default-timeout=300 --retries=10 --index-url https://download.pytorch.org/whl/cpu torch==2.13.0
RUN pip install --no-cache-dir --default-timeout=300 --retries=10 -r requirements-docker.txt
RUN python -c "import nltk; nltk.download('punkt_tab')"

COPY app ./app
COPY data ./data
COPY alembic.ini .
COPY migrations ./migrations

RUN mkdir -p /app/uploads /app/vectorstore /app/logs /app/hf_cache /usr/local/share/nltk_data

RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser /app \
    && chown -R appuser /usr/local/share/nltk_data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

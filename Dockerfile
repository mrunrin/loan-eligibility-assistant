FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NLTK_DATA=/usr/local/share/nltk_data

WORKDIR /app

COPY requirements-docker.txt constraints.txt ./

# No local models: embeddings (Jina), rerank (Cohere) and answers (Groq) are hosted APIs.
RUN pip install --no-cache-dir --default-timeout=300 --retries=10 -r requirements-docker.txt -c constraints.txt
# download_dir: without it NLTK saves to /root/nltk_data (the folder below does not exist yet), invisible to appuser.
RUN python -c "import nltk; nltk.download('punkt_tab', download_dir='/usr/local/share/nltk_data')"

COPY app ./app
COPY data ./data
COPY alembic.ini .
COPY migrations ./migrations

RUN mkdir -p /app/uploads /app/vectorstore /app/logs /usr/local/share/nltk_data

RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser /app \
    && chown -R appuser /usr/local/share/nltk_data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

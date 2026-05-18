FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Moscow

# build-essential на случай, если для pyswisseph нет manylinux-колеса под текущую сборку.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        ca-certificates \
        tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/logs

ENV NEUROMULE_PLATFORM=telegram \
    DB_PATH=/app/data/neuromule_base.db \
    LOG_DIR=/app/logs

CMD ["python", "main.py"]

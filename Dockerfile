FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1\
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential\
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock.txt* ./

RUN if [ -f requirements.lock.txt ]; then \
    pip install --prefix=/install -r requirements.lock.txt -i https://mirrors.aliyun.com/pypi/simple/; \
  else \
    pip install --prefix=/install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/; \
  fi

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1\
  PYTHONDONTWRITEBYTECODE=1\
  PYTHONPATH=/app\
  TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends\
    curl\
  && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash appuser

COPY --from=builder /install /usr/local

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3\
  CMD curl -fsS http://127.0.0.1:8000/api/v1/health|| exit 1

CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]


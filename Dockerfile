FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home validator

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

USER validator
ENTRYPOINT ["music-validator"]
CMD ["scan", "--config", "/config/rules.yaml", "--db", "/data/library.sqlite", "--reports", "/reports"]

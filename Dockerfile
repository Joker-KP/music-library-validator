FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home validator
USER validator
ENTRYPOINT ["music-validator"]
CMD ["scan", "--config", "/config/rules.yaml", "--db", "/data/library.sqlite", "--reports", "/reports"]

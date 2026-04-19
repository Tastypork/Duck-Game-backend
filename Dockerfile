FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md docker-entrypoint.sh ./
COPY duck_game_backend ./duck_game_backend

RUN chmod +x docker-entrypoint.sh \
    && pip install --no-cache-dir pip setuptools wheel \
    && pip install --no-cache-dir -e .

EXPOSE 19999

ENTRYPOINT ["./docker-entrypoint.sh"]

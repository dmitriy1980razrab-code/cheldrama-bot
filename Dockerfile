FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app

COPY --chown=bot:bot . /app

USER bot

EXPOSE 8080

CMD ["python", "scripts/run_web.py"]

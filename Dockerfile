# battle_test web UI for the smark_iq server. See plans/server-migration-plan.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Every command (web, users, corpus, evaluate) reads the server config.
    BATTLE_TEST_CONFIG=/app/config.server.toml

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY battle_test/ battle_test/
COPY examples/ examples/
COPY config.server.toml .

# Run as an unprivileged user. The /data folders are created here, owned by
# that user, so the named volumes mounted over them start out writable.
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin battle \
 && mkdir -p /data/law /data/cases \
 && chown -R battle:battle /data
USER battle

EXPOSE 8000
CMD ["python", "-m", "battle_test.web"]

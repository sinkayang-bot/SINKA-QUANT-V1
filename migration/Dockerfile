FROM node:22-bookworm-slim AS node_runtime
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg libstdc++6 \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo 'deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main' > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --home-dir /home/sinka --create-home sinka
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend/*.py /app/backend/
COPY strategy/*.mjs /app/strategy/
ENV HOME=/home/sinka PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER sinka
# No exchange operation starts until an authenticated owner mailbox command.
CMD ["python", "backend/okx_demo_control_runner.py", "--connect", "--seconds", "14400"]

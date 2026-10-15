FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py firestore_app.py firestore_ledger.py firebase_auth.py firestore_monitor.py monitor_lease.py ./
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
EXPOSE 8000
# Default stays on the legacy SQLite app for local Docker Compose compatibility.
# Cloud Run migration will explicitly use firestore_app and a coordinated monitor.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]

COPY leased_monitor.py ./

#!/usr/bin/env bash
# Fitness Trainer starten (http://127.0.0.1:8000)
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Erstelle virtuelle Umgebung…"
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Hinweis: .env wurde angelegt – bitte Garmin-Login eintragen."
fi
exec .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

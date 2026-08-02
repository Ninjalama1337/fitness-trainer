# AGENTS.md — Fitness Trainer

Multi-User-Web-App (PWA) für Garmin-Training mit KI-Trainingsplanung. Backend: Python/FastAPI + SQLite. Frontend: Vanilla JS, kein Build-Step.

## Architektur

```
backend/
  app/
    main.py          # FastAPI-App, Lifespan (DB-Init, Scheduler), CSRF-Middleware
    models.py        # SQLModel: User, Activity, HealthDay, PlanDay, Suggestion, SyncState, GarminCred
    auth.py          # Argon2, Session-Cookies, Rate-Limit, CSRF-Check, Admin-Helper
    crypto.py        # Fernet (Secrets) + Session-Tokens aus SECRET_KEY
    db.py            # Engine, init_db + Migrationen (ALTER TABLE für Alt-DBs!)
    garmin_sync.py   # garminconnect: Aktivitäten, Health, MFA, pro-User-Tokenstores
    garmin_workouts.py # KI-Steps → Garmin-Workout-JSON, Upload, Push an Geräte
    llm.py           # OpenAI-kompatibler Chat-Client, Provider: opencode/openai/anthropic/ollama
    plan_service.py  # LLM-Prompts: Wochenplan + Tagesvorschlag mit strukturierten steps
    routers/         # auth, users (Admin), activities, stats, garmin, upload, plan, suggestion, settings
  tests/test_app.py  # pytest (auth, Isolation, CSRF, LLM-Override, Workout-JSON)
frontend/
  index.html, css/style.css, js/app.js   # PWA, Dark/Light auto, Vanilla JS
  sw.js, manifest.json, icons/
```

## Wichtige Konzepte

- **user_id-Isolation**: Jede Datenzeile gehört einem User; alle Routen filtern per `Depends(auth.get_current_user)` auf `user.id`. Nie global abfragen.
- **Migration**: `db._migrate_columns()` ergänzt fehlende Spalten per `ALTER TABLE` (SQLite erstellt keine neuen Spalten bei bestehenden Tabellen!). Nach Schema-Änderungen IMMER `_migrate_columns` erweitern.
- **Secrets**: Garmin-Passwort + LLM-Keys liegen Fernet-verschlüsselt in der DB (`crypto.encrypt_secret`). `SECRET_KEY` aus Env oder `data/secret.key`.
- **LLM pro User**: User-Felder `llm_*` (nullable) → `llm.get_config(user)` fällt auf globale Env zurück. Standard: opencode-Gateway (`https://opencode.ai/zen/go/v1`), Key-Auto-Discovery in `~/.local/share/opencode/auth.json`.
- **Garmin-Workouts**: `Suggestion.steps`/`PlanDay.steps` (JSON: `{typ: warmup|interval|recovery|cooldown|rest, dauer_min, zone}`) → `garmin_workouts.build_workout()` → `upload_workout` + `push_workout_to_devices`. Nur `running`/`cycling` sendbar.
- **MFA**: `GarminMfaRequired` → HTTP 428 mit `{"mfa": true}` → Frontend zeigt Code-Eingabe.

## Befehle

```bash
./run.sh                    # venv anlegen (falls fehlt) + uvicorn auf :8000
.venv/bin/python -m pytest backend/tests -q   # Tests (23)
```

Server ist als systemd-user-Service aktiv: `systemctl --user restart fitness-trainer` (Logs: `journalctl --user -u fitness-trainer`). `.env` enthält `SECRET_KEY`, `ADMIN_USER`, `ADMIN_PASSWORD`.

## Konventionen

- Kein Node/Build-Step — Frontend bleibt Vanilla JS + eigenes CSS. Asset-URLs mit `?v=N`-Cache-Busting bei Änderungen, dann `sw.js`-`CACHE`-Version bumpen.
- UI-Sprache: Deutsch. API/Code: Englisch.
- Keine Kommentare im Code, außer nötig.
- `requirements.txt` mit Pins (`>=x,<y`).
- `.env`/`data/` nie committen.

## Deployment-Pipeline

1. Push auf `main` im Repo `Ninjalama1337/fitness-trainer` → GH Actions: `ci.yml` (Tests), `build.yml` (Docker → GHCR `ghcr.io/ninjalama1337/fitness-trainer:latest`).
2. Repo `Ninjalama1337/unraid-stacks` → Ordner `fitness-trainer/` (compose.yaml + .env.example). Änderung an `compose.yaml`/`.env.example` → Workflow `komodo-auto-redeploy.yml` deployed den Stack automatisch in Komodo (Unraid). Dafür nach dem GHCR-Build pushen (Reihenfolge beachten!).
3. Stack-Env in Komodo gemergt: bestehende Werte gewinnen, `change_me`/leere Platzhalter → Zufalls-Secrets.
4. Docker-Image: Entrypoint startet als Root, `chown -R appuser /data`, wechselt per gosu zu `appuser` — NICHT `USER appuser` im Dockerfile verwenden (bricht Volume-Permissions).
5. Versionen: `git tag vX.Y.Z && git push origin vX.Y.Z` → Build + optional Komodo-Webhook-Deploy.

## Fallstricke

- **SQLite**: keine neuen Spalten auf Alt-DBs → immer Migration prüfen. PKs mit `user_id` können NULL sein (Alt-Daten) — ORM-Update auf NULL-PK crasht → Migration nutzt rohes SQL (`text()` + `params=`).
- **`s.exec()` (SQLModel)** nimmt KEINE params als 2. Argument — `s.exec(text(...), params={...})`.
- **garminconnect**: inoffizielle API, MFA über `prompt_mfa`-Callback; Tokenstore `data/garmin_tokens/<user_id>/`. Rate-Limits (429) bei zu vielen Logins.
- **LLM**: Reasoning-Modelle brauchen großes `max_tokens` (Default 8000), sonst leere Antwort bei `finish_reason: length`.
- **GitHub Actions**: kein `lower()` in Expressions; Docker-Tags müssen lowercase sein; Komma-Strings bei `tags:` meiden (Multiline-Liste).

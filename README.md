# Fitness Trainer

Web-App (PWA) mit Garmin-Integration und KI-Trainingsplanung – für **mehrere Benutzer** mit eigenen Garmin- und LLM-Konten.

## Features

- **Multi-User**: Jeder mit eigenem Garmin-Login, eigenen Daten und eigener KI-Konfiguration
- **Garmin-Sync**: Automatischer Import von Aktivitäten + Health-Daten (Schlaf, Kalorien, Pulszonen); Workouts werden direkt **auf die Uhr gepusht** (Forerunner, Edge, …)
- **FIT-Upload-Fallback**: FIT-Dateien aus Garmin Connect hochladen
- **KI-Trainingsplan**: 7-Tage-Plan + Tagesvorschlag, abhakbar, mit Wochen-Navigation
- **LLM pro User**: Globaler Standard (OpenCode Go Gateway) oder eigener Provider (OpenAI/Anthropic/Ollama) pro Konto
- **PWA**: Installierbar, offline-fähig, Auto-Dark/Light-Mode, Activity-Rings, animierte Charts
- **Docker-Deployment** mit Auto-Updates (GitHub Actions → GHCR → Komodo)

## Lokaler Start

```bash
cp .env.example .env   # SECRET_KEY + ADMIN_USER/ADMIN_PASSWORD setzen!
./run.sh               # → http://127.0.0.1:8000
```

Beim ersten Start wird der Admin aus der `.env` angelegt. Weitere Benutzer legt der Admin in den Einstellungen an.

## Konfiguration (`.env`)

| Variable | Bedeutung |
|---|---|
| `SECRET_KEY` | **Pflicht.** Signiert Sessions & verschlüsselt Secrets (Garmin-Passwort, LLM-Keys) |
| `ADMIN_USER` / `ADMIN_PASSWORD` | Erster Admin (nur beim Erststart relevant) |
| `LLM_PROVIDER` | Globaler Standard: `opencode` / `openai` / `anthropic` / `ollama` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Globaler LLM-Standard (pro User überschreibbar) |
| `SYNC_INTERVAL_MINUTES` | Auto-Sync-Intervall, `0` = aus |
| `PORT` | Host-Port (Docker) |

## Docker / Unraid / Komodo

1. **Repository** auf GitHub anlegen und pushen (siehe unten). GHCR-Image: `ghcr.io/<user>/fitness-trainer`
2. **Komodo**: Neuen Stack anlegen mit `docker-compose.yml` aus diesem Repo
   - `SECRET_KEY` und `ADMIN_PASSWORD` als Stack-Env setzen (nicht die Defaults verwenden!)
   - Volume `./data:/data` → Daten bleiben bei Updates erhalten
3. **Auto-Updates**: In Komodo beim Stack einen **Deploy-Webhook** erzeugen
   - GitHub → Settings → Secrets: `KOOMODO_DEPLOY_URL` (Webhook-URL) und `KOOMODO_DEPLOY_KEY`
   - Bei jedem `v*`-Tag baut GitHub Actions das Image, pushed nach GHCR und ruft den Webhook auf → Komodo deployed neu

## GitHub-Automatisierung

| Workflow | Wann | Was |
|---|---|---|
| `ci.yml` | Jeder Push/PR | Python-Tests + JS-Syntaxcheck |
| `build.yml` | Push auf `main`, Tags `v*` | Docker-Build → GHCR (`latest`, `vX.Y.Z`, `sha-…`) |
| `deploy.yml` | Tags `v*` | Ruft den Komodo-Deploy-Webhook auf |

Release-Workflow: Tag setzen → `git push --tags` → Tests, Build, Deploy laufen automatisch.

## Sicherheit

- Passwörter: Argon2id-Hashes
- Garmin-Passwörter & LLM-API-Keys: Fernet-verschlüsselt (aus `SECRET_KEY`)
- Sessions: HttpOnly-Cookies, SameSite=Lax, 30 Tage
- CSRF-Schutz (Origin-Check) + Login-Rate-Limit
- Container: non-root, HEALTHCHECK, minimale Python-Basis
- Keine Secrets im Repo; `.env` ist in `.gitignore`

## API (Auszug)

- `POST /api/auth/login` · `GET /api/auth/me`
- `POST /api/garmin/sync` · `POST /api/garmin/credentials`
- `POST /api/garmin/workout/plan-all?week=…` (Workouts an Geräte pushen)
- `GET /api/stats/summary` · `GET /api/stats/zones`
- `GET /api/plan` · `POST /api/plan/generate` · `GET|POST /api/suggestion`
- `GET|POST /api/users` (Admin), `PUT /api/users/me/llm`

## Tests

```bash
.venv/bin/python -m pytest backend/tests -q
```

## Hinweise

- Garmin-Sync nutzt die inoffizielle Connect-API – FIT-Upload bleibt der robuste Fallback
- KI-Vorschläge ersetzen keine ärztliche Beratung

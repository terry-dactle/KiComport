# KiComport

KiComport is a helper service that ingests vendor component libraries and prepares them for integration into a KiCad workspace. It runs beside KiCad (e.g., in Docker on Unraid), watches uploaded archives or KiCad files, and builds an import plan before applying changes to your libraries.

## Features
- FastAPI service with health/status endpoints and JSON-backed job persistence
- Canonical incoming storage with MD5 deduping per original filename
- Heuristic analysis + optional Ollama ranking to build per-job import plans
- Apply + rollback endpoints that now update real `sym-lib-table`/`fp-lib-table`, create backups, and store readable diffs
- Preview + diff endpoints for uploaded archives and KiCad library tables (current vs. backups)
- Minimal web UI at `/ui/jobs` (auto-refresh dashboard + audit log viewer) secured by an optional token and powered by `/ui/jobs/data`
- Structured audit log at `/audit` and `/audit/{job_id}` for undo/rollback history browsing
- Planned: deeper KiCad integration, richer UI approvals, smarter heuristics

## Development Quickstart
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# visit http://localhost:8000/
```

## API Overview
- `POST /imports/upload` – upload vendor file, store under `incoming/`, create job.
- `GET /imports` / `GET /imports/{id}` – list/retrieve jobs (includes plan + backup info).
- `POST /imports/{id}/analyze` – run heuristics across the stored file and record plan metadata (Ollama consulted when enabled).
- `POST /imports/{id}/apply` – back up existing tables, append KiComport entries to `sym-lib-table` / `fp-lib-table`, and persist diffs on the job.
- `POST /imports/{id}/rollback` – restore KiCad tables from the recorded backups and reset job state.
- `POST /imports/{id}/undo` – optional undo endpoint that replays a specific backup path or audit event payload.
- `GET /imports/{id}/preview` – show file meta or zip entry listing for quick triage.
- `GET /imports/{id}/preview/tables` – view current vs. backed-up KiCad library tables.
- `GET /imports/{id}/diff` – read stored or on-demand diffs for library tables tied to the job.
- `GET /audit` / `GET /audit/{job_id}` – read structured audit history (requires token if configured).
- `GET /ui/jobs` – lightweight HTML dashboard (token-protected when configured) with auto-refresh, action buttons, and audit feed driven by `GET /ui/jobs/data`.
- `GET /` and `GET /health` – service status endpoints.

## Docker
```bash
docker build -t kicomport .
docker run -d -p 8080:8080 kicomport
```

## UI Security
- Set `ui.require_token: true` and `ui.token: <value>` in your config to gate `/ui/*` and `/audit*` routes.
- Include the token either via the `X-KiComport-Token` header or by appending `?token=<value>` to the URL. The built-in dashboard stores the token locally and applies it to refreshes + forms.

## Unraid / Production Notes
- Mount `/mnt/user/KiCad` from the host to `/kicad` inside the KiComport container.
- Provide `KICOMPORT_CONFIG_PATH=/kicad/config/kicomport-config.yaml` so the service loads the shared config file.
- The FastAPI server listens on port 8080 inside the container (map to your preferred host port, e.g., 27888).
- Persist `/data` (jobs + audit log) somewhere durable if running in Docker.
- Optional UI is available at `/ui/jobs`; if `ui.require_token` is true, append `?token=<value>` or send `X-KiComport-Token` when accessing UI/audit endpoints (handy for Cloudflare Access or OAuth headers).
- `sym-lib-table`/`fp-lib-table` under `paths.root` will be modified on apply; ensure the mount points map back to your KiCad configuration so diffs/backups correspond to real library tables.

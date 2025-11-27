# KiComport

KiComport is a helper service that ingests vendor component libraries and prepares them for integration into a KiCad workspace. It runs beside KiCad (e.g., in Docker), watches uploaded archives or KiCad files, and builds an import plan before applying changes to your libraries.

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
Pick the path that’s easiest for you:

**One-command setup (make):**
```bash
make dev         # create .venv and install app + dev deps
make run         # start FastAPI with autoreload
make test        # run pytest
```

**Manual setup:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# For tests: pip install -r requirements-dev.txt
uvicorn app.main:app --reload
# visit http://localhost:8000/
```

## API Overview
- `POST /imports/upload` – upload vendor file, store under `incoming/`, create job.
- `GET /imports` / `GET /imports/{id}` – list/retrieve jobs (includes plan + backup info).
- `POST /imports/{id}/analyze` – run heuristics across the stored file and record plan metadata (Ollama consulted when enabled).
- `POST /imports/{id}/apply` – back up existing tables, organize files under `<paths.root>/libs`, append KiComport entries to `sym-lib-table` / `fp-lib-table`, and persist diffs on the job (requires an approved review).
- `POST /imports/{id}/rollback` – restore KiCad tables from the recorded backups and reset job state.
- `POST /imports/{id}/undo` – optional undo endpoint that replays a specific backup path or audit event payload.
- `POST /imports/{id}/review` – capture approval status + reviewer notes; jobs must be approved before applying.
- `GET /imports/{id}/preview` – show file meta or zip entry listing for quick triage.
- `GET /imports/{id}/preview/tables` – view current vs. backed-up KiCad library tables.
- `GET /imports/{id}/diff` – read stored or on-demand diffs for library tables tied to the job.
- `GET /audit` / `GET /audit/{job_id}` – read structured audit history (requires token if configured).
- `GET /ui/jobs` – lightweight HTML dashboard (token-protected when configured) with auto-refresh, action buttons, and audit feed driven by `GET /ui/jobs/data`.
- `GET /` and `GET /health` – service status endpoints.

## Docker
```bash
docker build -t kicomport .
docker run -d -p 27888:27888 kicomport
```

## UI Security
- Set `ui.require_token: true` and `ui.token: <value>` in your config to gate `/ui/*` and `/audit*` routes.
- When a token is required, all import routes (read and write) enforce the same token, including list/get/diff/preview.
- Include the token either via the `X-KiComport-Token` header or by appending `?token=<value>` to the URL. The built-in dashboard stores the token locally and applies it to refreshes + forms.
- The UI shows a KiCad link derived from `integration.kicad_docker_port` to make it easier to jump to the companion container.
- Settings UI now edits logs/backup paths and the UI token; housekeeping actions include job delete and purging `incoming/` (all token-guarded when enabled).
- CI workflow (`.github/workflows/tests.yml`) installs deps and runs `pytest` on pushes/PRs.
- URL downloads: paste HTTP/HTTPS URLs in the UI to fetch files directly into the incoming folder (`/imports/download`).
- Settings panel now shows the active config file path, has one-click token generation, and a reset button to restore loaded values.

## 5 Easy Steps to a Usable KiCad Part
0. **Prep environment** – Mount your KiCad workspace into the KiComport container and set `KICOMPORT_CONFIG_PATH` to a writable config file.
1. **Configure KiComport** – Open `/ui/jobs`, double-check KiCad root paths, incoming/import directories, and port numbers, then save.
2. **Upload vendor assets** – Gather the vendor’s symbols (*.kicad_sym*), footprints (*.pretty`/`*.kicad_mod*), and 3D files (*.step*, *.wrl*, etc.) and drop the folder or archive into the import panel.
3. **Analyze & review** – Run *Analyze*, read the heuristics/AI notes, leave reviewer comments, and mark the job *Approved* when you’re satisfied.
4. **Apply & verify** – Click *Apply* to copy curated files into `<paths.root>/libs/...` and update `sym-lib-table` / `fp-lib-table`, then reload KiCad to confirm the part works (rollback if needed).

### Future Streamlining Ideas
- Provide saved environment presets so configuring paths/ports is a single click.
- Validate uploads automatically and highlight missing symbol/footprint/3D pairings before review.
- Link Apply actions directly to KiCad so you can open the new library entry immediately after deployment.

## Production Notes
- Mount `/mnt/user/KiCad` from the host to `/kicad` inside the KiComport container.
- Provide `KICOMPORT_CONFIG_PATH=/kicad/config/kicomport-config.yaml` so the service loads the shared config file.
- The FastAPI server listens on port 27888 inside the container (map to whichever host port you prefer).
- Persist `/data` (jobs + audit log) somewhere durable if running in Docker.
- Optional UI is available at `/ui/jobs`; if `ui.require_token` is true, append `?token=<value>` or send `X-KiComport-Token` when accessing UI/audit endpoints.
- `sym-lib-table`/`fp-lib-table` under `paths.root` will be modified on apply; ensure the mount points map back to your KiCad configuration so diffs/backups correspond to real library tables.

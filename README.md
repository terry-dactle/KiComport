# Global KiCad Library Intake Server

Dockerised FastAPI service that ingests KiCad library contributions, scans uploads for symbols/footprints/3D models, ranks candidates (heuristics + optional Ollama), and imports selected items into global KiCad library directories. A simple web UI guides uploads, review, and configuration.

## Highlights
- One-file-at-a-time uploads with MD5 deduplication and per-job tracking.
- Archive extraction, candidate discovery (`.kicad_sym`, `.kicad_mod`/`.pretty`, `.step`/`.stp`/`.wrl`, etc.).
- Heuristic scoring plus optional Ollama-based ranking (advisory-only, optional).
- UI for job list/detail, candidate selection per component, import to global KiCad libs.
- Configurable paths for uploads/temp/global symbol/footprint/3D dirs; settings editable in the UI.
- Docker Compose setup with SQLite stored under `/data` volume.

## Layout
- `README.md` — project overview.
- `JOBS.md` — phased roadmap/checklist.
- `.gitignore` — ignore common Python/build/data artifacts.
- `v1/` — all application code and assets  
  - `backend/` — FastAPI app, models, services  
  - `frontend/` — Jinja2 templates/static assets  
  - `docker/` — Dockerfile, docker-compose configs  
  - `config/` — default config templates  
  - `docs/` — architecture, config schema, quick start  
  - `scripts/` — helper scripts  
- `data/` — runtime volume for DB/uploads/extracted data (untracked)

See `v1/docs/QUICK_START.md` for running locally or via Docker.

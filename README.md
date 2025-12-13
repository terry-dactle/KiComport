# Global KiCad Library Import Server

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
- `data/` — runtime volume for DB/temp (untracked)
- `uploads/` — runtime volume for raw uploads (untracked)
- `kicad/` — shared KiCad library root (`symbols/`, `footprints/`, `3d/`) (untracked)

See `v1/docs/QUICK_START.md` for running locally or via Docker.

## Docker Build/Run
Build from the repo root:
```bash
docker build -t kicomport -f v1/docker/Dockerfile .
```

Run the container:
```bash
docker run -d \
  --name kicomport \
  --restart unless-stopped \
  -p 27888:8000 \
  -v /path/to/KiComport-data:/data \
  -v /path/to/KiCad:/kicad \
  -e KICOMPORT_CONFIG_PATH=/kicad/config/kicomport-config.yaml \
  # optional: change container listen port (defaults to 8000)
  # -e KICOMPORT_PORT=8000 \
  kicomport
```

The backend listens on port `8000` inside the container; map any host port you like (examples use `27888`). `/health` and `/` are safe endpoints for a quick status check; `/` redirects to the UI.

## Docker Compose (KiCad + shared libraries)
To run KiComport alongside a KiCad Docker container with a shared `/kicad` volume:
```bash
cd v1/docker
docker compose -f docker-compose.kicad.yaml up -d --build
```

KiComport writes imports under `/kicad/*/kicomport/` (see `v1/config/app_settings.docker.yaml`). Configure KiCad to add libraries from:
- `/kicad/symbols/kicomport/kicomport.kicad_sym`
- `/kicad/footprints/kicomport/kicomport.pretty`
- `/kicad/3d/kicomport/`

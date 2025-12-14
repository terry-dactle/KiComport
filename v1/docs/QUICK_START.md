# Quick Start (v1)

## Prerequisites
- Python 3.11+
- Docker + Docker Compose (for containerized run)

## Local Dev (uvicorn)
```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r v1/backend/requirements-dev.txt   # includes pytest; use requirements.txt for runtime

# run API + templates
uvicorn v1.backend.main:app --reload --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000` to view the UI. Default config lives at `v1/config/app_settings.yaml`; the service will create it with defaults if missing.

## Docker

### Build
From the repo root:
```bash
docker build -t kicomport -f v1/docker/Dockerfile .
```

### Run
```bash
docker run -d \
  --name kicomport \
  --restart unless-stopped \
  -p 27888:8000 \
  -e KICOMPORT_CONFIG_PATH=/app/config/app_settings.docker.yaml \
  -v /path/to/KiComport-data:/data \
  -v /path/to/KiComport-uploads:/uploads \
  -v /path/to/KiCad:/kicad \
  -v /path/to/KiComport-config:/app/config \
  # optional: change container listen port (defaults to 8000)
  # -e KICOMPORT_PORT=8000 \
  kicomport
```

The app listens on `8000` inside the container; map any host port you prefer (examples use `27888`). `/health` and `/` are safe status checks, with `/` redirecting to the UI.

## Docker Compose
The compose file lives under `v1/docker/docker-compose.yaml`.

Example run:
```bash
cd v1/docker
docker compose up -d --build
```

By default compose maps host `27888` to the container port `8000`. Override with `HOST_PORT` (host) or `KICOMPORT_PORT` (container listen port) environment variables as needed.

Docker config defaults are loaded from `v1/config/app_settings.docker.yaml` via `KICOMPORT_CONFIG_PATH`.

Mounted volumes (defaults):
- `../../data:/data` — SQLite DB, temp, extracted files
- `../../uploads:/uploads` — raw uploads
- `../../kicad:/kicad` — shared KiCad library root (`symbols/`, `footprints/`, `3d/`)
- `../config:/app/config` — config files (`app_settings.yaml`, `app_settings.docker.yaml`)

### Docker Compose (KiCad + shared libraries)
If you run KiCad in Docker and want it to see imported libraries, run the bundled KiCad container too:
```bash
cd v1/docker
docker compose -f docker-compose.kicad.yaml up -d --build
```

Both containers share the KiCad `/config` volume. Configure KiCad to add libraries from:
- `/config/data/kicad/symbols/~KiComport.kicad_sym`
- `/config/data/kicad/footprints/~KiComport.pretty`
- `/config/data/kicad/3d/~KiComport/`

On the job page you can optionally set a rename value to save the selected footprint + 3D model under a consistent base filename.

Notes:
- If you use LinuxServer-style UID/GID mapping, set `PUID`/`PGID` so KiComport writes to the shared volume with the same ownership.
- Safety limits can be tuned via env vars like `KICOMPORT_MAX_UPLOAD_BYTES` and `KICOMPORT_MAX_EXTRACT_BYTES`.

## Makefile / scripts
Helper scripts will be added under `v1/scripts` as the project evolves (dev server, lint, cleanup).

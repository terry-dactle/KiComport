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
  -v /path/to/KiComport-data:/data \
  -v /path/to/KiCad:/kicad \
  -e KICOMPORT_CONFIG_PATH=/kicad/config/kicomport-config.yaml \
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

Mounted volumes (defaults):
- `../data:/data` — SQLite DB, temp, extracted files
- `../uploads:/uploads` — raw uploads
- `../v1/config:/app/config` — config files

## Makefile / scripts
Helper scripts will be added under `v1/scripts` as the project evolves (dev server, lint, cleanup).

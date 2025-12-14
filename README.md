# KiComport – Global KiCad Library Import Server

Dockerised FastAPI service that ingests KiCad library contributions, scans uploads for symbols/footprints/3D models, and imports selected items into global KiCad library directories. A simple web UI guides uploads, review, and configuration.

## Features
- Upload a zip or single library file (`.kicad_sym`, `.kicad_mod`, `.step/.stp/.wrl/.obj`)
- Auto-extract + scan candidates (symbols/footprints/3D)
- Review + select candidates per component; import into shared KiCad library folders
- Optional rename on import to keep footprint/3D filenames consistent
- Optional Ollama scoring (advisory-only)
- Configurable paths; settings editable in the UI

## Quick Start (Docker Compose)
From the repo root:
```bash
mkdir -p data uploads kicad
cd v1/docker
docker compose up -d --build
```

Open the UI at `http://<host>:27888` (override with `HOST_PORT`).

### Optional: Run KiCad in Docker (shared libraries)
This starts both KiComport and the LinuxServer KiCad container with a shared `/kicad` mount:
```bash
cd v1/docker
docker compose -f docker-compose.kicad.yaml up -d --build
```

KiCad will be available at `http://<host>:3000` (override with `KICAD_WEB_PORT`). Both containers mount `./kicad` as `/kicad`.

KiComport imports into a single stable set of libraries. In KiCad, add libraries from:
- `/kicad/symbols/~KiComport.kicad_sym`
- `/kicad/footprints/~KiComport.pretty`
- `/kicad/3d/~KiComport/`

## Unraid / Manual Docker Run
Build from the repo root:
```bash
docker build -t kicomport -f v1/docker/Dockerfile .
```

Example (stores everything under `/mnt/user/appdata/kicomport`):
```bash
docker rm -f kicomport 2>/dev/null || true
mkdir -p /mnt/user/appdata/kicomport/{data,uploads,kicad,config}
cat > /mnt/user/appdata/kicomport/config/app_settings.docker.yaml <<'YAML'
app_name: Global KiCad Library Import Server
host: 0.0.0.0
port: 8000

uploads_dir: /uploads
temp_dir: /data/tmp
data_dir: /data
database_path: /data/app.db

kicad_root_dir:
kicad_symbol_dir: /kicad/symbols
kicad_footprint_dir: /kicad/footprints
kicad_3d_dir: /kicad/3d

ollama_enabled: false
ollama_base_url: http://localhost:11434
ollama_model: qwen2.5:7b
ollama_timeout_sec: 30
ollama_max_retries: 2

admin_password: ""
log_level: INFO
log_file:
YAML
docker run -d \
  --name kicomport \
  --restart unless-stopped \
  -p 27888:8000 \
  -e KICOMPORT_CONFIG_PATH=/app/config/app_settings.docker.yaml \
  -v /mnt/user/appdata/kicomport/data:/data \
  -v /mnt/user/appdata/kicomport/uploads:/uploads \
  -v /mnt/user/appdata/kicomport/kicad:/kicad \
  -v /mnt/user/appdata/kicomport/config:/app/config \
  kicomport
```

## Configuration
- Docker defaults: `v1/config/app_settings.docker.yaml`
- Override config path: `KICOMPORT_CONFIG_PATH=/app/config/app_settings.docker.yaml`
- Ports: container listens on `8000` (compose maps host `27888` by default)
- Health check: `GET /health` (UI: `GET /` redirects to `/ui/jobs`)

## Project Layout
- `v1/backend/` — FastAPI app + services
- `v1/frontend/` — Jinja2 templates + static assets
- `v1/docker/` — Dockerfile + compose files
- `v1/config/` — default config templates

See `v1/docs/QUICK_START.md` for more details.

# KiComport — Global KiCad Library Intake Server

Dockerised FastAPI service that ingests KiCad library contributions (zip, KiCad files, or URL), scans for symbols/footprints/3D models, and imports selections into a single shared KiCad library: `~KiComport`.

## Features
- Upload a zip or single library file (`.kicad_sym`, `.kicad_mod`, `.step/.stp/.wrl/.obj`)
- Auto-extract + scan candidates (symbols/footprints/3D)
- Review + select candidates per component; import into one shared `~KiComport` library (add it to KiCad once)
- Optional `Name` on import to keep symbol/footprint/3D names consistent (and rewrites footprint 3D model paths)
- Optional Ollama scoring (advisory-only)
- Configurable paths; settings editable in the UI (plus retention cleanup)

## Recommended Install (KiCad + KiComport, shared libraries)
This starts both KiCad (LinuxServer) and KiComport, sharing the KiCad `/config` volume so imports land where KiCad can read them.

From the repo root:
```bash
mkdir -p data uploads
cd v1/docker
docker compose -f docker-compose.kicad.yaml up -d --build
```

- KiComport UI: `http://<host>:27888` (override with `HOST_PORT`)
- KiCad: `http://<host>:3000` (override with `KICAD_WEB_PORT`)
- If you use `PUID/PGID` (LinuxServer), set them in the compose environment so KiComport writes files with the same ownership.

### One-time KiCad setup (so imports appear)
KiCad does not auto-detect new libraries just because files exist on disk. Add these once in KiCad Preferences:
- Symbol library: `/config/data/kicad/symbols/~KiComport.kicad_sym`
- Footprint library: `/config/data/kicad/footprints/~KiComport.pretty`

3D models are saved under `/config/data/kicad/3d/~KiComport/`.

**Easiest:** in the KiComport UI, use **KiCad Libraries → Install into KiCad** (writes `sym-lib-table` and `fp-lib-table`), then restart KiCad.
If the button errors, open KiCad once to generate its config files, then try again.

## Standalone KiComport (no KiCad container)
From the repo root:
```bash
mkdir -p data uploads kicad
cd v1/docker
docker compose up -d --build
```

This mode writes libraries to the host `./kicad` folder (mounted as `/kicad` in the container). Add libraries in KiCad from:
- `/kicad/symbols/~KiComport.kicad_sym`
- `/kicad/footprints/~KiComport.pretty`

If you hit permission issues on the host-mounted `./kicad` folder, run the container with the right UID/GID (compose supports `PUID`/`PGID`).

## Unraid / Manual Docker Run
Build from the repo root:
```bash
docker build -t kicomport -f v1/docker/Dockerfile .
```

Example (stores KiComport state under `/mnt/user/appdata/kicomport`, and shares an existing LinuxServer KiCad appdata folder as `/config`):
```bash
docker rm -f kicomport 2>/dev/null || true
mkdir -p /mnt/user/appdata/kicomport/{data,uploads,config}
cat > /mnt/user/appdata/kicomport/config/app_settings.kicad.yaml <<'YAML'
app_name: Global KiCad Library Import Server
host: 0.0.0.0
port: 8000

uploads_dir: /uploads
temp_dir: /data/tmp
data_dir: /data
database_path: /data/app.db
retention_days: 30

kicad_root_dir:
kicad_symbol_dir: /config/data/kicad/symbols
kicad_footprint_dir: /config/data/kicad/footprints
kicad_3d_dir: /config/data/kicad/3d

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
  --user ${PUID:-1000}:${PGID:-1000} \
  -p 27888:8000 \
  -e KICOMPORT_CONFIG_PATH=/app/config/app_settings.kicad.yaml \
  -v /mnt/user/appdata/kicomport/data:/data \
  -v /mnt/user/appdata/kicomport/uploads:/uploads \
  -v /mnt/user/appdata/kicad:/config \
  -v /mnt/user/appdata/kicomport/config:/app/config \
  kicomport
```

## Configuration
- Docker defaults: `v1/config/app_settings.docker.yaml` (standalone), `v1/config/app_settings.kicad.yaml` (KiCad shared `/config`)
- Override config path: `KICOMPORT_CONFIG_PATH=/app/config/app_settings.docker.yaml` (or `app_settings.kicad.yaml`)
- Ports: container listens on `8000` (compose maps host `27888` by default)
- Health check: `GET /health` (UI: `GET /` redirects to `/ui/jobs`)
- Cleanup: `retention_days` auto-removes old jobs/files on startup (`0` disables)
- Size limits:
  - `KICOMPORT_MAX_UPLOAD_BYTES` (default `512MB`)
  - `KICOMPORT_MAX_URL_DOWNLOAD_BYTES` (default `100MB`)
  - `KICOMPORT_MAX_EXTRACT_BYTES` (default `2GB`)
  - `KICOMPORT_MAX_EXTRACT_FILES` (default `20000`)
  - `KICOMPORT_MAX_EXTRACT_FILE_BYTES` (default `512MB`)
  - `KICOMPORT_ALLOW_PRIVATE_URL_FETCH=1` to allow fetching URLs from private RFC1918/ULA IPs
- SQLite tuning:
  - `KICOMPORT_SQLITE_WAL=0` to disable WAL mode (enabled by default)
  - `KICOMPORT_SQLITE_TIMEOUT_SEC` (default `30`)

## Project Layout
- `v1/backend/` — FastAPI app + services
- `v1/frontend/` — Jinja2 templates + static assets
- `v1/docker/` — Dockerfile + compose files
- `v1/config/` — default config templates

See `v1/docs/QUICK_START.md` for more details.

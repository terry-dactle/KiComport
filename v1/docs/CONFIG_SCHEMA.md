# Config Schema (v1)

Config is stored in `v1/config/app_settings.yaml` (or `.json`). On startup the app loads this file, applies defaults, and writes it back when updated via the UI/API.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `app_name` | string | `\"Global KiCad Library Import Server\"` | Display name |
| `host` | string | `0.0.0.0` | Bind host |
| `port` | int | `8000` | HTTP port |
| `uploads_dir` | path | `./uploads` | Where raw uploads are stored |
| `temp_dir` | path | `./data/tmp` | Per-job extraction directory |
| `data_dir` | path | `./data` | Root for DB/logs if needed |
| `database_path` | path | `${data_dir}/app.db` | SQLite file |
| `kicad_root_dir` | path | `` | Optional KiCad root; if set, library paths default under this root |
| `kicad_symbol_dir` | path | `./data/kicad/symbols` | Global symbol libs (`.kicad_sym`) |
| `kicad_footprint_dir` | path | `./data/kicad/footprints` | `.pretty` / `.kicad_mod` storage |
| `kicad_3d_dir` | path | `./data/kicad/3d` | 3D models (`.step/.stp/.wrl`, etc.) |
| `ollama_enabled` | bool | `false` | Toggle AI scoring |
| `ollama_base_url` | string | `http://localhost:11434` | Ollama endpoint |
| `ollama_model` | string | `qwen2.5:7b` | Model name/tag |
| `ollama_timeout_sec` | int | `30` | Request timeout |
| `ollama_max_retries` | int | `2` | Retry attempts |
| `admin_password` | string | `` (optional) | Basic gate for settings page (v1: simple check) |
| `log_level` | string | `INFO` | Logging threshold |
| `log_file` | path | `` | Optional log file target (JSON lines) |

## Validation Notes
- Directories are created on startup if missing (uploads, temp, KiCad target dirs).
- Paths are stored absolute after resolution to avoid surprises in Docker volumes.
- `admin_password` is optional and **not** a full auth system; environment handles real security.

## Config File Examples
```yaml
app_name: Global KiCad Library Import Server
host: 0.0.0.0
port: 8000
uploads_dir: ./uploads
temp_dir: ./data/tmp
data_dir: ./data
database_path: ./data/app.db
kicad_root_dir:
kicad_symbol_dir: ./data/kicad/symbols
kicad_footprint_dir: ./data/kicad/footprints
kicad_3d_dir: ./data/kicad/3d
ollama_enabled: false
ollama_base_url: http://localhost:11434
ollama_model: qwen2.5:7b
ollama_timeout_sec: 30
ollama_max_retries: 2
admin_password: \"\"
log_level: INFO
```

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$ROOT/data" "$ROOT/uploads"
cd "$ROOT"
python3 - <<'PY'
from pathlib import Path
from v1.backend.config import load_config, DEFAULT_CONFIG_PATH

cfg = load_config(DEFAULT_CONFIG_PATH)
print("Config ensured at", cfg.config_path)
print("Uploads dir:", cfg.uploads_dir)
print("Temp dir:", cfg.temp_dir)
print("Data dir:", cfg.data_dir)
PY

# Architecture (v1)

This service ingests KiCad library submissions, analyzes them, and imports chosen assets into global library directories. It favors clear, testable modules instead of a heavy framework stack.

## High-Level Flow
1. **Upload**: User posts a single file (zip/lib). The API stores it in `uploads_dir`, computes MD5, and creates a Job record.
2. **Extraction**: Archives are unpacked into a per-job temp directory.
3. **Scanning**: The scanner walks extracted files to find symbols (`.kicad_sym`), footprints (`.kicad_mod`/`.pretty`), and 3D models (`.step/.stp/.wrl`, etc.), extracting lightweight metadata (names, descriptions, pad/pin counts).
4. **Scoring**: Deterministic heuristics score candidates. If enabled, Ollama adds `ai_score`; the combined score ranks candidates.
5. **Selection**: UI shows components/candidates with scores; user selects preferred symbol/footprint/3D.
6. **Import**: Selected items are copied into configured global KiCad directories. Job status/logs are updated.

## Backend Layout
- `main.py`: FastAPI app factory, routes wiring, lifespan tasks (DB init, config load).
- `config/`: Pydantic settings, load/save helpers, validation of directories.
- `db/`: SQLAlchemy engine/session setup, Base models, migrations hook point.
- `models.py`: ORM models for Job, Component, CandidateFile, JobLog.
- `services/`:
  - `jobs.py`: Job creation, dedupe handling, status transitions, logging helpers.
  - `uploads.py`: File persistence, MD5 hashing, safe filenames.
  - `extract.py`: Zip extraction + temp directory management.
  - `scan.py`: File discovery + metadata parsing + heuristic scoring.
  - `ollama.py`: Optional Ollama client with retries/timeouts.
  - `importer.py`: Copy/merge selected assets into KiCad global dirs.
- `api/`: Routers for health, config, jobs, uploads, components, diagnostics, ollama.
- `frontend/`: Jinja2 templates + static assets, served by FastAPI.

## Data Model (initial)
- **Job**: id, md5, original_filename, stored_path, extracted_path, status, created_at, updated_at, flags (duplicate, ai_failed), summary/log pointer.
- **Component**: id, job_id, name/key, selected_symbol_id, selected_footprint_id, selected_model_id.
- **CandidateFile**: id, component_id, type (`symbol`/`footprint`/`model`), path, rel_path, name, description, pin_count/pad_count, heuristic_score, ai_score, quality_score, feedback_score, combined_score, ai_reason, metadata JSON.
- **JobLog**: id, job_id, level, message, created_at.

## Config & Storage
- Settings loaded from `v1/config/app_settings.yaml` (or `.json`), with defaults if missing.
- SQLite database lives under `/data/app.db` (volume-mounted in Docker).
- Uploads stored under `uploads_dir`; extracted content under `temp_dir`.
- Global KiCad dirs configured via settings: `kicad_symbol_dir`, `kicad_footprint_dir`, `kicad_3d_dir`.

## Frontend
- Jinja2 templates rendered from `v1/frontend/templates`.
- Minimal JS (vanilla/HTMX) for form submissions and refreshing job lists.
- Pages: dashboard, upload, job detail (candidates + logs), settings, diagnostics.

## Observability & Maintenance
- Application logging to stdout and optional log file.
- Job-level logs persisted in DB; surfaced in UI.
- Manual cleanup endpoint removes aged uploads/extractions (configurable days).

## Scoring snapshot
- Heuristic: filename/package hints, pin/pad counts, path trust bonus, part-number-like names; STEP models preferred.
- Quality: small bonus for metadata presence, non-empty files, pad/pin counts, path trust.
- AI (optional): `ai_score` + `ai_reason` from Ollama; combined score = `0.6*heuristic + 0.3*ai + quality + feedback`, capped to [0,1].
- Consistency: footprints get a bonus/penalty based on pad vs symbol pin match.
- Feedback: selected_count → small bonus (feedback_score) when imports occur.

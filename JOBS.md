# KiComport Jobs

## Phase 1 – Core service skeleton
- [x] Create FastAPI app with `/` and `/health`.
- [x] Implement config loader with env override.
- [x] Implement JSON job storage in `data/jobs`.
- [x] Implement upload/list/get endpoints with MD5 logic.
- [x] Add `dev-config.yaml`.
- [x] Add Dockerfile.
- [x] Add `requirements.txt`.
- [x] Write/update `REQUIREMENTS.md`.
- [x] Write/update `JOBS.md`.
- [x] Update `README.md` with dev and Docker instructions.

## Phase 2 – Analysis & Workflow
- [x] Expand job/plan models to capture candidates, quality tags, and AI annotations.
- [x] Build heuristics-based analysis service and `/imports/{job_id}/analyze` endpoint.
- [x] Integrate Ollama client stub into analysis flow with graceful fallback.
- [x] Implement `/imports/{job_id}/apply` and `/imports/{job_id}/rollback` with backup tracking.
- [x] Provide `/imports/{job_id}/preview` metadata endpoint for quick file inspection.
- [x] Add richer diff/previews for KiCad library tables.
- [x] Implement web UI for reviewing and applying jobs.
- [x] Add structured audit logging and undo history browser.

## Phase 3 – Deep KiCad integration (in progress)
- [x] Move apply/rollback from placeholders to real KiCad library edits (with backups + diffs).
- [x] Surface detailed diffs for `sym-lib-table` / `fp-lib-table` and expose `/imports/{job_id}/diff`.
- [x] Harden `/ui/jobs` with fetch/refresh controls, token guard, and undo browser tied to audit events.
- [ ] Move beyond table edits to physically organize incoming files under `/kicad/libs/...`.
- [ ] Add user annotations + approval workflow in the UI.
- [ ] Integrate Ollama-assisted ranking feedback into the UI previews and filtering.

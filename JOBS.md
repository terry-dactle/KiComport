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
- [x] Move beyond table edits to physically organize incoming files under `/kicad/libs/...`.
- [x] Add user annotations + approval workflow in the UI.
- [x] Integrate Ollama-assisted ranking feedback into the UI previews and filtering.

## Phase 4 – Hardening & polish
- [x] Make generated `sym-lib-table` / `fp-lib-table` entries valid KiCad syntax and pick highest-ranked symbol/footprint candidates when applying.
- [x] Deduplicate uploads (reuse existing jobs when filename+MD5 matches) and guard import actions with the optional UI token.
- [x] Honor configurable audit/log paths and expose `/audit/{job_id}` to match the docs.
- [x] Implement a real Ollama ranking call (with graceful fallback) so AI annotations populate when enabled.
- [x] Align table entry snippets with KiCad’s expected `(options "") (descr "")` fields and ensure plugin/type values are valid.
- [x] Fail or warn on apply when no symbol/footprint candidates exist rather than silently returning success.
- [x] Protect read-only import endpoints with the optional UI token so metadata/diffs aren’t exposed when the UI is locked down.
- [x] Wire integration ports (KiCad Docker, Ollama) into runtime behavior instead of unused config fields.
- [x] Avoid blocking the event loop with synchronous Ollama requests; add async-friendly execution or background offload.
- [x] Add UI affordance to select which symbol/footprint candidate to apply instead of always picking the top score.
- [x] Resolve Docker image KiCad tooling gap (`kicad-cli`) so apply/rollback can be validated in-container.
- [x] Add minimal tests for token guard and apply-without-candidates behaviour.
- [x] Align footprint table entries to standard `(options (pcbnew_plugin KiCad))` while preserving valid KiCad syntax.
- [x] Add model (`kind: model`) install/link handling and UI selection, so 3D files are placed and referenced.
- [x] Extend settings UI to edit `paths.logs`, `paths.backup`, and the UI token value.
- [x] Enforce token on the `/ui/jobs` page render (not only actions/fetches) when `ui.require_token` is true.
- [x] Provide housekeeping endpoints/UI to delete jobs or purge incoming/old jobs.
- [x] Harden Ollama calls with explicit timeout guard and retry/backoff.
- [x] Improve UI error surfacing (inline/toast) for analyze/apply/rollback instead of only alerts.
- [x] Add CI/automation to install deps and run pytest so regressions are caught (current env lacks Python/pip).
- [x] Surface audit log location/download in the UI and allow quick access to `audit.log`.
- [x] Make Docker image reproducible and slimmer (pin distro/packages, revisit `kicad-cli` install).
- [x] Use `integration.kicad_docker_port` for functional integration (health/link actions) beyond display.

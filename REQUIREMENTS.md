# KiComport Requirements

## Overview
KiComport is a companion service for KiCad that ingests vendor-supplied libraries (zips, `.kicad_sym`, `.pretty`, etc.), analyzes their content, and stages an import plan that can later be applied to an existing KiCad library structure. The service runs beside a KiCad container and interacts exclusively over an HTTP API (later paired with a UI).

## Environment
- Runs in its own container with `/mnt/user/KiCad` from the host mounted at `/kicad`.
- KiCad itself operates inside `linuxserver/kicad` and uses `/mnt/user/KiCad` as `/config`.
- KiComport exposes HTTP on port 27888 by default (remap it however you like when publishing via your preferred tunnel or proxy).

## Current Capabilities
- HTTP endpoints for status/health plus upload/list/get job APIs backed by JSON storage.
- Canonical incoming storage with MD5 deduping per original filename.
- Heuristics-driven analysis that scans uploaded archives/files, tags component types, and prepares import plans (optionally enriched by Ollama when enabled).
- Apply workflow that backups and edits real KiCad `sym-lib-table` / `fp-lib-table` files, persists human-readable diffs, and exposes rollback/undo utilities.
- Preview endpoints that surface both file metadata (archives/directories) and KiCad library table snapshots (current vs. backups).
- Minimal HTML UI for browsing jobs and audit history with auto-refresh + token guard; structured audit log accessible via API/UI with optional access token and undo links.

## Core Features (Planned)
- Accept uploads of vendor archives or KiCad library files.
- Scan for symbols (`.kicad_sym`), footprints (`.pretty` / `.kicad_mod`), and 3D models (`.step`, `.stp`, `.wrl`).
- Build an import plan with detected parts, guessed component types, optional AI ranking, and candidate symbol/footprint/model matches.
- Apply an import plan by placing files into `/kicad/libs/...` layout and updating KiCad library tables with backup + undo tracking.
- Provide HTTP + UI for reviewing plans, applying changes, and rolling back.

## Design Constraints
- No standalone CLI; all interactions go through HTTP endpoints and, later, the UI.
- Jobs are persisted as JSON files under `data/jobs/<job_id>.json`.
- Every upload produces a new job referencing a canonical stored file in `incoming/` using MD5 comparisons to avoid duplicates.
- Backups for `sym-lib-table` and `fp-lib-table` are recorded per job to support undo.
- Audit log entries are stored under `data/audit.log` and exposed via HTTP for transparency/history; the UI/audit APIs honor the optional `ui.require_token` guard for deployments that sit behind extra authentication.
- Apply operations should modify the actual KiCad library table files in place (after backups) so KiCad instances inside the companion container observe the changes in real time.
- Future AI assistance remains advisory only (ranking/suggestions) and never edits KiCad files directly.

## AI/Ollama Behaviour
- AI support is optional and driven by config.
- If disabled, missing URL/model, or requests fail/timeout, the system falls back to heuristics.
- AI is used only for ranking and suggestion metadata and does not write KiCad assets.

## Config Behaviour
- Production config path: `/kicad/config/kicomport-config.yaml`.
- Development default: `./dev-config.yaml` inside the repo.
- Environment variable `KICOMPORT_CONFIG_PATH` overrides the config location.
- Missing or partial config must not prevent the app from starting; sensible defaults are applied and directories (`incoming/`, `logs/`, `backup/`, `data/jobs/`) are created automatically.
- UI-specific config (`ui.require_token`, `ui.token`) controls whether `/ui/*` and `/audit*` endpoints require a static token (handy for setups layered behind external auth).

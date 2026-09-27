# ForgeWorship — Project Context

**What it is:** open-source worship presentation server (EasyWorship alternative).
FastAPI + SQLite backend, vanilla JS frontend (no build pipeline), one process
serves cockpit UI + stage view. Built for offline church LAN use; Windows
non-tech users are the eventual target.

**Status:** working, visually verified. Service active on port 8020.

## Key paths

- Project root: `/home/mvb/Hermes projects/ForgeWorship/` (space in path — quote it)
- Backend entry: `backend/src/main.py` (~1,000 lines, module `src.main` — PYTHONPATH
  is set to `backend/src` in the service)
- DB: `backend/data/forgeworship.db` (SQLite; songs/scriptures/media/themes/
  playlists/presentation state)
- Frontend: `frontend/dist/index.html` (cockpit) + `stage.html` (projector)
- Service: `scripts/forgeworship.service` symlinked to
  `~/.config/systemd/user/forgeworship.service` — `systemctl --user restart forgeworship`
  picks up backend changes
- Tests: `scripts/e2e_test.py` (26 API checks), `scripts/visual_test.js`
  (16 headless-Chromium checks, screenshots → `docs/screenshots/`)
- Seed: `scripts/seed_demo.py` (6 hymns, Isaiah 42:1-9 ESV, 6 themes,
  "Sunday Service (Demo)" playlist)
- Docs: `docs/README.md`, `docs/packaging.md` (single-exe guide)

## Architecture notes (learned the hard way)

- **WS push from sync routes:** route handlers run in a threadpool —
  `asyncio.get_running_loop()` always raises there. `push_state()` uses a loop
  captured at app startup (`_main_loop` via `@app.on_event("startup")`) +
  `call_soon_threadsafe(_schedule_send, payload)`. This was the 500-error /
  dead-stage bug. Don't replace it with anyio tricks.
- Presentation state lives in DB (`state` key) + module singleton `hub` — single
  process only, no `--workers N`.
- `stage.html` text renders via `textContent`; CSS uppercases refs/labels —
  test assertions must use `textContent`, not `innerText` (rendered casing).
- Credits overlay intentionally renders only over song slides.
- Port 8020 chosen to avoid conflicts; cockpit keyboard: N/P/B/L/C/F.

## Verified working (26 Sep 2026)

- e2e API: 26/26. Visual: 16/16 (ALL VISUAL CHECKS PASSED).
- Stage: song slides (title/label/lines/author/CCLI), scripture slides
  (reference · version, 3-verse splits), blank/logo/credits/alerts/countdown,
  theme colors, reconnect + polling fallback, cockpit live-state sync,
  keyboard advance, drag-reorder persist.
- LAN: http://192.168.68.43:8020 (cockpit) and /stage — both verified 200.

## Pending / ideas

- Single-exe packaging (guide written in `docs/packaging.md`; needs the
  `dist_dir()` change applied before first Windows build)
- Auth token (empty = open LAN, fine for church use)
- SharePoint/local folder media browsing
- Cockpit: scripture editor polish, theme editor UI (themes are API/seed-only now)
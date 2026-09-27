# ForgeWorship

An open-source worship presentation server — a self-hosted EasyWorship alternative.
FastAPI + SQLite backend, zero-build vanilla JS frontend, single process serves both
the **Cockpit** (operator UI) and the **Stage View** (projector output).

## Quick start (this machine)

Already installed as a systemd user service, listening on `0.0.0.0:8020`:

- Cockpit: http://192.168.68.43:8020/ (LAN) or http://127.0.0.1:8020/
- Stage:   http://192.168.68.43:8020/stage

```bash
systemctl --user status forgeworship      # it should be active
systemctl --user restart forgeworship     # pick up backend changes
```

Manual run (for development):

```bash
cd "/home/mvb/Hermes projects/ForgeWorship/backend"
../venv/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8020
```

> Path has a space — always quote it in shell commands.

## Features

- **Songs** with `[Verse 1]` / `[Chorus]` section markers, one slide per section
  (blank lines split slides too)
- **Scripture** — verses stored as JSON, auto-split 3 verses per slide
- **Playlists** (order of service) — songs, scripture, media, custom slides
- **Live control** — load / go / next / prev / goto / blank / logo / credits /
  clear / theme override / alert / countdown / end
- **Stage view** — fullscreen, cursor hidden, live via WebSocket with automatic
  HTTP-polling fallback, reconnect banner, offline screen
- **Themes** — calm, readable gradients; per-song / per-media / global override,
  text contrast + overlay opacity tuned for readability
- **Alerts** (bottom banner) and **countdown** (top-right), both API-driven
- **Cockpit keyboard shortcuts** — `N` next, `P` prev, `B` blank, `L` logo,
  `C` credits, `F` clear

## Content management

- Cockpit UI: library search, add/edit songs & scripture, drag-to-reorder
- Seed demo data (6 hymns + Isaiah 42:1-9 ESV + themes + 9-item playlist):

```bash
cd "/home/mvb/Hermes projects/ForgeWorship"
venv/bin/python scripts/seed_demo.py
```

- DB: `backend/data/forgeworship.db` (SQLite, plain file — copy to back up)

## Tests

```bash
# Full API rehearsal (26 checks)
venv/bin/python scripts/e2e_test.py

# Headless-Chromium visual verification (16 checks + screenshots)
node scripts/visual_test.js        # screenshots land in docs/screenshots/
```

## Optional auth

Set `FORGEWORSHIP_TOKEN` in the service file to require a shared token
(empty = open LAN use, which is the intended mode for a church LAN).

## Packaging

See [packaging.md](packaging.md) for the single-exe / installer story
(PyInstaller now, Tauri sidecar later).
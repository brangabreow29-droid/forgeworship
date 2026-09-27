# ForgeWorship — Packaging (single exe / installer)

Target: Windows non-tech users. The app is FastAPI + SQLite + static HTML/JS,
so the backend can ship as one file and serve the frontend from inside itself.

## Current state

The frontend is plain static files (`frontend/dist/`) served by the FastAPI app,
**but** the static dir is resolved from a relative path. For packaging, switch to
bundled-file lookup before freezing — see "Code change required" below.

## Option A — PyInstaller (recommended first step)

One file, ~12-18 MB with Python 3.13, zero runtime deps for the user.

```bash
cd "/home/mvb/Hermes projects/ForgeWorship"
venv/bin/pip install pyinstaller

venv/bin/pyinstaller --onefile --name ForgeWorship \
  backend/src/main.py \
  --add-data "frontend/dist:frontend_dist"
```

(On Windows use `;` instead of `:` in `--add-data`.)

### Code change required

In `main.py`, the static mount should look for the bundled copy first:

```python
import sys, os
from pathlib import Path

def dist_dir() -> Path:
    # PyInstaller: files extracted under sys._MEIPASS
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "frontend_dist"
    if bundled.exists():
        return bundled
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"

app.mount("/", StaticFiles(directory=dist_dir(), html=True), name="static")
```

### Run behaviour

- Launch: user double-clicks `ForgeWorship.exe` → server starts on
  `http://127.0.0.1:8020`, DB is created next to the exe
  (`ForgeWorship/data/forgeworship.db` — keep this, it survives updates)
- Auto-open the cockpit with `webbrowser.open(...)` after startup (add to
  the startup event when `--packaged`)
- Firewall prompt: Windows will ask once for LAN access — expect it, it's normal
- Optional launcher flags: `--port`, `--no-browser`, `--data-dir`

### Stage display on Windows

Stage view is just a browser page: user opens Edge in kiosk mode on the
projector output:

```
msedge --kiosk http://127.0.0.1:8020/stage --edge-kiosk-type=fullscreen
```

No extra packaging needed for the projector.

## Option B — Tauri sidecar (later, "real installer" polish)

Tauri wraps the PyInstaller exe as a sidecar binary in a native installer with
Start-menu shortcuts, an icon, and a proper webview window for the cockpit
(~15-25 MB total). Worth it once Option A is proven on a real church PC, not
before — the sidecar config is fiddly and buys UI polish, not function.

- Tauri config: `externalBin: "binaries/ForgeWorship"`; the Rust side spawns it
  on app start, kills it on exit, and the webview loads `http://127.0.0.1:8020`
- Ship SQLite via the backend (already bundled) — no Rust-side DB needed

## What does NOT work

- Freezing with `--onedir` plus separately-zipped `frontend/dist` — the exe
  must find the static files inside `_MEIPASS` (handled above)
- uvicorn workers > 1: the app keeps presentation state in SQLite + a module
  singleton (`hub`) — always run the packaged exe single-process (default)
- Python 3.13 + very old PyInstaller: pin `pyinstaller>=6.10` on the build box
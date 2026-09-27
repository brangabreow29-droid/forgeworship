"""ForgeWorship — local-first worship presentation backend.

FastAPI + SQLite. Serves the Cockpit UI, the Stage View, and the JSON/WebSocket
API that keeps every connected Stage in sync with the operator's desk.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

# Packaged (PyInstaller onefile) builds extract to a temp dir that is wiped on
# exit — keep the SQLite DB next to the real executable instead so it survives
# restarts/updates. Dev runs keep the repo path.
if getattr(sys, "frozen", False):
    BASE = Path(sys.executable).resolve().parent  # folder containing the exe
else:
    BASE = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = BASE / "data"
THEME_DIR = BASE / "themes"
DB_PATH = DATA_DIR / "forgeworship.db"

# Packaged (PyInstaller) builds extract bundled files under sys._MEIPASS; the
# frontend dist is embedded there as frontend_dist/. Dev runs use the repo copy.
_bundled_dist = Path(getattr(sys, "_MEIPASS", "")) / "frontend_dist"
FRONTEND_DIST = _bundled_dist if _bundled_dist.exists() else BASE.parent / "frontend" / "dist"
AUTH_TOKEN = os.environ.get("FORGEWORSHIP_TOKEN", "")

app = FastAPI(title="ForgeWorship", version="1.0.0")

_main_loop: "asyncio.AbstractEventLoop | None" = None


@app.on_event("startup")
async def _capture_loop() -> None:
    """Remember the event loop so sync route handlers can schedule WS broadcasts."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- database
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    background TEXT NOT NULL,          -- CSS color or 'gradient:json'
    text_color TEXT NOT NULL,
    sub_text_color TEXT NOT NULL,
    font_family TEXT NOT NULL DEFAULT 'Inter, system-ui, sans-serif',
    font_size_vw REAL NOT NULL DEFAULT 5.2,
    font_weight TEXT NOT NULL DEFAULT '600',
    shadow TEXT NOT NULL DEFAULT '1',
    align TEXT NOT NULL DEFAULT 'center',
    overlay_opacity REAL NOT NULL DEFAULT 0.55,
    is_default INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    ccli TEXT DEFAULT '',
    lyrics TEXT NOT NULL DEFAULT '',   -- sections separated by blank lines; [Section] markers
    theme_id INTEGER REFERENCES themes(id),
    notes TEXT DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scriptures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'NIV',
    verses TEXT NOT NULL,              -- JSON array of {v, text}
    notes TEXT DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'image',   -- image | video | color
    path TEXT NOT NULL,                   -- URL or file path or CSS color
    theme_id INTEGER REFERENCES themes(id),
    notes TEXT DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    service_date TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS playlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,                -- song | scripture | media | blank | custom
    ref_id INTEGER,                    -- id in the source table (NULL for blank/custom)
    label TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}' -- JSON: slides array etc.
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = db()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) c FROM themes").fetchone()["c"] == 0:
        seed_themes(conn)
    if conn.execute("SELECT COUNT(*) c FROM playlists").fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO playlists (name, service_date, created_at, updated_at) VALUES (?,?,?,?)",
            ("Sunday Service", time.strftime("%Y-%m-%d"), time.time(), time.time()),
        )
    conn.commit()
    conn.close()


def seed_themes(conn: sqlite3.Connection) -> None:
    """Calm, readable defaults tuned for projectors (light text, dark restful bg)."""
    themes = [
        ("Calm Dawn", "gradient:linear-gradient(160deg,#1c2541 0%,#274060 55%,#3a5a78 100%)", "#f4f7fb", "#bcd2e8", 0.35, 1),
        ("Deep Peace", "gradient:linear-gradient(180deg,#0f2027 0%,#203a43 50%,#2c5364 100%)", "#f7fafc", "#a8c6d8", 0.30, 0),
        ("Soft Evening", "gradient:linear-gradient(150deg,#2b1f3a 0%,#3e2f52 60%,#52406e 100%)", "#faf7fc", "#cbb8dd", 0.35, 0),
        ("Forest Quiet", "gradient:linear-gradient(160deg,#14261c 0%,#1f3a2a 55%,#2f5240 100%)", "#f4fbf6", "#b3d4c0", 0.30, 0),
        ("Still Water", "gradient:linear-gradient(165deg,#0b2545 0%,#134266 50%,#1b6ca8 100%)", "#f5fbff", "#aed3ef", 0.30, 0),
        ("Plain White", "#ffffff", "#1a2733", "#5a7184", 0.0, 0),
    ]
    for name, bg, txt, sub, ov, is_def in themes:
        conn.execute(
            "INSERT INTO themes (name, background, text_color, sub_text_color, overlay_opacity, is_default) VALUES (?,?,?,?,?,?)",
            (name, bg, txt, sub, ov, is_def),
        )


def get_state(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO state (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def theme_row(conn: sqlite3.Connection, theme_id: Optional[int]):
    if theme_id:
        row = conn.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
        if row:
            return row
    return conn.execute("SELECT * FROM themes WHERE is_default=1").fetchone() or conn.execute("SELECT * FROM themes LIMIT 1").fetchone()


def resolve_background(bg: str) -> dict:
    if bg.startswith("gradient:"):
        return {"type": "gradient", "css": bg[len("gradient:") :]}
    if bg.startswith("#") or bg.startswith("rgb"):
        return {"type": "color", "css": bg}
    return {"type": "color", "css": bg}


def build_theme_payload(row: sqlite3.Row) -> dict:
    bg = resolve_background(row["background"])
    return {
        "id": row["id"],
        "name": row["name"],
        "background": bg,
        "text_color": row["text_color"],
        "sub_text_color": row["sub_text_color"],
        "font_family": row["font_family"],
        "font_size_vw": row["font_size_vw"],
        "font_weight": row["font_weight"],
        "shadow": row["shadow"] == "1",
        "align": row["align"],
        "overlay_opacity": row["overlay_opacity"],
    }


# ---------------------------------------------------------------- models
class SongIn(BaseModel):
    title: str
    author: str = ""
    ccli: str = ""
    lyrics: str = ""
    theme_id: Optional[int] = None
    notes: str = ""


class ScriptureIn(BaseModel):
    reference: str
    version: str = "NIV"
    verses: list[dict]
    notes: str = ""


class MediaIn(BaseModel):
    name: str
    kind: str = "image"
    path: str
    theme_id: Optional[int] = None
    notes: str = ""


class ThemeIn(BaseModel):
    name: str
    background: str
    text_color: str
    sub_text_color: str = "#ffffff"
    font_family: str = "Inter, system-ui, sans-serif"
    font_size_vw: float = 5.2
    font_weight: str = "600"
    shadow: str = "1"
    align: str = "center"
    overlay_opacity: float = 0.5
    is_default: int = 0


class PlaylistIn(BaseModel):
    name: str
    service_date: str = ""
    notes: str = ""


class ItemIn(BaseModel):
    kind: str                       # song | scripture | media | custom
    ref_id: Optional[int] = None
    label: Optional[str] = None
    payload: dict = {}


class ItemsIn(BaseModel):
    items: list[ItemIn]


class ReorderIn(BaseModel):
    order: list[int]  # playlist_item ids in new order


class PresentIn(BaseModel):
    action: str  # load | next | prev | goto | blank | logo | credits | clear | end | theme
    item_index: Optional[int] = None
    slide_index: Optional[int] = None
    theme_id: Optional[int] = None


class AlertIn(BaseModel):
    text: str
    duration: int = 8


class CountdownIn(BaseModel):
    minutes: int = 0  # 0 cancels


class SearchIn(BaseModel):
    q: str


# ---------------------------------------------------------------- helpers
def parse_lyrics_sections(lyrics: str) -> list[dict]:
    """Split a lyrics block into slides. A section = lines separated by blank line.
    Supports [Verse 1] style markers; marker becomes the slide label."""
    sections: list[dict] = []
    current_label = None
    current_lines: list[str] = []
    for raw in lyrics.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            if current_lines:
                sections.append({"label": current_label or f"Slide {len(sections)+1}", "lines": current_lines})
                current_lines = []
            current_label = line[1:-1].strip().title()
        elif not line:
            if current_lines:
                sections.append({"label": current_label or f"Slide {len(sections)+1}", "lines": current_lines})
                current_lines = []
                current_label = None
        else:
            current_lines.append(line)
    if current_lines:
        sections.append({"label": current_label or f"Slide {len(sections)+1}", "lines": current_lines})
    return sections


def song_slides(conn: sqlite3.Connection, song: sqlite3.Row) -> list[dict]:
    theme = build_theme_payload(theme_row(conn, song["theme_id"]))
    sections = parse_lyrics_sections(song["lyrics"] or "")
    return [
        {
            "type": "song",
            "ref_id": song["id"],
            "label": sec["label"],
            "lines": sec["lines"],
            "title": song["title"],
            "author": song["author"] or "",
            "theme": theme,
        }
        for sec in sections
    ]


def scripture_slides(conn: sqlite3.Connection, script: sqlite3.Row, verses_per_slide: int = 3) -> list[dict]:
    theme = build_theme_payload(theme_row(conn, None))
    verses = json.loads(script["verses"])
    chunks = [verses[i : i + verses_per_slide] for i in range(0, len(verses), verses_per_slide)]
    slides = []
    total = len(chunks)
    for n, chunk in enumerate(chunks, 1):
        text = " ".join(f"{v['v']} {v['text'].strip()}" for v in chunk)
        slides.append(
            {
                "type": "scripture",
                "ref_id": script["id"],
                "label": f"{script['reference']} ({n}/{total})",
                "text": text,
                "version": script["version"],
                "reference": script["reference"],
                "theme": theme,
            }
        )
    return slides


def media_slides(conn: sqlite3.Connection, m: sqlite3.Row) -> list[dict]:
    theme = build_theme_payload(theme_row(conn, m["theme_id"]))
    return [{"type": "media", "ref_id": m["id"], "label": m["name"], "path": m["path"], "kind": m["kind"], "theme": theme}]


def item_slides(conn: sqlite3.Connection, item: sqlite3.Row) -> list[dict]:
    kind, ref_id = item["kind"], item["ref_id"]
    if kind == "song" and ref_id:
        song = conn.execute("SELECT * FROM songs WHERE id=?", (ref_id,)).fetchone()
        if song:
            return song_slides(conn, song)
    if kind == "scripture" and ref_id:
        script = conn.execute("SELECT * FROM scriptures WHERE id=?", (ref_id,)).fetchone()
        if script:
            return scripture_slides(conn, script)
    if kind == "media" and ref_id:
        m = conn.execute("SELECT * FROM media WHERE id=?", (ref_id,)).fetchone()
        if m:
            return media_slides(conn, m)
    payload = json.loads(item["payload"] or "{}")
    theme = build_theme_payload(theme_row(conn, payload.get("theme_id")))
    if kind == "custom" and payload.get("lines"):
        return [{"type": "custom", "label": item["label"], "lines": payload["lines"], "theme": theme}]
    if kind == "blank":
        return [{"type": "blank", "label": item["label"] or "Blank", "theme": theme}]
    return []


# ---------------------------------------------------------------- state & sync
class Hub:
    """In-process live presentation state + WebSocket fan-out."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.lock_generation = 0

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    def state(self) -> dict:
        conn = db()
        st = get_state(conn, "present", {})
        conn.close()
        return st or {
            "mode": "idle",
            "playlist_id": None,
            "playlist_name": "",
            "item_index": None,
            "slide_index": None,
            "slides": [],
            "slide": None,
            "display": "normal",  # normal | blank | logo | credits
            "theme": build_theme_payload(theme_row(db(), None)),
            "updated_at": 0,
        }

    def snapshot(self) -> dict:
        return {"type": "state", "state": self.state(), "ts": time.time()}


hub = Hub()


def push_state() -> None:
    """Fan the current state out to all connected stage/cockpit sockets."""
    if not hub.clients or _main_loop is None:
        return
    payload = json.dumps(hub.snapshot())
    _main_loop.call_soon_threadsafe(_schedule_send, payload)


def _schedule_send(payload: str) -> None:
    try:
        _main_loop.create_task(_send_all(payload))
    except RuntimeError:
        pass  # loop shutting down — clients fall back to HTTP polling


async def _send_all(payload: str) -> None:
    dead = []
    for ws in list(hub.clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        hub.disconnect(ws)


def load_item(conn: sqlite3.Connection, item_index: int) -> dict:
    st = get_state(conn, "present", {})
    pid = st.get("playlist_id")
    if not pid:
        raise HTTPException(400, "No playlist loaded")
    items = conn.execute(
        "SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position", (pid,)
    ).fetchall()
    if not items:
        raise HTTPException(400, "Playlist is empty")
    item_index = max(0, min(item_index, len(items) - 1))
    item = items[item_index]
    slides = item_slides(conn, item)
    if not slides:
        raise HTTPException(400, f"Item '{item['label']}' has no slides")
    return {
        "mode": "live",
        "playlist_id": pid,
        "playlist_name": st.get("playlist_name", ""),
        "item_index": item_index,
        "item_label": item["label"],
        "slide_index": 0,
        "slides": slides,
        "slide": slides[0],
        "display": "normal",
        "ts": time.time(),
    }


# ---------------------------------------------------------------- routes: health
@app.get("/api/health")
def health():
    return {"ok": True, "name": "ForgeWorship", "time": time.time()}


# ---------------------------------------------------------------- routes: themes
@app.get("/api/themes")
def list_themes():
    conn = db()
    rows = [build_theme_payload(r) for r in conn.execute("SELECT * FROM themes ORDER BY name")]
    conn.close()
    return rows


@app.post("/api/themes")
def create_theme(t: ThemeIn):
    conn = db()
    if t.is_default:
        conn.execute("UPDATE themes SET is_default=0")
    cur = conn.execute(
        "INSERT INTO themes (name,background,text_color,sub_text_color,font_family,font_size_vw,font_weight,shadow,align,overlay_opacity,is_default) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (t.name, t.background, t.text_color, t.sub_text_color, t.font_family, t.font_size_vw, t.font_weight, t.shadow, t.align, t.overlay_opacity, t.is_default),
    )
    conn.commit()
    tid = cur.lastrowid
    row = conn.execute("SELECT * FROM themes WHERE id=?", (tid,)).fetchone()
    conn.close()
    return build_theme_payload(row)


@app.put("/api/themes/{theme_id}")
def update_theme(theme_id: int, t: ThemeIn):
    conn = db()
    if t.is_default:
        conn.execute("UPDATE themes SET is_default=0 WHERE id != ?", (theme_id,))
    cur = conn.execute(
        "UPDATE themes SET name=?,background=?,text_color=?,sub_text_color=?,font_family=?,font_size_vw=?,font_weight=?,shadow=?,align=?,overlay_opacity=?,is_default=? WHERE id=?",
        (t.name, t.background, t.text_color, t.sub_text_color, t.font_family, t.font_size_vw, t.font_weight, t.shadow, t.align, t.overlay_opacity, t.is_default, theme_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(404, "Theme not found")
    row = conn.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
    conn.close()
    return build_theme_payload(row)


@app.delete("/api/themes/{theme_id}")
def delete_theme(theme_id: int):
    conn = db()
    n = conn.execute("SELECT COUNT(*) c FROM themes WHERE is_default=1 AND id=?", (theme_id,)).fetchone()["c"]
    if n:
        conn.close()
        raise HTTPException(400, "Cannot delete the default theme")
    conn.execute("UPDATE songs SET theme_id=NULL WHERE theme_id=?", (theme_id,))
    conn.execute("UPDATE media SET theme_id=NULL WHERE theme_id=?", (theme_id,))
    conn.execute("DELETE FROM themes WHERE id=?", (theme_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------------------------------------------------------- routes: songs
@app.get("/api/songs")
def list_songs(q: str = Query("", description="search title/lyrics/author")):
    conn = db()
    if q:
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT id,title,author,ccli,theme_id,updated_at FROM songs WHERE title LIKE ? OR lyrics LIKE ? OR author LIKE ? ORDER BY title",
            (like, like, like),
        ).fetchall()
    else:
        rows = conn.execute("SELECT id,title,author,ccli,theme_id,updated_at FROM songs ORDER BY title").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/songs/{song_id}")
def get_song(song_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Song not found")
    d = dict(row)
    d["slides_preview"] = parse_lyrics_sections(row["lyrics"] or "")
    return d


@app.post("/api/songs")
def create_song(s: SongIn):
    conn = db()
    cur = conn.execute(
        "INSERT INTO songs (title,author,ccli,lyrics,theme_id,notes,updated_at) VALUES (?,?,?,?,?,?,?)",
        (s.title, s.author, s.ccli, s.lyrics, s.theme_id, s.notes, time.time()),
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return {"id": sid}


@app.put("/api/songs/{song_id}")
def update_song(song_id: int, s: SongIn):
    conn = db()
    cur = conn.execute(
        "UPDATE songs SET title=?,author=?,ccli=?,lyrics=?,theme_id=?,notes=?,updated_at=? WHERE id=?",
        (s.title, s.author, s.ccli, s.lyrics, s.theme_id, s.notes, time.time(), song_id),
    )
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Song not found")
    return {"ok": True}


@app.delete("/api/songs/{song_id}")
def delete_song(song_id: int):
    conn = db()
    conn.execute("DELETE FROM songs WHERE id=?", (song_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------------------------------------------------------- routes: scriptures
@app.get("/api/scriptures")
def list_scriptures(q: str = ""):
    conn = db()
    if q:
        like = f"%{q}%"
        rows = conn.execute("SELECT id,reference,version,updated_at FROM scriptures WHERE reference LIKE ? ORDER BY reference", (like,)).fetchall()
    else:
        rows = conn.execute("SELECT id,reference,version,updated_at FROM scriptures ORDER BY reference").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/scriptures/{script_id}")
def get_scripture(script_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM scriptures WHERE id=?", (script_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Not found")
    return dict(row)


@app.post("/api/scriptures")
def create_scripture(s: ScriptureIn):
    conn = db()
    cur = conn.execute(
        "INSERT INTO scriptures (reference,version,verses,notes,updated_at) VALUES (?,?,?,?,?)",
        (s.reference, s.version, json.dumps(s.verses), s.notes, time.time()),
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return {"id": sid}


@app.delete("/api/scriptures/{script_id}")
def delete_scripture(script_id: int):
    conn = db()
    conn.execute("DELETE FROM scriptures WHERE id=?", (script_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------------------------------------------------------- routes: media
@app.get("/api/media")
def list_media():
    conn = db()
    rows = conn.execute("SELECT * FROM media ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/media")
def create_media(m: MediaIn):
    conn = db()
    cur = conn.execute(
        "INSERT INTO media (name,kind,path,theme_id,notes,updated_at) VALUES (?,?,?,?,?,?)",
        (m.name, m.kind, m.path, m.theme_id, m.notes, time.time()),
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return {"id": mid}


@app.delete("/api/media/{media_id}")
def delete_media(media_id: int):
    conn = db()
    conn.execute("DELETE FROM media WHERE id=?", (media_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------------------------------------------------------- routes: playlists
@app.get("/api/playlists")
def list_playlists():
    conn = db()
    rows = conn.execute(
        """SELECT p.*, (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id=p.id) items
           FROM playlists p ORDER BY p.updated_at DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/playlists")
def create_playlist(p: PlaylistIn):
    conn = db()
    cur = conn.execute(
        "INSERT INTO playlists (name,service_date,notes,created_at,updated_at) VALUES (?,?,?,?,?)",
        (p.name, p.service_date, p.notes, time.time(), time.time()),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {"id": pid}


@app.get("/api/playlists/{playlist_id}")
def get_playlist(playlist_id: int):
    conn = db()
    p = conn.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
    if not p:
        conn.close()
        raise HTTPException(404, "Playlist not found")
    items = conn.execute("SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position", (playlist_id,)).fetchall()
    conn.close()
    return {"playlist": dict(p), "items": [dict(i) for i in items]}


@app.put("/api/playlists/{playlist_id}")
def update_playlist(playlist_id: int, p: PlaylistIn):
    conn = db()
    cur = conn.execute(
        "UPDATE playlists SET name=?,service_date=?,notes=?,updated_at=? WHERE id=?",
        (p.name, p.service_date, p.notes, time.time(), playlist_id),
    )
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Playlist not found")
    return {"ok": True}


@app.delete("/api/playlists/{playlist_id}")
def delete_playlist(playlist_id: int):
    conn = db()
    conn.execute("DELETE FROM playlist_items WHERE playlist_id=?", (playlist_id,))
    conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/playlists/{playlist_id}/items")
def add_item(playlist_id: int, item: ItemIn):
    conn = db()
    pos = conn.execute("SELECT COALESCE(MAX(position),-1)+1 p FROM playlist_items WHERE playlist_id=?", (playlist_id,)).fetchone()["p"]
    label = item.label
    if not label:
        if item.kind == "song" and item.ref_id:
            label = conn.execute("SELECT title FROM songs WHERE id=?", (item.ref_id,)).fetchone()["title"]
        elif item.kind == "scripture" and item.ref_id:
            label = conn.execute("SELECT reference FROM scriptures WHERE id=?", (item.ref_id,)).fetchone()["reference"]
        elif item.kind == "media" and item.ref_id:
            label = conn.execute("SELECT name FROM media WHERE id=?", (item.ref_id,)).fetchone()["name"]
        else:
            label = item.kind.title()
    cur = conn.execute(
        "INSERT INTO playlist_items (playlist_id,position,kind,ref_id,label,payload) VALUES (?,?,?,?,?,?)",
        (playlist_id, pos, item.kind, item.ref_id, label, json.dumps(item.payload)),
    )
    conn.execute("UPDATE playlists SET updated_at=? WHERE id=?", (time.time(), playlist_id))
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return {"id": iid, "position": pos, "label": label}


@app.post("/api/playlists/{playlist_id}/items/batch")
def add_items(playlist_id: int, items: ItemsIn):
    conn = db()
    pos = conn.execute("SELECT COALESCE(MAX(position),-1)+1 p FROM playlist_items WHERE playlist_id=?", (playlist_id,)).fetchone()["p"]
    ids = []
    for item in items.items:
        label = item.label
        if not label:
            if item.kind == "song" and item.ref_id:
                r = conn.execute("SELECT title FROM songs WHERE id=?", (item.ref_id,)).fetchone()
                label = r["title"] if r else "Song"
            elif item.kind == "scripture" and item.ref_id:
                r = conn.execute("SELECT reference FROM scriptures WHERE id=?", (item.ref_id,)).fetchone()
                label = r["reference"] if r else "Scripture"
            elif item.kind == "media" and item.ref_id:
                r = conn.execute("SELECT name FROM media WHERE id=?", (item.ref_id,)).fetchone()
                label = r["name"] if r else "Media"
            else:
                label = item.kind.title()
        cur = conn.execute(
            "INSERT INTO playlist_items (playlist_id,position,kind,ref_id,label,payload) VALUES (?,?,?,?,?,?)",
            (playlist_id, pos, item.kind, item.ref_id, label, json.dumps(item.payload)),
        )
        ids.append(cur.lastrowid)
        pos += 1
    conn.execute("UPDATE playlists SET updated_at=? WHERE id=?", (time.time(), playlist_id))
    conn.commit()
    conn.close()
    return {"ids": ids}


@app.put("/api/playlists/{playlist_id}/items/{item_id}")
def update_item(playlist_id: int, item_id: int, item: ItemIn):
    conn = db()
    label = item.label
    if not label:
        if item.kind == "song" and item.ref_id:
            label = conn.execute("SELECT title FROM songs WHERE id=?", (item.ref_id,)).fetchone()["title"]
        elif item.kind == "scripture" and item.ref_id:
            label = conn.execute("SELECT reference FROM scriptures WHERE id=?", (item.ref_id,)).fetchone()["reference"]
        else:
            label = item.kind.title()
    conn.execute(
        "UPDATE playlist_items SET kind=?,ref_id=?,label=?,payload=? WHERE id=? AND playlist_id=?",
        (item.kind, item.ref_id, label, json.dumps(item.payload), item_id, playlist_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/playlists/{playlist_id}/items/{item_id}")
def delete_item(playlist_id: int, item_id: int):
    conn = db()
    conn.execute("DELETE FROM playlist_items WHERE id=? AND playlist_id=?", (item_id, playlist_id))
    # re-pack positions
    rows = conn.execute("SELECT id FROM playlist_items WHERE playlist_id=? ORDER BY position", (playlist_id,)).fetchall()
    for i, r in enumerate(rows):
        conn.execute("UPDATE playlist_items SET position=? WHERE id=?", (i, r["id"]))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.put("/api/playlists/{playlist_id}/reorder")
def reorder(playlist_id: int, body: ReorderIn):
    conn = db()
    for pos, iid in enumerate(body.order):
        conn.execute("UPDATE playlist_items SET position=? WHERE id=? AND playlist_id=?", (pos, iid, playlist_id))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------------------------------------------------------- routes: present
@app.get("/api/state")
def api_state():
    return hub.state()


@app.post("/api/present")
def present(p: PresentIn):
    conn = db()
    st = get_state(conn, "present", {}) or {}
    action = p.action
    if action == "load":
        pid = p.item_index  # overloaded: playlist id for load
        pl = conn.execute("SELECT * FROM playlists WHERE id=?", (pid,)).fetchone()
        if not pl:
            conn.close()
            raise HTTPException(404, "Playlist not found")
        items = conn.execute("SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position", (pid,)).fetchall()
        st = {
            "mode": "loaded",
            "playlist_id": pid,
            "playlist_name": pl["name"],
            "item_index": None,
            "slide_index": None,
            "slides": [],
            "slide": None,
            "display": "normal",
            "item_count": len(items),
            "ts": time.time(),
        }
    elif action == "go":
        idx = p.item_index or 0
        st = load_item(conn, idx)
        st["playlist_name"] = (get_state(conn, "present", {}) or {}).get("playlist_name", st.get("playlist_name", ""))
    elif action == "next":
        cur = st.get("slide_index")
        slides = st.get("slides", [])
        if cur is None or not slides:
            conn.close()
            raise HTTPException(400, "Nothing live")
        if cur + 1 < len(slides):
            st["slide_index"] = cur + 1
            st["slide"] = slides[cur + 1]
            st["display"] = "normal"
            st["ts"] = time.time()
        else:
            items = conn.execute("SELECT * FROM playlist_items WHERE playlist_id=? ORDER BY position", (st["playlist_id"],)).fetchall()
            nxt = st.get("item_index", -1) + 1
            if nxt < len(items):
                st = load_item(conn, nxt)
            # else stay on last slide
    elif action == "prev":
        cur = st.get("slide_index")
        slides = st.get("slides", [])
        if cur is None or not slides:
            conn.close()
            raise HTTPException(400, "Nothing live")
        if cur > 0:
            st["slide_index"] = cur - 1
            st["slide"] = slides[cur - 1]
            st["display"] = "normal"
            st["ts"] = time.time()
        else:
            prv = st.get("item_index", 0) - 1
            if prv >= 0:
                st = load_item(conn, prv)
                st["slide_index"] = len(st["slides"]) - 1
                st["slide"] = st["slides"][-1]
    elif action == "goto":
        idx = p.slide_index or 0
        slides = st.get("slides", [])
        if not slides:
            conn.close()
            raise HTTPException(400, "Nothing live")
        idx = max(0, min(idx, len(slides) - 1))
        st["slide_index"] = idx
        st["slide"] = slides[idx]
        st["display"] = "normal"
        st["ts"] = time.time()
    elif action in ("blank", "logo", "credits"):
        st["display"] = "logo" if action == "logo" else ("credits" if action == "credits" else "blank")
        st["ts"] = time.time()
    elif action == "clear":
        st["display"] = "normal"
        st["ts"] = time.time()
    elif action == "end":
        st = {
            "mode": "idle",
            "playlist_id": st.get("playlist_id"),
            "playlist_name": st.get("playlist_name", ""),
            "item_index": None,
            "slide_index": None,
            "slides": [],
            "slide": None,
            "display": "normal",
            "ts": time.time(),
        }
    elif action == "theme":
        tid = p.theme_id
        row = theme_row(conn, tid)
        th = build_theme_payload(row)
        st["theme"] = th
        slide = st.get("slide")
        if slide:
            slide["theme"] = th
        st["ts"] = time.time()
    else:
        conn.close()
        raise HTTPException(400, f"Unknown action: {action}")
    set_state(conn, "present", st)
    conn.commit()
    conn.close()
    push_state()
    return st


@app.post("/api/alert")
def alert(a: AlertIn):
    conn = db()
    st = get_state(conn, "present", {}) or {}
    st["alert"] = {"text": a.text, "until": time.time() + a.duration}
    set_state(conn, "present", st)
    conn.commit()
    conn.close()
    push_state()
    return {"ok": True}


@app.post("/api/countdown")
def countdown(c: CountdownIn):
    conn = db()
    st = get_state(conn, "present", {}) or {}
    if c.minutes > 0:
        st["countdown_end"] = time.time() + c.minutes * 60
    else:
        st["countdown_end"] = None
    set_state(conn, "present", st)
    conn.commit()
    conn.close()
    push_state()
    return {"ok": True}


# ---------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await hub.connect(ws)
    try:
        await ws.send_text(json.dumps(hub.snapshot()))
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(json.dumps({"type": "pong", "ts": time.time()}))
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)


# ---------------------------------------------------------------- frontend
@app.get("/")
def index():
    fp = FRONTEND_DIST / "index.html"
    if fp.exists():
        return FileResponse(fp)
    return HTMLResponse("<h1>ForgeWorship</h1><p>Frontend not built. See README.</p>")


@app.get("/stage")
def stage():
    fp = FRONTEND_DIST / "stage.html"
    if fp.exists():
        return FileResponse(fp)
    return HTMLResponse("<h1>Stage view missing</h1>")


@app.get("/favicon.ico")
def favicon():
    fp = FRONTEND_DIST / "favicon.svg"
    if fp.exists():
        return FileResponse(fp, media_type="image/svg+xml")
    return HTMLResponse("", status_code=204)


if __name__ == "__main__":
    import argparse
    import threading
    import webbrowser

    parser = argparse.ArgumentParser(description="ForgeWorship worship presentation server")
    parser.add_argument("--port", type=int, default=8020, help="HTTP port (default 8020)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open the cockpit")
    args = parser.parse_args()

    init_db()

    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_browser:
        # Open the cockpit once the server is accepting connections.
        def _open_browser():
            for _ in range(60):
                try:
                    import urllib.request
                    urllib.request.urlopen(url, timeout=1)
                    break
                except Exception:
                    time.sleep(0.5)
            webbrowser.open(url)
        threading.Thread(target=_open_browser, daemon=True).start()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
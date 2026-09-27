#!/usr/bin/env python3
"""End-to-end service rehearsal through the live HTTP API.

Runs a complete demo service exactly as an operator would:
load playlist -> walk every item slide-by-slide -> blank/logo/credits ->
theme override -> alert -> countdown -> end service. Asserts every transition.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import urllib.request

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode())


def check(label, cond, detail=""):
    global FAILS
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ FAIL: {label} {detail}")
        FAILS.append(label)


FAILS = []

# ---- start server ----------------------------------------------------------
server = subprocess.Popen(
    [str(ROOT / "venv" / "bin" / "python"), "-m", "uvicorn", "src.main:app",
     "--host", "127.0.0.1", "--port", "8000"],
    cwd=str(ROOT / "backend"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
try:
    for _ in range(40):
        try:
            req("GET", "/api/health")
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("server never came up")
        sys.exit(1)

    print("== health ==")
    h = req("GET", "/api/health")
    check("health ok", h.get("ok") is True)

    print("== content ==")
    songs = req("GET", "/api/songs")
    check("6 songs seeded", len(songs) >= 6, str(len(songs)))
    scripts = req("GET", "/api/scriptures")
    check("scripture seeded", len(scripts) >= 1)
    themes = req("GET", "/api/themes")
    check("6 calm themes", len(themes) >= 6, str(len(themes)))
    pls = req("GET", "/api/playlists")
    demo = next(p for p in pls if "Demo" in p["name"])
    pl = req("GET", f"/api/playlists/{demo_id if (demo_id := demo['id']) else 0}")
    check("playlist has 9 items", len(pl["items"]) == 9, str(len(pl["items"])))
    song_detail = req("GET", f"/api/songs/{songs[0]['id']}")
    check("lyrics parse into sections", len(song_detail["slides_preview"]) >= 2,
          str(len(song_detail["slides_preview"])))

    print("== load & run the service ==")
    st = req("POST", "/api/present", {"action": "load", "item_index": demo_id})
    check("playlist loaded", st["mode"] == "loaded" and st["item_count"] == 9)

    expected_slides = []
    for i in range(9):
        st = req("POST", "/api/present", {"action": "go" if i == 0 else "next"})
        # after 'go' on the last slide of an item, next advances to next item
    # walk deterministically: for each item, goto slide 0 then next through slides
    st = req("GET", "/api/state")
    total_slide_steps = 0
    while True:
        if st.get("slide_index") is not None and st.get("slides"):
            idx = st["slide_index"]
        if st["slide_index"] < len(st["slides"]) - 1:
            st = req("POST", "/api/present", {"action": "next"})
            total_slide_steps += 1
        else:
            if st["item_index"] < 8:
                st = req("POST", "/api/present", {"action": "next"})
                total_slide_steps += 1
                if st.get("item_index") is None:
                    break
            else:
                break
    check("walked whole service", st["item_index"] == 8 and st["slide_index"] == len(st["slides"]) - 1,
          f"ended at item {st['item_index']} slide {st['slide_index']}, steps {total_slide_steps}")
    slide = st["slide"]
    check("last item is benediction custom slide", slide["type"] == "custom" and "LORD bless" in " ".join(slide["lines"]),
          str(slide)[:120])

    print("== prev / goto ==")
    st = req("POST", "/api/present", {"action": "prev"})
    check("prev crosses item boundary", st["slide_index"] == len(st["slides"]) - 1 and st["item_index"] == 7)
    st = req("POST", "/api/present", {"action": "goto", "slide_index": 0})
    check("goto slide 0", st["slide_index"] == 0)

    print("== overlays ==")
    st = req("POST", "/api/present", {"action": "blank"})
    check("blank", st["display"] == "blank")
    st = req("POST", "/api/present", {"action": "logo"})
    check("logo", st["display"] == "logo")
    st = req("POST", "/api/present", {"action": "credits"})
    check("credits", st["display"] == "credits")
    st = req("POST", "/api/present", {"action": "clear"})
    check("clear", st["display"] == "normal")

    print("== theme override ==")
    st = req("POST", "/api/present", {"action": "theme", "theme_id": themes[2]["id"]})
    check("theme override applies to live slide", st["slide"]["theme"]["name"] == themes[2]["name"])
    st = req("POST", "/api/present", {"action": "theme", "theme_id": themes[0]["id"]})
    check("theme revert to default", st["slide"]["theme"]["name"] == themes[0]["name"])

    print("== alert + countdown ==")
    req("POST", "/api/alert", {"text": "Pot luck lunch after the service", "duration": 30})
    st = req("GET", "/api/state")
    check("alert set", st.get("alert", {}).get("text", "").startswith("Pot luck"))
    req("POST", "/api/countdown", {"minutes": 5})
    st = req("GET", "/api/state")
    check("countdown set", (st.get("countdown_end") or 0) > time.time())
    req("POST", "/api/countdown", {"minutes": 0})
    st = req("GET", "/api/state")
    check("countdown cleared", not st.get("countdown_end"))

    print("== Isaiah 42 content check ==")
    pl = req("GET", f"/api/playlists/{demo_id}")
    isa = next(i for i in pl["items"] if i["kind"] == "scripture")
    # go live on it
    idx = pl["items"].index(isa)
    req("POST", "/api/present", {"action": "go", "item_index": idx})
    st = req("GET", "/api/state")
    check("Isaiah item produces 3 slides (3 verses each)", len(st["slides"]) == 3, str(len(st["slides"])))
    texts = " ".join(s.get("text", "") for s in st["slides"])
    check("Isaiah 42:1 text present", "Behold my servant" in texts)
    check("Isaiah 42:9 text present", "new things I now declare" in texts)
    check("scripture reference shown", st["slide"]["reference"].startswith("Isaiah 42"))

    print("== websocket sync ==")
    # quick WS probe
    out = subprocess.run([str(ROOT / "venv" / "bin" / "python"), "-c", """
import asyncio, json
async def t():
    try:
        import websockets
    except ImportError:
        print("WS_SKIPPED no websockets lib")
        return
    async with websockets.connect("ws://127.0.0.1:8000/ws") as ws:
        hello = json.loads(await ws.recv())
        assert hello["type"] == "state", hello
        print("WS_OK first state type:", hello["type"])
asyncio.run(t())
"""], capture_output=True, text=True, timeout=15)
    ws_out = (out.stdout + out.stderr).strip()
    if "WS_SKIPPED" in ws_out:
        print("  ~ websocket lib not installed; falling back to polling check")
        st = req("GET", "/api/state")
        check("polling fallback has state", st.get("mode") is not None)
    else:
        check("websocket handshake + initial state", "WS_OK" in ws_out, ws_out[:200])

    print("== stage + cockpit pages ==")
    with urllib.request.urlopen(BASE + "/stage", timeout=10) as r:
        body = r.read().decode()
        check("stage page served", "ForgeWorship — Stage" in body and "connect()" in body)
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        body = r.read().decode()
        check("cockpit page served", "Cockpit" in body and "goNext" in body)

    print("== end service ==")
    st = req("POST", "/api/present", {"action": "end"})
    check("service ended cleanly", st["mode"] == "idle" and st["slide"] is None)

    print()
    if FAILS:
        print("E2E FAILED:", len(FAILS), "failures ->", FAILS)
        sys.exit(1)
    print("E2E PASSED — full demo service runs clean.")
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except Exception:
        server.kill()
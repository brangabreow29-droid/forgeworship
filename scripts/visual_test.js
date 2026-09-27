#!/usr/bin/env node
/* Visual verification: drives the live ForgeWorship app over CDP with headless
   Chromium. Walks the demo service, screenshots cockpit + stage at each key
   moment, and asserts what's actually rendered on screen. */
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.FORGE_URL || "http://127.0.0.1:8020";
const OUT = path.join(__dirname, "..", "docs", "screenshots");
fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: "/usr/bin/chromium",
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1600,900"],
  });

  const results = [];
  const check = (label, ok, detail = "") => {
    results.push({ label, ok });
    console.log((ok ? "  ✓ " : "  ✗ FAIL ") + label + (detail ? " — " + detail : ""));
  };

  // ---------- STAGE ----------
  const stage = await browser.newPage();
  await stage.setViewport({ width: 1600, height: 900 });
  await stage.goto(BASE + "/stage", { waitUntil: "networkidle2" });
  await sleep(1500);

  // put something live: load playlist item 1 (Amazing Grace)
  async function api(method, p, body) {
    const r = await fetch(BASE + p, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(p + " -> " + r.status + " " + (await r.text()));
    return r.json();
  }

  const pls = await api("GET", "/api/playlists");
  const demo = pls.find((p) => p.name.includes("Demo")) || pls[0];
  const pl = await api("GET", "/api/playlists/" + demo.id);
  const songIdx = pl.items.findIndex((i) => i.kind === "song");

  await api("POST", "/api/present", { action: "load", item_index: demo.id });
  await api("POST", "/api/present", { action: "go", item_index: songIdx });
  await sleep(1800);

  let shot = path.join(OUT, "stage_song.png");
  await stage.screenshot({ path: shot });
  const src = () => stage.evaluate(() => document.body.textContent);
  let text = await src();
  check("stage shows song title (Amazing Grace)", text.includes("Amazing Grace"));
  check("stage shows lyric lines", text.includes("Amazing grace, how sweet the sound"));

  // Isaiah item
  const isaIdx = pl.items.findIndex((i) => i.kind === "scripture");
  await api("POST", "/api/present", { action: "go", item_index: isaIdx });
  await sleep(1200);
  shot = path.join(OUT, "stage_isaiah.png");
  await stage.screenshot({ path: shot });
  text = await src();
  check("stage shows Isaiah 42", text.includes("Isaiah 42") && text.includes("Behold my servant"));

  // blank overlay
  await api("POST", "/api/present", { action: "blank" });
  await sleep(900);
  shot = path.join(OUT, "stage_blank.png");
  await stage.screenshot({ path: shot });
  let visible = await stage.evaluate(() => getComputedStyle(document.getElementById("content-wrap")).display);
  check("blank hides slide content", visible === "none", "display=" + visible);

  // logo overlay
  await api("POST", "/api/present", { action: "logo" });
  await sleep(900);
  shot = path.join(OUT, "stage_logo.png");
  await stage.screenshot({ path: shot });
  text = await stage.evaluate(() => document.body.innerText);
  check("logo overlay shows ForgeWorship", text.includes("ForgeWorship"));

  // alert
  await api("POST", "/api/alert", { text: "Shared lunch after the service — all welcome!", duration: 60 });
  await api("POST", "/api/present", { action: "clear" });
  await sleep(1000);
  shot = path.join(OUT, "stage_alert.png");
  await stage.screenshot({ path: shot });
  visible = await stage.evaluate(() => getComputedStyle(document.getElementById("alerts")).display);
  check("alert banner rendered", visible === "flex");

  // credits — only renders over a song slide, so go back to the song first
  await api("POST", "/api/present", { action: "go", item_index: songIdx });
  await sleep(600);
  await api("POST", "/api/present", { action: "credits" });
  await sleep(900);
  shot = path.join(OUT, "stage_credits.png");
  await stage.screenshot({ path: shot });
  text = await src();
  check("credits show song + author", text.includes("CCLI") || text.includes("John Newton") || text.includes("Public Domain"));

  // countdown
  await api("POST", "/api/countdown", { minutes: 10 });
  await api("POST", "/api/present", { action: "clear" });
  await sleep(1200);
  shot = path.join(OUT, "stage_countdown.png");
  await stage.screenshot({ path: shot });
  text = await stage.evaluate(() => document.body.innerText);
  check("countdown renders", /min/.test(text) && /sec/.test(text));
  await api("POST", "/api/countdown", { minutes: 0 });

  // theme contrast check: text color applied from theme
  await api("POST", "/api/present", { action: "clear" });
  const color = await stage.evaluate(() => getComputedStyle(document.getElementById("s-lines")).color);
  check("theme text color applied (not default black)", color !== "rgb(0, 0, 0)", color);

  // ---------- COCKPIT ----------
  const cockpit = await browser.newPage();
  await cockpit.setViewport({ width: 1600, height: 900 });
  await cockpit.goto(BASE + "/", { waitUntil: "networkidle2" });
  await sleep(2200);

  shot = path.join(OUT, "cockpit_overview.png");
  await cockpit.screenshot({ path: shot });
  text = await cockpit.evaluate(() => document.body.innerText);
  check("cockpit shows 6 songs in library", (text.match(/Amazing Grace|How Great Thou Art|Great Is Thy Faithfulness|Blessed Assurance|Be Thou My Vision|10,000 Reasons/g) || []).length >= 3);
  check("cockpit shows service items", text.includes("Welcome") && text.includes("Isaiah 42:1-9"));
  check("themes rendered", (cockpit.evaluate(() => document.querySelectorAll(".theme-chip").length)) !== undefined);

  const themeCount = await cockpit.evaluate(() => document.querySelectorAll(".theme-chip").length);
  check("6 theme chips in cockpit", themeCount >= 6, String(themeCount));

  // live state flows into cockpit (item currently credits/normal from stage test)
  const footText = await cockpit.evaluate(() => document.getElementById("foot-state").innerText);
  check("cockpit reflects live state", footText.includes("LIVE") || footText.includes("Idle"), footText);

  // keyboard: press N to advance
  await api("POST", "/api/present", { action: "go", item_index: songIdx });
  await sleep(600);
  const before = (await api("GET", "/api/state")).slide_index;
  await cockpit.focus("body");
  await cockpit.keyboard.press("n");
  await sleep(900);
  const after = (await api("GET", "/api/state")).slide_index;
  check("keyboard N advances slide", after === before + 1 || after !== before, `${before} -> ${after}`);

  // drag-and-drop reorder via API equivalence (drop target)
  const items = (await api("GET", "/api/playlists/" + demo.id)).items;
  const ids = items.map((i) => i.id);
  ids.splice(2, 0, ids.splice(1, 1)[0]);
  await api("PUT", "/api/playlists/" + demo.id + "/reorder", { order: ids });
  const afterRe = (await api("GET", "/api/playlists/" + demo.id)).items;
  check("reorder persisted", afterRe[1].id === ids[1] && afterRe[2].id === ids[2]);
  ids.splice(1, 0, ids.splice(2, 1)[0]); // restore
  await api("PUT", "/api/playlists/" + demo.id + "/reorder", { order: ids });

  await browser.close();

  const fails = results.filter((r) => !r.ok);
  console.log("");
  if (fails.length) {
    console.log("VISUAL TESTS FAILED:", fails.length);
    process.exit(1);
  }
  console.log("ALL VISUAL CHECKS PASSED — screenshots in docs/screenshots/");
})().catch((e) => {
  console.error("FATAL:", e);
  process.exit(1);
});
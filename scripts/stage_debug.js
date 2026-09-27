// Stage render debug
const puppeteer = require("puppeteer-core");
(async () => {
  const BASE = "http://127.0.0.1:8020";
  const browser = await puppeteer.launch({ executablePath: "/usr/bin/chromium", headless: "new", args: ["--no-sandbox"] });
  const stage = await browser.newPage();
  stage.on("console", (m) => console.log("CONSOLE:", m.type(), m.text().slice(0, 200)));
  stage.on("pageerror", (e) => console.log("PAGEERROR:", e.message.slice(0, 300)));
  await stage.setViewport({ width: 1600, height: 900 });
  await stage.goto(BASE + "/stage", { waitUntil: "networkidle2" });
  await new Promise((r) => setTimeout(r, 2000));
  const state = await (await fetch(BASE + "/api/state")).json();
  console.log("API state mode:", state.mode, "| slide:", state.slide ? state.slide.label : null, "| display:", state.display);
  const dbg = await stage.evaluate(() => ({
    wrapDisplay: getComputedStyle(document.getElementById("content-wrap")).display,
    linesText: document.getElementById("s-lines").textContent.slice(0, 80),
    offline: getComputedStyle(document.getElementById("offline")).display,
    bgCount: document.querySelectorAll(".bg").length,
  }));
  console.log("DEBUG:", JSON.stringify(dbg));
  await browser.close();
  process.exit(0);
})().catch((e) => { console.error("FATAL", e.message); process.exit(1); });
#!/usr/bin/env node
/* Real-browser interaction runner for api/static.
 *
 * Contract:
 *  - loads the UNMODIFIED static tree (or --static <copy> for mutation runs);
 *  - serves everything through ctx.route(), so no HTTP server and no port;
 *  - one browser per invocation, one fresh context/page per case;
 *  - prints one ASCII PASS/FAIL line per case and writes a JSON map;
 *  - exit 0 all pass, 1 any case failed, 2 environment missing.
 *
 * Usage:
 *   node tests/integration/ui_browser/runner.cjs [--static DIR] [--only A,B]
 *        [--json OUT] [--list]
 */
"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");

const REPO = path.resolve(__dirname, "..", "..", "..");
const DEFAULT_STATIC = path.join(REPO, "api", "static");
const BASE = "http://dsh.probe/";

function arg(name, def) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const staticDir = path.resolve(arg("static", DEFAULT_STATIC));
const only = String(arg("only", "")).split(",").map(s => s.trim()).filter(Boolean);
const jsonOut = arg("json", "");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".png": "image/png",
};

function loadPlaywright() {
  try {
    return require("playwright-core");
  } catch (e) {
    const msg = "playwright-core is not installed: run `npm ci` in " + REPO;
    const err = new Error(msg + " (" + e.message + ")");
    err.code = "NO_DEP";
    throw err;
  }
}

async function launchBrowser(chromium) {
  const attempts = [];
  if (process.env.UI_BROWSER_PATH) attempts.push({ executablePath: process.env.UI_BROWSER_PATH });
  attempts.push({ channel: "msedge" }, { channel: "chrome" });
  let last = null;
  for (const opt of attempts) {
    try {
      return await chromium.launch(Object.assign({ headless: true }, opt));
    } catch (e) {
      last = e;
    }
  }
  const err = new Error(
    "no usable browser: tried UI_BROWSER_PATH, channel=msedge, channel=chrome. " +
    "Install Edge/Chrome or set UI_BROWSER_PATH. Last error: " + (last ? last.message : "?"));
  err.code = "NO_BROWSER";
  throw err;
}

async function installRoutes(context, opts) {
  const posts = [];
  let permissionStatus = (opts && opts.permissionStatus) || 200;
  await context.route("**/*", async (route) => {
    const req = route.request();
    let u;
    try { u = new URL(req.url()); } catch (e) { return route.abort(); }
    if (u.origin !== "http://dsh.probe") return route.continue().catch(() => {});
    const p = decodeURIComponent(u.pathname);
    try {
      if (p === "/" || p === "/index.html") {
        return route.fulfill({ status: 200, contentType: MIME[".html"],
          body: fs.readFileSync(path.join(staticDir, "index.html")) });
      }
      if (p.startsWith("/static/")) {
        const rel = p.slice("/static/".length);
        const file = path.normalize(path.join(staticDir, rel));
        if (!file.startsWith(staticDir)) return route.fulfill({ status: 403, body: "forbidden" });
        if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: "not found" });
        return route.fulfill({ status: 200,
          contentType: MIME[path.extname(file)] || "application/octet-stream",
          body: fs.readFileSync(file) });
      }
      if (p === "/api/modes") {
        return route.fulfill({ status: 200, contentType: MIME[".json"],
          body: JSON.stringify({ modes: ["fast"], topologies: {} }) });
      }
      if (p === "/api/runs") {
        return route.fulfill({ status: 200, contentType: MIME[".json"],
          body: JSON.stringify({ runs: [] }) });
      }
      if (p === "/api/evolve/versions") {
        return route.fulfill({ status: 200, contentType: MIME[".json"], body: "[]" });
      }
      if (p === "/api/backends") {
        return route.fulfill({ status: 200, contentType: MIME[".json"],
          body: JSON.stringify({ backends: [] }) });
      }
      if (/^\/api\/runs\/[^/]+\/permission$/.test(p) && req.method() === "POST") {
        posts.push({ url: req.url(), body: req.postData(), headers: req.headers() });
        const ok = permissionStatus === 200;
        return route.fulfill({ status: permissionStatus, contentType: MIME[".json"],
          body: JSON.stringify(ok ? { status: "answered" } : { detail: "rejected" }) });
      }
      if (p.startsWith("/api/")) {
        return route.fulfill({ status: 200, contentType: MIME[".json"], body: "{}" });
      }
      return route.fulfill({ status: 404, body: "not found" });
    } catch (e) {
      return route.fulfill({ status: 500, body: String(e && e.stack || e) });
    }
  });
  return { posts, setPermissionStatus: (s) => { permissionStatus = s; } };
}

async function openPage(browser, opts) {
  const context = await browser.newContext({
    viewport: (opts && opts.viewport) || { width: 1400, height: 900 },
    deviceScaleFactor: 1,
  });
  const api = await installRoutes(context, opts || {});
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String((e && e.message) || e)));
  await page.goto(BASE, { waitUntil: "load" });
  await page.waitForFunction(
    () => typeof window.ensureWindow === "function" && !!document.getElementById("canvas"));
  return { context, page, errors, api };
}

async function readWin(page, index) {
  return page.evaluate((i) => {
    const el = Array.from(document.querySelectorAll(".win"))[i || 0];
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      l: el.offsetLeft, t: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight,
      z: Number(el.style.zIndex), cls: el.className,
      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
    };
  }, index || 0);
}

async function setWinPos(page, left, top) {
  await page.evaluate(([l, t]) => {
    const el = document.querySelector(".win");
    el.style.left = l + "px";
    el.style.top = t + "px";
  }, [left, top]);
}

async function mouseDrag(page, from, delta) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + delta.x, from.y + delta.y, { steps: 2 });
  await page.mouse.up();
}

async function headBox(page) {
  const b = await page.locator(".win .win-head").first().boundingBox();
  assert.ok(b, "window head has a box");
  return b;
}

async function canvasRect(page) {
  return page.evaluate(() => {
    const r = document.getElementById("canvas").getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  });
}

async function ensureOneWindow(page) {
  await page.evaluate(() => { ensureWindow("a", ""); });
  const info = await readWin(page, 0);
  assert.ok(info, "window created");
  return info;
}

// --------------------------------------------------------------------- cases

const CASES = [
  { id: "D1", fn: async (page) => {
    const before = await ensureOneWindow(page);
    const b = await headBox(page);
    await mouseDrag(page, { x: b.x + 30, y: b.y + 10 }, { x: 20, y: 10 });
    const after = await readWin(page, 0);
    assert.strictEqual(after.l, before.l + 20, "left follows the pointer delta");
    assert.strictEqual(after.t, before.t + 10, "top follows the pointer delta");
  } },
  { id: "D2", fn: async (page) => {
    await ensureOneWindow(page);
    const c = await canvasRect(page);
    const b = await headBox(page);
    await page.mouse.move(b.x + 30, b.y + 10);
    await page.mouse.down();
    await page.mouse.move(c.x + 2, c.y + 2, { steps: 2 });
    // Read BEFORE pointerup: the live move must clamp, not only the up-nudge.
    const during = await readWin(page, 0);
    assert.strictEqual(during.l, 0, "left stops at 0 during the drag");
    assert.strictEqual(during.t, 0, "top stops at 0 during the drag");
    await page.mouse.up();
    const after = await readWin(page, 0);
    assert.strictEqual(after.l, 0, "left stays at 0 after pointerup");
    assert.strictEqual(after.t, 0, "top stays at 0 after pointerup");
  } },
  { id: "D3", opts: { viewport: { width: 1000, height: 700 } }, fn: async (page) => {
    await ensureOneWindow(page);
    await setWinPos(page, 500, 20);
    const scrolled = await page.evaluate(() => {
      const c = document.getElementById("canvas");
      c.scrollLeft = 100;
      return { sl: c.scrollLeft, sw: c.scrollWidth, cw: c.clientWidth };
    });
    assert.ok(scrolled.sl >= 50, "canvas really scrolled (sl=" + scrolled.sl + ")");
    const before = await readWin(page, 0);
    const b = await headBox(page);
    await mouseDrag(page, { x: b.x + 30, y: b.y + 10 }, { x: 20, y: 0 });
    const after = await readWin(page, 0);
    assert.strictEqual(after.l, before.l + 20, "scroll must not change the drag delta");
  } },
  { id: "D4", fn: async (page) => {
    await ensureOneWindow(page);
    const b = await headBox(page);
    await mouseDrag(page, { x: b.x + 30, y: b.y + 10 }, { x: 20, y: 10 });
    const afterDrag = await readWin(page, 0);
    await page.mouse.move(b.x + 300, b.y + 200);
    const afterMove = await readWin(page, 0);
    assert.strictEqual(afterMove.l, afterDrag.l, "no move after pointerup");
    const dragging = await page.evaluate(() => document.body.classList.contains("dragging"));
    assert.strictEqual(dragging, false, "body.dragging is cleared on pointerup");
  } },
  { id: "D5", fn: async (page) => {
    const before = await ensureOneWindow(page);
    const btn = await page.locator('.win [data-a="min"]').first().boundingBox();
    assert.ok(btn, "min button has a box");
    await mouseDrag(page, { x: btn.x + btn.width / 2, y: btn.y + btn.height / 2 },
      { x: 50, y: 30 });
    const after = await readWin(page, 0);
    assert.strictEqual(after.l, before.l, "window buttons must not start a drag");
  } },

  { id: "R1:n", fn: async (page) => resizeCase(page, "n", { x: -40, y: -30 }) },
  { id: "R1:s", fn: async (page) => resizeCase(page, "s", { x: 40, y: 30 }) },
  { id: "R1:e", fn: async (page) => resizeCase(page, "e", { x: 40, y: 30 }) },
  { id: "R1:w", fn: async (page) => resizeCase(page, "w", { x: -40, y: -30 }) },
  { id: "R1:ne", fn: async (page) => resizeCase(page, "ne", { x: 40, y: -30 }) },
  { id: "R1:nw", fn: async (page) => resizeCase(page, "nw", { x: -40, y: -30 }) },
  { id: "R1:se", fn: async (page) => resizeCase(page, "se", { x: 40, y: 30 }) },
  { id: "R1:sw", fn: async (page) => resizeCase(page, "sw", { x: -40, y: 30 }) },
  { id: "R2:w", fn: async (page) => resizeMinCase(page, "w", { x: 400, y: 0 }) },
  { id: "R2:n", fn: async (page) => resizeMinCase(page, "n", { x: 0, y: 400 }) },

  { id: "F1", fn: async (page) => {
    await page.evaluate(() => { ensureWindow("a", ""); ensureWindow("b", ""); });
    const click = async (i) => {
      await page.locator(".win").nth(i).locator(".win-body").click({ position: { x: 40, y: 40 } });
    };
    await click(0);
    let a = await readWin(page, 0), b = await readWin(page, 1);
    assert.ok(a.z > b.z, "click A raises A above B");
    assert.strictEqual(await focusedCount(page), 1, "exactly one focused window");
    assert.ok(a.cls.includes("focused") && !b.cls.includes("focused"), "focus follows A");
    await click(1);
    a = await readWin(page, 0); b = await readWin(page, 1);
    assert.ok(b.z > a.z, "click B raises B above A");
    assert.ok(b.cls.includes("focused") && !a.cls.includes("focused"), "focus follows B");
    await click(0);
    a = await readWin(page, 0); b = await readWin(page, 1);
    assert.ok(a.z > b.z, "click A raises it again");
    assert.strictEqual(await focusedCount(page), 1, "still exactly one focused window");
  } },

  { id: "F2", fn: async (page) => {
    await page.evaluate(() => { ensureWindow("a", ""); ensureWindow("b", ""); });
    const before = await readWin(page, 0);
    const other = await readWin(page, 1);
    assert.ok(other.z > before.z, "B starts above A");
    const h = await page.locator(".win").nth(0).locator(".rz-se").first().boundingBox();
    await mouseDrag(page, { x: h.x + h.width / 2, y: h.y + h.height / 2 }, { x: 5, y: 5 });
    const after = await readWin(page, 0);
    assert.ok(after.z > other.z, "resizing A raises it above B");
    assert.strictEqual(after.z, other.z + 1, "resize raises exactly one level");
    assert.ok(after.cls.includes("focused") && !other.cls.includes("focused"),
      "resize also moves the focused class");
  } },

  { id: "P1", fn: async (page) => {
    await page.evaluate(() => mountPerm(
      { agent_id: "a1", tool_call_id: "tc1", options: ["allow-once", "reject-once"] }, "run-7"));
    const s = await page.evaluate(() => {
      const w = document.querySelector(".win");
      const host = w && w.querySelector('[data-pane="chat"] .perm-host');
      return {
        wins: document.querySelectorAll(".win").length,
        inChat: !!host,
        inTrace: !!(w && w.querySelector('.win-trace .perm-host')),
        btns: host ? host.querySelectorAll("[data-perm-opt]").length : 0,
        runs: host ? Array.from(host.querySelectorAll("[data-perm-run]"))
          .map((b) => b.dataset.permRun) : [],
        hosts: document.querySelectorAll(".perm-host").length,
      };
    });
    assert.strictEqual(s.wins, 1, "one agent window");
    assert.strictEqual(s.inChat, true, "card is inside the chat pane");
    assert.strictEqual(s.inTrace, false, "card is not in the hidden trace pane");
    assert.strictEqual(s.btns, 2, "only server options become buttons");
    assert.deepStrictEqual(s.runs, ["run-7", "run-7"], "run id is carried by every button");
    assert.strictEqual(s.hosts, 1, "exactly one perm host");
  } },
  { id: "P2", fn: async (page, api) => {
    await page.evaluate(() => mountPerm(
      { agent_id: "a1", tool_call_id: "tc1", options: ["allow-once", "reject-once"] }, "run-7"));
    assert.strictEqual(api.posts.length, 0, "no POST before the click");
    const reqP = page.waitForRequest((r) => r.method() === "POST" && r.url().includes("/permission"));
    await page.locator('[data-perm-opt="allow-once"]').first().click();
    const req = await reqP;
    await page.waitForFunction(() => {
      const h = document.querySelector(".perm-host");
      return h && h.innerHTML === "";
    });
    assert.strictEqual(api.posts.length, 1, "exactly one POST");
    assert.strictEqual(req.url(), "http://dsh.probe/api/runs/run-7/permission", "exact endpoint");
    assert.deepStrictEqual(req.postDataJSON(),
      { option_id: "allow-once", tool_call_id: "tc1" }, "exact body incl. toolCallId");
    assert.ok(String(api.posts[0].headers["content-type"] || "").includes("application/json"),
      "JSON content type");
  } },
  { id: "P3", opts: { permissionStatus: 422 }, fn: async (page) => {
    await page.evaluate(() => mountPerm(
      { agent_id: "a1", tool_call_id: "tc1", options: ["allow-once", "reject-once"] }, "run-7"));
    await page.locator('[data-perm-opt="allow-once"]').first().click();
    await page.waitForFunction(() =>
      document.getElementById("dc-events").textContent.includes("refused: 422"));
    const s = await page.evaluate(() => {
      const h = document.querySelector(".perm-host");
      const err = h && h.querySelector(".perm-error");
      return {
        btns: h ? h.querySelectorAll("[data-perm-opt]").length : 0,
        err: err ? err.textContent : "",
      };
    });
    assert.ok(s.btns >= 1, "a retryable 422 keeps the card");
    assert.ok(s.err.includes("422"), "the card shows the HTTP status");
  } },

  { id: "P7", opts: { permissionStatus: 409 }, fn: async (page) => {
    await page.evaluate(() => {
      mountPerm({ agent_id: "a1", tool_call_id: "tc1", options: ["allow-once"] }, "run-1");
      mountPerm({ agent_id: "a2", tool_call_id: "tc2", options: ["allow-once"] }, "run-2");
    });
    await page.locator('[data-perm-tc="tc1"]').first().click();
    await page.waitForFunction(() => {
      const h = Array.from(document.querySelectorAll(".perm-host"))
        .find((x) => x.dataset.toolCall === "tc1");
      return h && h.innerHTML === "";
    });
    const s = await page.evaluate(() => {
      const out = {};
      for (const w of document.querySelectorAll(".win")) {
        const title = (w.querySelector(".win-title") || {}).textContent || "";
        const h = w.querySelector(".perm-host");
        out[title] = h ? h.querySelectorAll("[data-perm-opt]").length : 0;
      }
      return out;
    });
    assert.strictEqual(s.a1, 0, "stale 409 clears the answered card");
    assert.strictEqual(s.a2, 1, "another agent's card survives a 409");
  } },
  { id: "P8", fn: async (page) => {
    await page.evaluate(() => {
      mountPerm({ agent_id: "a1", tool_call_id: "tc1", options: ["allow-once"] }, "run-1");
      mountPerm({ agent_id: "a2", tool_call_id: "tc2", options: ["allow-once"] }, "run-2");
      expirePerm("nope");
    });
    const s = await page.evaluate(() => {
      const out = {};
      for (const w of document.querySelectorAll(".win")) {
        const title = (w.querySelector(".win-title") || {}).textContent || "";
        const h = w.querySelector(".perm-host");
        out[title] = {
          btns: h ? h.querySelectorAll("[data-perm-opt]").length : 0,
          text: h ? h.textContent : "",
        };
      }
      return out;
    });
    assert.strictEqual(s.a1.btns, 1, "unknown expiry must not wipe a1's card");
    assert.strictEqual(s.a2.btns, 1, "unknown expiry must not wipe a2's card");
    assert.ok(!s.a1.text.includes("已超时"), "a1 is not marked expired");
  } },
  { id: "P4", fn: async (page) => {
    await page.evaluate(() => {
      mountPerm({ agent_id: "a1", tool_call_id: "tc1", options: ["allow-once"] }, "run-1");
      mountPerm({ agent_id: "a2", tool_call_id: "tc2", options: ["allow-once"] }, "run-2");
      expirePerm("tc1");
    });
    const s = await page.evaluate(() => {
      const out = {};
      for (const w of document.querySelectorAll(".win")) {
        const title = (w.querySelector(".win-title") || {}).textContent || "";
        const host = w.querySelector(".perm-host");
        out[title] = {
          text: host ? host.textContent : "",
          btns: host ? host.querySelectorAll("[data-perm-opt]").length : 0,
        };
      }
      return out;
    });
    assert.ok(s.a1 && s.a1.text.includes("已超时"), "timed-out card says so");
    assert.strictEqual(s.a1.btns, 0, "timed-out card has no buttons");
    assert.ok(s.a2 && !s.a2.text.includes("已超时"), "other agent's card is untouched");
    assert.strictEqual(s.a2.btns, 1, "other agent's card keeps its buttons");
  } },

  { id: "DC1", fn: async (page) => {
    const opened = await page.evaluate(() => {
      dcToggle();
      return document.getElementById("dc-panel").hidden;
    });
    assert.strictEqual(opened, false, "dispatch panel is open");
    await page.evaluate(() => {
      document.querySelector('button[onclick*="dcConnect"]').click();
    });
    await page.waitForTimeout(200);
    const text = await page.evaluate(() => document.getElementById("dc-events").textContent);
    assert.ok(text.includes("no run_id"), "empty dispatch input is loudly refused");
  } },

  { id: "P5", fn: async (page) => {
    await page.evaluate(() => {
      routeEvent({ type: "spi_permission_request", run_id: "run-9",
        payload: { agent_id: "a1", tool_call_id: "tc9", options: ["allow-once"] } });
    });
    const s = await page.evaluate(() => {
      const w = document.querySelector(".win");
      const host = w && w.querySelector('[data-pane="chat"] .perm-host');
      return {
        hosts: document.querySelectorAll(".perm-host").length,
        btns: host ? host.querySelectorAll("[data-perm-opt]").length : 0,
        tc: host ? host.dataset.toolCall : "",
      };
    });
    assert.strictEqual(s.hosts, 1, "the live event mounts exactly one card");
    assert.strictEqual(s.btns, 1, "the card offers the server option");
    assert.strictEqual(s.tc, "tc9", "the card carries the toolCallId");
  } },

  { id: "P6", fn: async (page) => {
    await page.evaluate(() => {
      routeEvent({ type: "spi_permission_request", run_id: "run-1",
        payload: { agent_id: "a1", tool_call_id: "tc1", options: ["allow-once"] } });
      routeEvent({ type: "spi_permission_request", run_id: "run-2",
        payload: { agent_id: "a2", tool_call_id: "tc2", options: ["allow-once"] } });
      routeEvent({ type: "permission_answered", run_id: "run-1",
        payload: { agent_id: "a1", tool_call_id: "tc1", option_id: "allow-once" } });
    });
    const s = await page.evaluate(() => {
      const out = {};
      for (const w of document.querySelectorAll(".win")) {
        const title = (w.querySelector(".win-title") || {}).textContent || "";
        const host = w.querySelector(".perm-host");
        out[title] = host ? host.innerHTML : "<missing>";
      }
      return out;
    });
    assert.strictEqual(s.a1, "", "answered clears the matching card");
    assert.ok(s.a2 && s.a2.includes("data-perm-opt"), "another agent's card survives");
  } },

  { id: "L1", fn: async (page) => {
    await ensureOneWindow(page);
    const c = await page.evaluate(() => {
      const e = document.getElementById("canvas");
      const r = e.getBoundingClientRect();
      return { sl: e.scrollLeft, cw: e.clientWidth, cx: r.x };
    });
    const b = await headBox(page);
    await mouseDrag(page, { x: b.x + 30, y: b.y + 10 },
      { x: (c.cx + c.cw - 5) - (b.x + 30), y: 0 });
    let w = await readWin(page, 0);
    let vis = Math.min(w.l + w.w, c.sl + c.cw) - Math.max(w.l, c.sl);
    assert.ok(vis >= 79, "horizontal visible width >= 80 (got " + vis + ")");
    const first = w.l;
    const b2 = await headBox(page);
    await mouseDrag(page, { x: b2.x + 30, y: b2.y + 10 },
      { x: (c.cx + c.cw - 5) - (b2.x + 30), y: 0 });
    w = await readWin(page, 0);
    assert.strictEqual(w.l, first, "second drag must not ratchet the bound");
  } },

  { id: "L2", fn: async (page) => {
    await ensureOneWindow(page);
    const c = await page.evaluate(() => {
      const e = document.getElementById("canvas");
      const r = e.getBoundingClientRect();
      return { st: e.scrollTop, ch: e.clientHeight, cy: r.y };
    });
    const b = await headBox(page);
    await mouseDrag(page, { x: b.x + 30, y: b.y + 10 },
      { x: 0, y: (c.cy + c.ch - 5) - (b.y + 10) });
    let w = await readWin(page, 0);
    let vis = Math.min(w.t + w.h, c.st + c.ch) - Math.max(w.t, c.st);
    assert.ok(vis >= 39, "vertical visible height >= 40 (got " + vis + ")");
    const first = w.t;
    const b2 = await headBox(page);
    await mouseDrag(page, { x: b2.x + 30, y: b2.y + 10 },
      { x: 0, y: (c.cy + c.ch - 5) - (b2.y + 10) });
    w = await readWin(page, 0);
    assert.strictEqual(w.t, first, "second drag must not ratchet the bound");
  } },

  { id: "C1", fn: async (page) => {
    await ensureOneWindow(page);
    const b = await headBox(page);
    await page.mouse.move(b.x + 30, b.y + 10);
    await page.mouse.down();
    await page.mouse.move(b.x + 60, b.y + 30, { steps: 2 });
    const mid = await readWin(page, 0);
    await page.evaluate(() => {
      document.querySelector(".win").dispatchEvent(
        new PointerEvent("pointercancel", { bubbles: true }));
    });
    await page.mouse.move(b.x + 300, b.y + 200, { steps: 2 });
    const after = await readWin(page, 0);
    assert.strictEqual(after.l, mid.l, "pointercancel ends the drag");
    assert.strictEqual(after.t, mid.t, "pointercancel ends the drag (y)");
    const dragging = await page.evaluate(() => document.body.classList.contains("dragging"));
    assert.strictEqual(dragging, false, "body.dragging cleared on pointercancel");
    await page.mouse.up();
  } },

  { id: "C2", fn: async (page) => {
    await ensureOneWindow(page);
    await setWinPos(page, 200, 200);
    const h = await page.locator(".win .rz-se").first().boundingBox();
    await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2);
    await page.mouse.down();
    await page.mouse.move(h.x + h.width / 2 + 20, h.y + h.height / 2 + 20, { steps: 2 });
    const mid = await readWin(page, 0);
    await page.evaluate(() => {
      document.querySelector(".win").dispatchEvent(
        new PointerEvent("pointercancel", { bubbles: true }));
    });
    await page.mouse.move(h.x + h.width / 2 + 120, h.y + h.height / 2 + 120, { steps: 2 });
    const after = await readWin(page, 0);
    assert.strictEqual(after.w, mid.w, "pointercancel ends resize (width)");
    assert.strictEqual(after.h, mid.h, "pointercancel ends resize (height)");
    const dragging = await page.evaluate(() => document.body.classList.contains("dragging"));
    assert.strictEqual(dragging, false, "body.dragging cleared on resize cancel");
    await page.mouse.up();
  } },

  { id: "C3", fn: async (page) => {
    const sb = page.locator("#sb-splitter");
    const b = await sb.boundingBox();
    await page.mouse.move(b.x + b.width / 2, b.y + 20);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 + 30, b.y + 20, { steps: 2 });
    const mid = await page.evaluate(() => document.getElementById("sidebar").offsetWidth);
    await page.evaluate(() => {
      document.getElementById("sb-splitter").dispatchEvent(
        new PointerEvent("pointercancel", { bubbles: true }));
    });
    await page.mouse.move(b.x + b.width / 2 + 180, b.y + 20, { steps: 2 });
    const after = await page.evaluate(() => document.getElementById("sidebar").offsetWidth);
    assert.strictEqual(after, mid, "pointercancel ends the splitter drag");
    const dragging = await page.evaluate(() => document.body.classList.contains("dragging"));
    assert.strictEqual(dragging, false, "body.dragging cleared on splitter cancel");
    await page.mouse.up();
  } },
];

async function resizeCase(page, dir, delta) {
  await ensureOneWindow(page);
  await setWinPos(page, 200, 200);
  const before = await readWin(page, 0);
  const h = await page.locator(".win .rz-" + dir).first().boundingBox();
  assert.ok(h, "resize handle " + dir + " has a box");
  await mouseDrag(page, { x: h.x + h.width / 2, y: h.y + h.height / 2 }, delta);
  const after = await readWin(page, 0);
  const hasW = dir.includes("w"), hasE = dir.includes("e");
  const hasN = dir.includes("n"), hasS = dir.includes("s");
  if (hasW) assert.strictEqual(after.l + after.w, before.l + before.w, dir + ": right edge fixed");
  if (hasE) assert.strictEqual(after.l, before.l, dir + ": left edge fixed");
  if (hasN) assert.strictEqual(after.t + after.h, before.t + before.h, dir + ": bottom edge fixed");
  if (hasS) assert.strictEqual(after.t, before.t, dir + ": top edge fixed");
  if (!hasW && !hasE) {
    assert.strictEqual(after.l, before.l, dir + ": x untouched");
    assert.strictEqual(after.w, before.w, dir + ": width untouched");
  }
  if (!hasN && !hasS) {
    assert.strictEqual(after.t, before.t, dir + ": y untouched");
    assert.strictEqual(after.h, before.h, dir + ": height untouched");
  }
  if (hasE) assert.strictEqual(after.w, before.w + 40, dir + ": width grew by dx");
  if (hasW) assert.strictEqual(after.w, before.w + 40, dir + ": width grew by |dx|");
  if (hasS) assert.strictEqual(after.h, before.h + 30, dir + ": height grew by dy");
  if (hasN) assert.strictEqual(after.h, before.h + 30, dir + ": height grew by |dy|");
}

async function resizeMinCase(page, dir, delta) {
  await ensureOneWindow(page);
  await setWinPos(page, 500, 100);
  const before = await readWin(page, 0);
  const h = await page.locator(".win .rz-" + dir).first().boundingBox();
  assert.ok(h, "resize handle " + dir + " has a box");
  await mouseDrag(page, { x: h.x + h.width / 2, y: h.y + h.height / 2 }, delta);
  const after = await readWin(page, 0);
  if (dir === "w") {
    assert.strictEqual(after.w, 220, "min width on the w axis");
    assert.strictEqual(after.l + after.w, before.l + before.w, "right edge fixed at min width");
  } else {
    assert.strictEqual(after.h, 120, "min height on the n axis");
    assert.strictEqual(after.t + after.h, before.t + before.h, "bottom edge fixed at min height");
  }
}

async function focusedCount(page) {
  return page.evaluate(() => document.querySelectorAll(".win.focused").length);
}

// ----------------------------------------------------------------------- main

async function main() {
  if (process.argv.includes("--list")) {
    console.log(CASES.map((c) => c.id).join("\n"));
    return 0;
  }
  if (!fs.existsSync(path.join(staticDir, "index.html"))) {
    console.error("static root has no index.html: " + staticDir);
    return 2;
  }
  let chromium;
  try { ({ chromium } = loadPlaywright()); } catch (e) { console.error(e.message); return 2; }
  let browser;
  try { browser = await launchBrowser(chromium); }
  catch (e) { console.error(e.message); return 2; }

  const selected = CASES.filter((c) => !only.length || only.includes(c.id));
  const results = {};
  const t0 = Date.now();
  for (const c of selected) {
    const s = Date.now();
    let ctx = null;
    let ok = true, detail = "";
    try {
      const opened = await openPage(browser, c.opts || {});
      ctx = opened.context;
      try {
        await c.fn(opened.page, opened.api);
      } finally {
        if (opened.errors.length) throw new Error("pageerror: " + opened.errors.join(" | "));
      }
    } catch (e) {
      ok = false;
      detail = String((e && e.stack) || e).split("\n").slice(0, 4).join(" | ");
    } finally {
      if (ctx) { try { await ctx.close(); } catch (e) {} }
    }
    results[c.id] = { ok, detail, ms: Date.now() - s };
    console.log((ok ? "PASS " : "FAIL ") + c.id + (ok ? "" : "  << " + detail));
  }
  await browser.close();
  const failed = Object.keys(results).filter((k) => !results[k].ok);
  const out = {
    staticDir, browser: "system", totalMs: Date.now() - t0,
    cases: results, pass: selected.length - failed.length, of: selected.length,
  };
  if (jsonOut) fs.writeFileSync(jsonOut, JSON.stringify(out, null, 1), "utf8");
  console.log("RESULT " + out.pass + "/" + out.of + " PASS " + out.totalMs + "ms");
  if (failed.length) console.log("FAILED: " + failed.join(", "));
  return failed.length ? 1 : 0;
}

main().then((code) => { process.exitCode = code; }).catch((e) => {
  console.error("RUNNER ERROR: " + String((e && e.stack) || e));
  process.exitCode = 2;
});

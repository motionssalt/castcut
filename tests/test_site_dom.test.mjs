import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, "../web");

test("Web DOM Integrity & Mobile UI Guardrails", async (t) => {
  const indexHtml = fs.readFileSync(path.join(webDir, "index.html"), "utf8");
  const stylesCss = fs.readFileSync(path.join(webDir, "styles.css"), "utf8");

  await t.test("styles.css enforces [hidden] display:none !important", () => {
    assert.ok(
      stylesCss.includes("[hidden]{display:none!important}") ||
      stylesCss.includes("[hidden] { display: none !important; }") ||
      stylesCss.includes("[hidden] {\n  display: none !important;\n}"),
      "styles.css must enforce [hidden] with !important to prevent flex/grid display overrides"
    );
  });

  await t.test("index.html contains mobile viewport and auth containers", () => {
    assert.ok(indexHtml.includes('viewport-fit=cover'), "Viewport must include viewport-fit=cover for mobile notches");
    assert.ok(indexHtml.includes('id="authGate"'), "index.html must have #authGate container");
    assert.ok(indexHtml.includes('id="appContent"'), "index.html must have #appContent container");
    assert.ok(indexHtml.includes('id="projectsList"'), "index.html must have #projectsList container");
    assert.ok(indexHtml.includes('id="btnLogout"'), "index.html must have #btnLogout");
  });

  await t.test("core.js auth and badge functions execute as expected in node sandbox", async () => {
    const core = await import("../web/core.js");
    
    // Test badge generation
    const bReady = core.getStatusBadge("ready");
    assert.ok(bReady.label.includes("Ready"), "Ready state badge must display Ready");
    
    const bSel = core.getStatusBadge("selecting");
    assert.ok(bSel.label.includes("Listing Torrent"), "Selecting state badge must display Listing Torrent");
    
    const bAwait = core.getStatusBadge("awaiting_selection");
    assert.ok(bAwait.label.includes("Pick Files"), "Awaiting selection badge must display Pick Files");
    
    const bFailed = core.getStatusBadge("failed");
    assert.ok(bFailed.label.includes("Failed"), "Failed badge must display Failed");
  });
});

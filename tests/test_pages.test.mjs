import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webDir = path.resolve(__dirname, "../web");

test("Multi-Page Structure and Web Core Runtime", async (t) => {
  await t.test("All 7 separate page HTML documents and scripts exist and are non-empty", () => {
    const requiredPages = [
      { html: "index.html", js: "projects.js" },
      { html: "new/index.html", js: "new.js" },
      { html: "project/index.html", js: "project.js" },
      { html: "faces/index.html", js: "faces.js" },
      { html: "scene/index.html", js: "scene.js" },
      { html: "exports/index.html", js: "exports.js" },
      { html: "task/index.html", js: "task.js" },
    ];

    for (const p of requiredPages) {
      const htmlPath = path.join(webDir, p.html);
      const jsPath = path.join(webDir, p.js);
      assert.ok(fs.existsSync(htmlPath), `HTML missing: ${p.html}`);
      assert.ok(fs.existsSync(jsPath), `JS missing: ${p.js}`);
      const htmlContent = fs.readFileSync(htmlPath, "utf8");
      const jsContent = fs.readFileSync(jsPath, "utf8");
      assert.ok(htmlContent.length > 50, `${p.html} is too short`);
      assert.ok(jsContent.length > 50, `${p.js} is too short`);
    }
  });

  await t.test("tolerantParseJson handles fences, text, smart quotes, trailing commas, and BOM", async () => {
    const { tolerantParseJson } = await import("../web/core.js");

    // 1. Raw JSON
    const r1 = tolerantParseJson('{"version":1,"segments":[{"start":1,"end":2}]}');
    assert.ok(r1.ok, "Raw JSON should parse");
    assert.equal(r1.data.segments.length, 1);

    // 2. Fences + surrounding prose
    const r2 = tolerantParseJson(`Hello editor!\n\`\`\`json\n{"title":"Cut 1","segments":[{"start":10.5,"end":20.0,"note":"battle"}]}\n\`\`\`\nHope this works!`);
    assert.ok(r2.ok, "Fenced JSON with prose should parse");
    assert.equal(r2.data.title, "Cut 1");

    // 3. Smart quotes and trailing commas
    const r3 = tolerantParseJson(`{\n  “title”: “Speech Scene”,\n  “segments”: [\n    {“start”: 5, “end”: 12,},\n  ],\n}`);
    assert.ok(r3.ok, "Smart quotes and trailing commas should parse");
    assert.equal(r3.data.title, "Speech Scene");

    // 4. BOM
    const r4 = tolerantParseJson("\uFEFF" + '{"segments":[{"start":0,"end":5}]}');
    assert.ok(r4.ok, "BOM-prefixed JSON should parse");

    // 5. Malformed JSON returns ok: false
    const r5 = tolerantParseJson('{"broken": [1, 2, }');
    assert.equal(r5.ok, false);
    assert.ok(r5.error, "Must return descriptive syntax error");
  });

  await t.test("validateSceneProduction handles MM:SS timestamps and catches errors", async () => {
    const { validateSceneProduction } = await import("../web/core.js");

    // Valid with mixed seconds and MM:SS
    const val1 = validateSceneProduction({
      title: "Climax",
      segments: [
        { start: "0:15", end: "0:45.5", note: "intro" },
        { start: 60.0, end: 95.0, note: "climax" }
      ]
    }, 120.0);
    assert.ok(val1.ok, "Valid timestamps should pass");
    assert.equal(val1.count, 2);
    assert.equal(val1.totalDuration, 30.5 + 35.0);

    // Invalid: start >= end
    const val2 = validateSceneProduction({
      segments: [{ start: 20, end: 15 }]
    }, 100);
    assert.equal(val2.ok, false);
    assert.ok(val2.errors[0].includes("must be after start") || val2.errors[0].includes("greater than start"));

    // Invalid: out of duration range
    const val3 = validateSceneProduction({
      segments: [{ start: 150, end: 160 }]
    }, 100);
    assert.equal(val3.ok, false);
    assert.ok(val3.errors[0].includes("exceeds source duration") || val3.errors[0].includes("beyond source duration"));
  });

  await t.test("formatBytes and formatSeconds format human readable units correctly", async () => {
    const { formatBytes, formatSeconds } = await import("../web/core.js");
    assert.equal(formatBytes(0), "0 B");
    assert.equal(formatBytes(1024), "1.0 KB");
    assert.equal(formatBytes(1048576), "1.0 MB");
    assert.equal(formatBytes(1073741824), "1.0 GB");

    assert.equal(formatSeconds(45), "45.0s");
    assert.equal(formatSeconds(90), "1m 30.0s");
    assert.equal(formatSeconds(3665), "1h 1m 5.0s");
  });

  await t.test("Workflow dispatch contract: every workflow dispatched by web/functions exists and matches inputs", () => {
    const workflowsDir = path.resolve(__dirname, "../.github/workflows");
    const functionsDir = path.resolve(__dirname, "../web/functions");
    
    function scanDir(dir) {
      let results = [];
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          results = results.concat(scanDir(full));
        } else if (entry.name.endsWith(".js")) {
          results.push(full);
        }
      }
      return results;
    }

    const jsFiles = scanDir(functionsDir);
    const dispatchRegex = /dispatchWorkflow\s*\(\s*[^,]+,\s*["'`]([^"'`]+)["'`]\s*,\s*(\{[\s\S]*?\})\s*\)/g;

    const dispatches = [];
    for (const file of jsFiles) {
      const code = fs.readFileSync(file, "utf8");
      let m;
      while ((m = dispatchRegex.exec(code)) !== null) {
        dispatches.push({
          file: path.relative(webDir, file),
          workflow: m[1],
        });
      }
    }

    assert.ok(dispatches.length >= 6, `Expected at least 6 dispatchWorkflow calls, found ${dispatches.length}`);
    for (const d of dispatches) {
      const wfPath = path.join(workflowsDir, d.workflow);
      assert.ok(fs.existsSync(wfPath), `Workflow file ${d.workflow} dispatched by ${d.file} does not exist in .github/workflows/`);
    }
  });
});

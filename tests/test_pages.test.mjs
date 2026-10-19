import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const siteDir = path.resolve(__dirname, "../site");

test("Multi-Page Structure and Common Core", async (t) => {
  const commonJs = fs.readFileSync(path.join(siteDir, "common.js"), "utf8");

  await t.test("All 6 separate page HTML documents and scripts exist and are non-empty", () => {
    const requiredPages = [
      { html: "index.html", js: "app.js" },
      { html: "new/index.html", js: "new/new.js" },
      { html: "project/index.html", js: "project/project.js" },
      { html: "faces/index.html", js: "faces/faces.js" },
      { html: "scene/index.html", js: "scene/scene.js" },
      { html: "exports/index.html", js: "exports/exports.js" },
      { html: "task/index.html", js: "task/task.js" },
    ];

    for (const p of requiredPages) {
      const htmlPath = path.join(siteDir, p.html);
      const jsPath = path.join(siteDir, p.js);
      assert.ok(fs.existsSync(htmlPath), `HTML missing: ${p.html}`);
      assert.ok(fs.existsSync(jsPath), `JS missing: ${p.js}`);
      const htmlContent = fs.readFileSync(htmlPath, "utf8");
      const jsContent = fs.readFileSync(jsPath, "utf8");
      assert.ok(htmlContent.length > 50, `${p.html} is too short`);
      assert.ok(jsContent.length > 50, `${p.js} is too short`);
      assert.ok(htmlContent.includes("/common.js"), `${p.html} must load /common.js`);
    }
  });

  await t.test("Issue 3: cleanAndParseJson handles fences, text, smart quotes, trailing commas, and BOM", () => {
    const sandbox = {};
    const fn = new Function("window", "document", "localStorage", "fetch", "location", commonJs + "\nreturn { cleanAndParseJson, validateProduction, humanSize, formatSec };");
    const { cleanAndParseJson, validateProduction } = fn(sandbox, {}, {}, {}, {});

    // 1. Raw JSON
    const r1 = cleanAndParseJson('{"version":1,"segments":[{"start":1,"end":2}]}');
    assert.ok(r1.ok, "Raw JSON should parse");
    assert.equal(r1.data.segments.length, 1);

    // 2. Fences + surrounding prose
    const r2 = cleanAndParseJson(`Hello editor!\n\`\`\`json\n{"title":"Cut 1","segments":[{"start":10.5,"end":20.0,"note":"battle"}]}\n\`\`\`\nHope this works!`);
    assert.ok(r2.ok, "Fenced JSON with prose should parse");
    assert.equal(r2.data.title, "Cut 1");

    // 3. Smart quotes and trailing commas
    const r3 = cleanAndParseJson(`{\n  “title”: “Speech Scene”,\n  “segments”: [\n    {“start”: 5, “end”: 12,},\n  ],\n}`);
    assert.ok(r3.ok, "Smart quotes and trailing commas should parse");
    assert.equal(r3.data.title, "Speech Scene");

    // 4. BOM
    const r4 = cleanAndParseJson("\uFEFF" + '{"segments":[{"start":0,"end":5}]}');
    assert.ok(r4.ok, "BOM-prefixed JSON should parse");

    // 5. Malformed JSON provides precise error message
    const r5 = cleanAndParseJson('{"broken": [1, 2, }');
    assert.equal(r5.ok, false);
    assert.ok(r5.error.includes("syntax error") || r5.error.includes("JSON"), "Must return descriptive syntax error");
  });

  await t.test("Issue 3/4: validateProduction handles MM:SS timestamps and catches errors", () => {
    const sandbox = {};
    const fn = new Function("window", "document", "localStorage", "fetch", "location", commonJs + "\nreturn { validateProduction };");
    const { validateProduction } = fn(sandbox, {}, {}, {}, {});

    // Valid with mixed seconds and MM:SS
    const val1 = validateProduction({
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
    const val2 = validateProduction({
      segments: [{ start: 20, end: 15 }]
    }, 100);
    assert.equal(val2.ok, false);
    assert.ok(val2.errors[0].includes("greater than start"));

    // Invalid: out of duration range
    const val3 = validateProduction({
      segments: [{ start: 150, end: 160 }]
    }, 100);
    assert.equal(val3.ok, false);
    assert.ok(val3.errors[0].includes("beyond source duration"));
  });

  await t.test("Issue 2: renderAudioTrackPicker correctly handles 0, 1, and multi tracks", () => {
    function mockDoc() {
      return {
        createElement(tag) {
          return {
            tagName: tag,
            className: "",
            innerHTML: "",
            style: {},
            children: [],
            appendChild(child) { this.children.push(child); },
            querySelector(sel) {
              if (sel === "#audioSelect") return this._select;
              if (sel === "#audioSelectedBadge") return this._badge;
              return null;
            }
          };
        }
      };
    }

    const doc = mockDoc();
    const sandbox = {};
    const fn = new Function("window", "document", "localStorage", "fetch", "location", commonJs + "\nreturn { renderAudioTrackPicker };");
    const { renderAudioTrackPicker } = fn(sandbox, doc, {}, {}, {});

    // 0 tracks
    const c0 = doc.createElement("div");
    renderAudioTrackPicker(c0, []);
    assert.ok(c0.children[0].innerHTML.includes("No audio tracks detected"));

    // 1 track
    const c1 = doc.createElement("div");
    let sel1 = null;
    renderAudioTrackPicker(c1, [{ audio_index: 0, label: "Track 1 · JA · 2.0 · AAC" }], null, (v) => { sel1 = v; });
    assert.equal(sel1, 0, "Single track should auto-select 0");

    // Multiple tracks with preselected default
    const cMulti = doc.createElement("div");
    const tracks = [
      { audio_index: 0, label: "Track 1 · JA · 5.1 · OPUS", is_default: false },
      { audio_index: 1, label: "Track 2 · JA · 2.0 · AAC", is_default: true },
      { audio_index: 2, label: "Track 3 · EN · 2.0 · AAC", is_default: false },
    ];
    renderAudioTrackPicker(cMulti, tracks, null);
    assert.ok(cMulti.children[0].innerHTML.includes("Track 2"));
  });
});

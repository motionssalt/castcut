import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const siteDir = path.resolve(__dirname, "../site");

test("Site DOM Integrity & Startup Execution", async (t) => {
  const html = fs.readFileSync(path.join(siteDir, "index.html"), "utf8");
  const appJs = fs.readFileSync(path.join(siteDir, "app.js"), "utf8");
  const stylesCss = fs.readFileSync(path.join(siteDir, "styles.css"), "utf8");

  await t.test("styles.css enforces [hidden] display:none !important", () => {
    assert.ok(
      stylesCss.includes("[hidden]{display:none!important}") ||
      stylesCss.includes("[hidden] { display: none !important; }"),
      "styles.css must enforce [hidden] with !important to prevent .btn display overrides"
    );
  });

  await t.test("app.js executes cleanly without null reference crashes when user has PAT", async () => {
    const htmlIds = new Set(Array.from(html.matchAll(/id="([^"]+)"/g)).map(m => m[1]));
    const elements = {};
    for (const id of htmlIds) {
      elements[id] = {
        id,
        hidden: true,
        textContent: "",
        value: "",
        style: {},
        classList: { add() {}, remove() {}, toggle: () => false },
        addEventListener() {},
        appendChild() {},
        querySelectorAll: () => [],
        querySelector: () => null,
      };
    }

    const mockWindow = {
      document: {
        getElementById: (id) => elements[id] || null,
        querySelectorAll: () => [],
        createElement: () => ({
          className: "",
          innerHTML: "",
          style: {},
          classList: { add() {}, remove() {}, toggle: () => false },
          querySelector: () => null,
          querySelectorAll: () => [],
          appendChild: () => {},
        }),
      },
      localStorage: {
        getItem: (k) => (k === "castcut.pat" ? "ghp_validOwnerPat1234567890" : null),
        setItem() {},
        removeItem() {},
      },
      fetch: async (url) => {
        if (url.includes("/whoami")) {
          return {
            status: 200,
            ok: true,
            text: async () => JSON.stringify({ login: "motionssalt", id: 12345 }),
          };
        }
        if (url.includes("/jobs")) {
          return {
            status: 200,
            ok: true,
            text: async () => JSON.stringify({ jobs: [] }),
          };
        }
        return {
          status: 200,
          ok: true,
          text: async () => JSON.stringify({}),
        };
      },
      location: { reload() {} },
    };

    // Execute in sandbox
    const fn = new Function(
      "window", "document", "localStorage", "fetch", "location",
      appJs
    );

    assert.doesNotThrow(() => {
      fn(mockWindow, mockWindow.document, mockWindow.localStorage, mockWindow.fetch, mockWindow.location);
    }, "Evaluating app.js with valid PAT must not throw any exception");

    // Wait a tick for async tryGate / refreshJobs
    await new Promise(r => setTimeout(r, 50));

    assert.equal(elements.gate.hidden, true, "#gate must be hidden when signed in");
    assert.equal(elements.home.hidden, false, "#home must be unhidden when signed in");
    assert.equal(elements.btnLogout.hidden, false, "#btnLogout must be visible when signed in");
    assert.equal(elements.whoami.textContent, "· motionssalt", "#whoami must display owner login");
  });

  await t.test("app.js executes cleanly without PAT (displays sign-in gate)", async () => {
    const htmlIds = new Set(Array.from(html.matchAll(/id="([^"]+)"/g)).map(m => m[1]));
    const elements = {};
    for (const id of htmlIds) {
      elements[id] = {
        id,
        hidden: true,
        textContent: "",
        value: "",
        style: {},
        classList: { add() {}, remove() {}, toggle: () => false },
        addEventListener() {},
        appendChild() {},
        querySelectorAll: () => [],
        querySelector: () => null,
      };
    }

    const mockWindow = {
      document: {
        getElementById: (id) => elements[id] || null,
        querySelectorAll: () => [],
        createElement: () => ({
          className: "",
          innerHTML: "",
          style: {},
          classList: { add() {}, remove() {}, toggle: () => false },
          querySelector: () => null,
          querySelectorAll: () => [],
          appendChild: () => {},
        }),
      },
      localStorage: {
        getItem: () => null,
        setItem() {},
        removeItem() {},
      },
      fetch: async () => ({ status: 401, ok: false, text: async () => "{}" }),
      location: { reload() {} },
    };

    const fn = new Function(
      "window", "document", "localStorage", "fetch", "location",
      appJs
    );

    assert.doesNotThrow(() => {
      fn(mockWindow, mockWindow.document, mockWindow.localStorage, mockWindow.fetch, mockWindow.location);
    }, "Evaluating app.js without PAT must not throw");

    await new Promise(r => setTimeout(r, 50));

    assert.equal(elements.gate.hidden, false, "#gate must be visible when signed out");
    assert.equal(elements.home.hidden, true, "#home must be hidden when signed out");
    assert.equal(elements.btnLogout.hidden, true, "#btnLogout must be hidden when signed out");
  });
});

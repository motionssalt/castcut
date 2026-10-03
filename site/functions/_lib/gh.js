// Shared GitHub helpers for Pages Functions. The owner's PAT (verified by
// _middleware) is forwarded for all content/dispatch calls.
export const REPO_OWNER = "motionssalt";
export const REPO_NAME = "castcut";
export const REPO = `${REPO_OWNER}/${REPO_NAME}`;

const UA = "castcut-pages/1.0";

export async function gh(pat, path, { method = "GET", body = null, raw = false, acceptRaw = false } = {}) {
  const headers = {
    Authorization: `Bearer ${pat}`,
    Accept: acceptRaw ? "application/vnd.github.raw" : "application/vnd.github+json",
    "User-Agent": UA,
  };
  if (body) headers["content-type"] = "application/json";
  const r = await fetch(`https://api.github.com${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`github ${method} ${path} -> ${r.status}: ${t.slice(0, 200)}`);
  }
  if (raw) return r;
  if (acceptRaw) return r.text();
  if (r.status === 204) return null;
  return r.json();
}

export async function readFile(pat, path) {
  try {
    const txt = await gh(pat, `/repos/${REPO}/contents/${encodeURIComponent(path).replace(/%2F/g, "/")}`, { acceptRaw: true });
    return txt;
  } catch (e) {
    if (String(e.message).includes("-> 404")) return null;
    throw e;
  }
}

export async function putFile(pat, path, content, message) {
  // Get existing sha if any
  let sha = null;
  try {
    const cur = await gh(pat, `/repos/${REPO}/contents/${encodeURIComponent(path).replace(/%2F/g, "/")}`);
    sha = cur.sha;
  } catch (e) {
    if (!String(e.message).includes("-> 404")) throw e;
  }
  const b64 = typeof content === "string"
    ? btoa(unescape(encodeURIComponent(content)))
    : btoa(String.fromCharCode(...new Uint8Array(content)));
  return gh(pat, `/repos/${REPO}/contents/${encodeURIComponent(path).replace(/%2F/g, "/")}`, {
    method: "PUT",
    body: { message, content: b64, sha: sha || undefined },
  });
}

export async function dispatchWorkflow(pat, filename, inputs) {
  return gh(pat, `/repos/${REPO}/actions/workflows/${filename}/dispatches`, {
    method: "POST",
    body: { ref: "main", inputs },
  });
}

export async function getRelease(pat, tag) {
  try {
    return await gh(pat, `/repos/${REPO}/releases/tags/${encodeURIComponent(tag)}`);
  } catch (e) {
    if (String(e.message).includes("-> 404")) return null;
    throw e;
  }
}

export async function listJobs(pat) {
  try {
    const items = await gh(pat, `/repos/${REPO}/contents/jobs`);
    if (!Array.isArray(items)) return [];
    return items.filter(x => x.type === "dir").map(x => x.name);
  } catch (e) {
    if (String(e.message).includes("-> 404")) return [];
    throw e;
  }
}

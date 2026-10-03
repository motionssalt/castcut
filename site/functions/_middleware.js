// Owner-only gate: every /api/* request must carry an x-castcut-pat that
// GitHub confirms belongs to the owner login.
const OWNER = "motionssalt";

async function verifyPat(pat) {
  if (!pat || typeof pat !== "string" || pat.length < 20) return null;
  const r = await fetch("https://api.github.com/user", {
    headers: {
      Authorization: `Bearer ${pat}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "castcut-pages",
    },
  });
  if (!r.ok) return null;
  const user = await r.json();
  if (!user || user.login !== OWNER) return null;
  return user;
}

export async function onRequest(context) {
  const { request, next } = context;
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/")) return next();
  const pat = request.headers.get("x-castcut-pat") || "";
  const user = await verifyPat(pat);
  if (!user) {
    return new Response(JSON.stringify({ error: "unauthorized" }), {
      status: 401, headers: { "content-type": "application/json" },
    });
  }
  context.data.user = user;
  context.data.pat = pat;
  return next();
}

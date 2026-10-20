// Owner-only gate. Native media GET/HEAD may use an encrypted, scoped cookie;
// every other API call still requires the PAT header. GitHub verifies both.
import { readTicket } from './_lib/download-ticket.js';
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
  let pat = request.headers.get("x-castcut-pat") || "";
  const media = url.pathname.match(/^\/api\/release-assets\/(\d+)$/);
  if (!pat && media && ['GET', 'HEAD'].includes(request.method) && url.searchParams.get('download') === '1') {
    pat = await readTicket(context.env, request, media[1]) || '';
  }
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

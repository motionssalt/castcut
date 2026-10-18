import { deleteFile, gh, REPO, esc } from "../../../../_lib/gh.js";

export async function onRequest({ request, params, data }) {
  const pat = data.pat;
  const jobId = params.id;
  const exportId = params.exportId;

  if (request.method === "DELETE") {
    try {
      const path = `jobs/${jobId}/exports/${exportId}.json`;
      const cur = await gh(pat, `/repos/${REPO}/contents/${esc(path)}`);
      if (cur && cur.sha) {
        await deleteFile(pat, path, cur.sha, `castcut: delete export ${exportId}`);
      }
      return new Response(JSON.stringify({ ok: true, deleted: exportId }), {
        headers: { "content-type": "application/json" },
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: err.message }), {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    }
  }

  return new Response(JSON.stringify({ error: "method not allowed" }), {
    status: 405,
    headers: { "content-type": "application/json" },
  });
}

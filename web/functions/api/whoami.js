export async function onRequest({ data }) {
  return new Response(JSON.stringify({ login: data.user.login, id: data.user.id }), {
    headers: { "content-type": "application/json" },
  });
}

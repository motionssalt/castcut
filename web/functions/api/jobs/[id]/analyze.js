import { dispatchWorkflow } from "../../../_lib/gh.js";
import { projectType } from "../../../_lib/project.js";
export async function onRequest({ params, data, request }) {
  if (request.method !== 'POST') return new Response('POST required',{status:405});
  const body = await request.json().catch(() => ({}));
  try {
    const content_type = await projectType(data.pat,params.id,body.content_type);
    await dispatchWorkflow(data.pat,"hybrid-analyze.yml",{job_id:params.id,sample_fps:String(body.sample_fps || 2),content_type});
    return new Response(JSON.stringify({ok:true,content_type}),{headers:{'content-type':'application/json'}});
  } catch (e) { return new Response(JSON.stringify({error:e.message}),{status:409,headers:{'content-type':'application/json'}}); }
}

import { dispatchWorkflow } from "../../../_lib/gh.js";
import { projectType } from "../../../_lib/project.js";
export async function onRequest({params,data,request}) {
 if(request.method!=='POST') return new Response('POST required',{status:405});
 const content_type=await projectType(data.pat,params.id);
 const body=await request.json().catch(()=>({}));
 const workflow=body.part==='faces'?'faces.yml':body.part==='all'?'hybrid-analyze.yml':'scene-analyze.yml';
 await dispatchWorkflow(data.pat,workflow,{job_id:params.id,content_type});
 return new Response(JSON.stringify({ok:true,workflow,content_type}),{headers:{'content-type':'application/json'}});
}

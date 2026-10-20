import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import fs from 'node:fs';
import { startNativeDownload, verifiedExportAsset } from '../web/download.js';
import { issueTicket, readTicket, ticketCookie, COOKIE, TTL } from '../web/functions/_lib/download-ticket.js';
import { onRequest as route } from '../web/functions/api/release-assets/[assetId].js';
import { onRequest as middleware } from '../web/functions/_middleware.js';

const pat = 'test-only-not-a-real-credential';
const env = { DOWNLOAD_TOKEN_KEY: '12'.repeat(32) };
const bytes = new Uint8Array([0,0,0,16,102,116,121,112,109,112,52,50,0,0,0,0]);
const asset = { id:123, name:'export.mp4', size:bytes.length, state:'uploaded', digest:'sha256:'+'1'.repeat(64) };
globalThis.localStorage={ getItem:()=>pat };
if (!globalThis.crypto) globalThis.crypto=webcrypto;
const ctx=(method='GET', headers={})=>({request:new Request('https://castcut.pages.dev/api/release-assets/123?download=1',{method,headers}),params:{assetId:'123'},data:{pat},env});
function mockUpstream({range=false,bad=false,truncate=false}={}) {
  const calls=[];
  globalThis.fetch=async(url,opts={})=>{
    calls.push({url,opts});
    if (opts.headers.Accept==='application/vnd.github+json') return Response.json(asset);
    if (String(url).startsWith('https://api.github.com/')) return new Response(null,{status:302,headers:{Location:bad?'https://evil.invalid/file':'https://release-assets.githubusercontent.com/test'}});
    assert.equal(opts.headers.Authorization,undefined);
    const b=range?bytes.slice(4,8):bytes;
    return new Response(b,{status:range?206:200,headers:{'Content-Type':'application/octet-stream','Content-Length':String(truncate?b.length-1:b.length),...(range?{'Content-Range':`bytes 4-7/${bytes.length}`}:{})}});
  };
  return calls;
}

test('Native handoff fetches only small JSON then navigates; never buffers video',async()=>{
  let assigned;
  globalThis.window={location:{assign:url=>assigned=url}};
  globalThis.fetch=async(url,opts)=>{
    assert.equal(url,'/api/release-assets/123');assert.equal(opts.method,'POST');assert.equal(opts.credentials,'same-origin');assert.equal(opts.headers['x-castcut-pat'],pat);
    return Response.json({url:'/api/release-assets/123?download=1',name:'CastCut-123.mp4',size:bytes.length});
  };
  await startNativeDownload(asset);
  assert.equal(assigned,'/api/release-assets/123?download=1');
  const text=fs.readFileSync(new URL('../web/download.js',import.meta.url),'utf8');
  assert.doesNotMatch(text,/new Blob|arrayBuffer|getReader|createObjectURL|showSaveFilePicker/);
});

test('Invalid files, HTTP errors and untrusted navigation cannot start a download',async()=>{
  await assert.rejects(startNativeDownload({...asset,name:'error.html'}),/completed MP4/);
  globalThis.fetch=async()=>new Response('error',{status:401});
  await assert.rejects(startNativeDownload(asset),/401/);
  globalThis.fetch=async()=>Response.json({url:'https://evil.invalid/'});
  await assert.rejects(startNativeDownload(asset),/Invalid download handoff/);
});

test('Encrypted cookie expires, cannot be tampered with, and is bound to exactly one asset',async()=>{
  const now=10000000;
  const token=await issueTicket(env,pat,123,now);
  assert.ok(!token.includes(pat));
  const request=new Request('https://castcut.pages.dev/api/release-assets/123?download=1',{headers:{Cookie:`${COOKIE}=${token}`}});
  assert.equal(await readTicket(env,request,123,now+1),pat);
  assert.equal(await readTicket(env,request,124,now+1),null);
  assert.equal(await readTicket(env,request,123,now+TTL*1000),null);
  const bad=new Request(request,{headers:{Cookie:`${COOKIE}=${token.slice(0,20)}!${token.slice(21)}`}});
  assert.equal(await readTicket(env,bad,123,now+1),null);
  assert.match(ticketCookie(token,123),/Path=\/api\/release-assets\/123;.*HttpOnly; Secure; SameSite=Strict/);
  assert.equal(await readTicket({},request,123,now+1),null);
});

test('POST issues same-origin cookie and JSON without video body or PAT',async()=>{
  const calls=mockUpstream();
  const response=await route(ctx('POST'));
  assert.equal(response.status,200);assert.equal(calls.length,1);
  assert.match(response.headers.get('set-cookie'),/HttpOnly; Secure; SameSite=Strict/);
  const payload=await response.text();assert.ok(!payload.includes(pat));assert.ok(payload.includes('download=1'));
  assert.equal((await route(ctx('POST',{Origin:'https://evil.invalid'}))).status,403);
});

test('GET streams bytes with gallery MIME and short MP4 filename without credential leakage',async()=>{
  mockUpstream();
  const response=await route(ctx());
  assert.equal(response.status,200);assert.equal(response.headers.get('Content-Type'),'video/mp4');
  assert.equal(response.headers.get('Content-Disposition'),'attachment; filename="CastCut-123.mp4"');
  assert.equal(response.headers.get('Content-Length'),String(bytes.length));
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()),bytes);
  assert.equal(response.headers.get('X-Asset-SHA256'),asset.digest.slice(7));
});

test('Range resume and HEAD preserve HTTP semantics',async()=>{
  const calls=mockUpstream({range:true});
  const response=await route(ctx('GET',{Range:'bytes=4-7'}));
  assert.equal(response.status,206);assert.equal(response.headers.get('Content-Length'),'4');
  assert.equal(response.headers.get('Content-Range'),`bytes 4-7/${bytes.length}`);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()),bytes.slice(4,8));
  assert.equal(calls.at(-1).opts.headers.Range,'bytes=4-7');
  mockUpstream();const head=await route(ctx('HEAD'));assert.equal(head.status,200);assert.equal(await head.text(),'');
  mockUpstream();assert.equal((await route(ctx('GET',{Range:'bytes=1-3,6-8'}))).status,416);
});

test('Unsafe redirects and truncated upstream responses fail before download',async()=>{
  mockUpstream({bad:true});assert.equal((await route(ctx())).status,502);
  mockUpstream({truncate:true});assert.equal((await route(ctx())).status,502);
});

test('Cookie only authorizes native GET/HEAD for the scoped asset; other APIs still require PAT',async()=>{
  const token=await issueTicket(env,pat,123);
  globalThis.fetch=async(url,opts)=>{assert.equal(url,'https://api.github.com/user');assert.equal(opts.headers.Authorization,`Bearer ${pat}`);return Response.json({login:'motionssalt'});};
  const check=async(path,method='GET')=>{
    const request=new Request('https://castcut.pages.dev'+path,{method,headers:{Cookie:`${COOKIE}=${token}`}});
    return middleware({request,env,data:{},next:async()=>new Response('authorized')});
  };
  assert.equal((await check('/api/release-assets/123?download=1')).status,200);
  assert.equal((await check('/api/release-assets/124?download=1')).status,401);
  assert.equal((await check('/api/release-assets/123?download=1','POST')).status,401);
  assert.equal((await check('/api/jobs')).status,401);
});

test('Old done records and mismatched bytes cannot still offer the obsolete export',()=>{
  assert.equal(verifiedExportAsset({status:'done',asset_name:asset.name},[asset]),null);
  const digest=asset.digest.slice(7);
  const exp={status:'done',ok:true,asset_name:asset.name,sha256:digest,checks:{ok:true,full_decode_passed:true,sha256:digest,size_bytes:asset.size},delivery:{runner_release_download_identical:true,sha256:digest,asset_id:asset.id}};
  assert.equal(verifiedExportAsset(exp,[asset]),asset);
  assert.equal(verifiedExportAsset(exp,[{...asset,digest:'sha256:'+'0'.repeat(64)}]),null);
  assert.equal(verifiedExportAsset(exp,[{...asset,id:124}]),null);
  assert.equal(verifiedExportAsset(exp,[{...asset,size:asset.size-1}]),null);
  assert.equal(verifiedExportAsset(exp,[{...asset,name:'export.mp4-old.mp4'}]),null);
});

test('Export UI starts native download and never claims an async transfer was saved',()=>{
  const text=fs.readFileSync(new URL('../web/exports.js',import.meta.url),'utf8');
  assert.match(text,/startNativeDownload\(asset/);assert.doesNotMatch(text,/verifiedDownload|saveMp4|MP4 verified and saved|a\.name\.includes\(expId\)/);
});

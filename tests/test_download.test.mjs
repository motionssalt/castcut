import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto, createHash } from 'node:crypto';
import fs from 'node:fs';
import { verifiedDownload, saveMp4 } from '../web/download.js';

const bytes = new Uint8Array([0,0,0,16,102,116,121,112,109,112,52,50,0,0,0,0]);
const asset = { id:123, name:'export.mp4', size:bytes.length, state:'uploaded', digest:'sha256:'+createHash('sha256').update(bytes).digest('hex') };
globalThis.localStorage={ getItem:()=> 'test-only-not-a-real-credential' };
if (!globalThis.crypto) globalThis.crypto=webcrypto;

test('ClipForge authenticated streaming download preserves bytes and verifies digest',async()=>{
  globalThis.fetch=async(url,opts)=>{
    assert.equal(url,'https://api.github.com/repos/motionssalt/castcut/releases/assets/123');
    assert.equal(opts.headers.Accept,'application/octet-stream');
    assert.equal(opts.headers.Authorization,'Bearer test-only-not-a-real-credential');
    return new Response(bytes,{headers:{'content-length':String(bytes.length)}});
  };
  const progress=[];
  const received=await verifiedDownload(asset,(n,total)=>progress.push([n,total]));
  assert.deepEqual(received,bytes);
  assert.deepEqual(progress.at(-1),[bytes.length,bytes.length]);
});

test('Private HTML error, truncation, and digest mismatch cannot be saved',async()=>{
  globalThis.fetch=async()=>new Response('not authorized',{status:404});
  await assert.rejects(verifiedDownload(asset),/404/);
  globalThis.fetch=async()=>new Response(bytes.slice(0,10));
  await assert.rejects(verifiedDownload(asset),/incomplete/);
  globalThis.fetch=async()=>new Response(new Uint8Array(bytes.length));
  await assert.rejects(verifiedDownload(asset),/not MP4/);
  globalThis.fetch=async()=>new Response(bytes);
  await assert.rejects(verifiedDownload({...asset,digest:'sha256:'+'0'.repeat(64)}),/SHA256 mismatch/);
  await assert.rejects(verifiedDownload({...asset,digest:null}),/Missing release SHA256/);
});

test('Release URL cannot redirect PAT to an untrusted supplied URL',async()=>{
  globalThis.fetch=async(url)=>{
    assert.equal(url,'https://api.github.com/repos/motionssalt/castcut/releases/assets/123');
    return new Response(bytes);
  };
  await verifiedDownload({...asset,url:'https://untrusted.invalid',browserUrl:'https://untrusted.invalid'});
});

test('Android save uses byte-identical video/mp4 Blob with .mp4 filename',async()=>{
  globalThis.window={};
  let blob;
  const oldCreate=URL.createObjectURL;
  const oldTimeout=globalThis.setTimeout;
  URL.createObjectURL=b=>{blob=b;return 'blob:test';};
  globalThis.setTimeout=()=>0;
  const a={click(){this.clicked=true;},remove(){}};
  globalThis.document={body:{appendChild(){}},createElement:()=>a};
  try {
    assert.equal(await saveMp4(bytes,'export.mp4'),'download');
    assert.equal(blob.type,'video/mp4');
    assert.deepEqual(new Uint8Array(await blob.arrayBuffer()),bytes);
    assert.equal(a.download,'export.mp4');
    assert.ok(a.clicked);
  } finally {URL.createObjectURL=oldCreate;globalThis.setTimeout=oldTimeout;}
});

test('Export UI has only authenticated verified MP4 download, not raw private links',()=>{
  const text=fs.readFileSync(new URL('../web/exports.js',import.meta.url),'utf8');
  assert.match(text,/verifiedDownload\(asset/);
  assert.doesNotMatch(text,/href="\$\{escapeHtml\(downloadUrl/);
  assert.doesNotMatch(text,/Download MP4\/MKV/);
});

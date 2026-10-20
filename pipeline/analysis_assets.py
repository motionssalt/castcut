"""Single required analysis-asset contract, retrying publication and remote completeness gate."""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path
from .project import content_type
from .sheets import SHEET_FORMAT, DESCRIPTION
from .releases import choose_release_for_asset, ensure_release
from .status import write_status


def gh(*args):
    return subprocess.check_output(['gh',*args],text=True)


def release_assets(tag, repo):
    return json.loads(gh('release','view',tag,'--repo',repo,'--json','assets'))['assets']


def missing_assets(manifest, inventories):
    missing=[]
    for entry in manifest['assets']:
        found=next((a for a in inventories.get(entry['release_tag'],[]) if a['name']==entry['name']),None)
        if not found or (entry.get('size') is not None and found.get('size') != entry['size']) or found.get('state','uploaded') not in ('uploaded',''):
            missing.append(f"{entry['release_tag']}/{entry['name']}")
    return missing


def upload(job, path, name, purpose, repo):
    path=Path(path)
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError(f'Required analysis asset missing or empty: {name} ({path})')
    if path.name != name:
        import shutil
        staged=Path('work')/'.release_upload'/name
        staged.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,staged)
        path=staged
    size=path.stat().st_size
    if size >= 2*1024**3:
        raise RuntimeError(f'Analysis asset {name} exceeds 2 GiB; split this archive, never omit analysis assets.')
    primary=f'castcut-{job}'
    # Reuse an existing named asset's release, avoiding obsolete duplicates on repair.
    prior=[]
    try: prior=release_assets(primary,repo)
    except subprocess.CalledProcessError: pass
    tag=primary if any(a['name']==name for a in prior) else choose_release_for_asset(job,size,repo)
    ensure_release(tag,job,repo)
    error=None
    for delay in (0,2,5):
        time.sleep(delay)
        try:
            gh('release','upload',tag,str(path),'--repo',repo,'--clobber')
            if any(a['name']==name and a['size']==size for a in release_assets(tag,repo)):
                return {'name':name,'release_tag':tag,'size':size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'purpose':purpose}
        except subprocess.CalledProcessError as exc: error=exc
    raise RuntimeError(f'Upload/verification failed for {name} on {tag}: {error}')


def remote_part(job,part,repo):
    try:
        doc=json.loads(gh('api',f'repos/{repo}/contents/jobs/{job}/analysis/{part}-assets.json'))
        return json.loads(base64.b64decode(doc['content']))
    except (subprocess.CalledProcessError,KeyError,ValueError): return None


def publish(job,part,work='work',jobs_root='jobs',repo=None):
    repo=repo or os.environ.get('GITHUB_REPOSITORY','motionssalt/castcut')
    kind=content_type(job,jobs_root)
    root=Path(jobs_root)/job
    directory=root/'analysis'
    directory.mkdir(exist_ok=True)
    work=Path(work)
    refs=[]
    if part=='faces':
        face_path=root/'clips/manifest.json'
        faces=json.loads(face_path.read_text())
        thumbs=work/'face_thumbnails.zip'
        with zipfile.ZipFile(thumbs,'w',zipfile.ZIP_DEFLATED) as z:
            for char in faces['characters']:
                thumb=char.get('thumb')
                if not thumb: raise RuntimeError(f"Face {char['id']} missing thumbnail")
                z.writestr(char['id']+'.webp',base64.b64decode(thumb.split(',',1)[1]))
            if not faces['characters']: z.writestr('NO_FACES.txt','Detector found no faces; see face manifest funnel.')
        refs=[upload(job,face_path,'faces-manifest.json','face_manifest',repo),upload(job,thumbs,'face_thumbnails.zip','face_thumbnails',repo)]
    elif part=='scene':
        sheets=json.loads((work/'sheets.json').read_text())
        duration=json.loads((work/'video_meta.json').read_text())['duration']
        import math
        if len(sheets['sheets'])!=math.ceil(duration/6): raise RuntimeError('Screenshot sheet count does not match ceil(duration/6)')
        shots=work/'screenshots.zip'
        with zipfile.ZipFile(shots,'w',zipfile.ZIP_DEFLATED) as z:
            for sheet in sheets['sheets']:
                path=work/'shots'/sheet['name']
                if not path.is_file(): raise RuntimeError(f"Missing screenshot {sheet['name']}")
                z.write(path,'shots/'+sheet['name'])
        for name,purpose in [('00_READ_THIS_FIRST.txt','instructions'),('scene_index.json','scene_index'),('transcript.json','transcript'),('video_meta.json','video_metadata'),('sheets.json','sheet_index'),('screenshots.zip','screenshot_sheets')]:
            refs.append(upload(job,work/name,name,purpose,repo))
        refs.append(upload(job,root/'scene/agent_prompt.txt','agent_prompt.txt','short_prompt',repo))
    else: raise ValueError(part)
    component={'content_type':kind,'assets':refs}
    (directory/f'{part}-assets.json').write_text(json.dumps(component,indent=2))
    parts={part:component}
    other='scene' if part=='faces' else 'faces'
    other_path=directory/f'{other}-assets.json'
    parts[other]=json.loads(other_path.read_text()) if other_path.exists() else remote_part(job,other,repo)
    if not parts[other]:
        write_status(job,task=part,task_state='ready',task_percent=100,task_message=f'{part} assets uploaded; waiting for {other}',release_verified=False,jobs_root=jobs_root)
        return False
    all_refs=parts['faces']['assets']+parts['scene']['assets']
    ingest=json.loads((root/'ingest.json').read_text())
    if ingest.get('video_in_release') is not False:
        names=ingest.get('sources') or [ingest.get('asset_name','source.mp4')]
        for name in names:
            all_refs.append({'name':name,'release_tag':f'castcut-{job}','purpose':'source_video'})
    manifest={'version':2,'job_id':job,'content_type':kind,'video_in_release':ingest.get('video_in_release',True),'screenshot_format':SHEET_FORMAT,'screenshot_instructions':DESCRIPTION,'assets':all_refs+[{'name':'manifest.json','release_tag':f'castcut-{job}','purpose':'manifest'}]}
    manifest_path=work/'manifest.json'
    manifest_path.write_text(json.dumps(manifest,indent=2))
    # The authoritative manifest is always discoverable on the primary release.
    gh('release','upload',f'castcut-{job}',str(manifest_path),'--repo',repo,'--clobber')
    missing=[]
    for delay in (0,2,5):
        time.sleep(delay)
        inventories={tag:release_assets(tag,repo) for tag in {a['release_tag'] for a in manifest['assets']}}
        missing=missing_assets(manifest,inventories)
        if not missing: break
        for ref in manifest['assets']:
            if f"{ref['release_tag']}/{ref['name']}" not in missing: continue
            candidate=work/ref['name']
            if candidate.is_file(): gh('release','upload',ref['release_tag'],str(candidate),'--repo',repo,'--clobber')
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if missing:
        message='Analysis incomplete; missing release assets: '+', '.join(missing)
        write_status(job,'incomplete',message=message,task=part,task_state='incomplete',task_message=message,release_verified=False,missing_assets=missing,jobs_root=jobs_root,force=True)
        raise RuntimeError(message)
    write_status(job,task=part,task_state='ready',task_percent=100,task_message=f'{part} complete; release verified',release_verified=True,missing_assets=[],release_url=f'https://github.com/{repo}/releases/tag/castcut-{job}',jobs_root=jobs_root)
    print(f'[assets] VERIFIED {len(manifest["assets"])} required assets across {len(inventories)} release(s)',flush=True)
    return True


def main():
    p=argparse.ArgumentParser()
    p.add_argument('job_id');p.add_argument('--part',required=True,choices=['faces','scene']);p.add_argument('--work-dir',default='work');p.add_argument('--jobs-root',default='jobs')
    a=p.parse_args()
    try: publish(a.job_id,a.part,a.work_dir,a.jobs_root)
    except Exception as exc:
        write_status(a.job_id,'incomplete',message=str(exc),task=a.part,task_state='incomplete',task_message=str(exc),release_verified=False,jobs_root=a.jobs_root,force=True)
        raise

if __name__=='__main__': main()

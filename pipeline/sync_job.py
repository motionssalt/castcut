"""Conflict-free job commits via GitHub Contents SHA compare-and-swap.
Never autostash/pull/add conflict-marker JSON. Concurrent tasks merge by timestamp.
"""
import argparse
import base64
import json
import os
import subprocess
import time
from pathlib import Path
import requests


def merge_status(remote,local,kind):
    merged={**remote,**local,'content_type':kind}
    tasks=dict(remote.get('tasks',{}))
    for key,value in local.get('tasks',{}).items():
        prior=tasks.get(key,{})
        if value.get('updated_at','') >= prior.get('updated_at',''): tasks[key]=value
    merged['tasks']=tasks
    # A stale pipeline must not invalidate a newer successful completeness check.
    if remote.get('updated_at','') > local.get('updated_at',''):
        for key in ('release_verified','missing_assets','release_url'):
            if key in remote: merged[key]=remote[key]
    states=[tasks.get(k,{}).get('state') for k in ('faces','scene')]
    if any(s in ('failed','cancelled','incomplete') for s in states): merged['state']='incomplete'
    elif any(s in ('running','queued') for s in states): merged['state']='analyzing'
    elif all(s=='ready' for s in states):
        merged['state']='ready' if merged.get('release_verified') else 'incomplete'
        merged['message']='Project ready — required release assets verified.' if merged.get('release_verified') else 'Analysis incomplete: required release assets not yet verified.'
    return merged


def sync(job,repo=None,jobs_root='jobs'):
    repo=repo or os.environ.get('GITHUB_REPOSITORY','motionssalt/castcut')
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token: raise RuntimeError('GH_TOKEN required to commit job progress')
    session=requests.Session();session.headers.update(Authorization=f'Bearer {token}',Accept='application/vnd.github+json')
    root=Path(jobs_root)/job
    kind=json.loads((root/'ingest-request.json').read_text()).get('content_type')
    if kind not in ('irl','anime'): raise ValueError(f'Project {job}: missing immutable content_type')
    # Only files actually changed/generated locally. Never commit source videos or scratch.
    changed=subprocess.check_output(['git','ls-files','--modified','--others','--exclude-standard','--',str(root)],text=True).splitlines()
    for filename in changed:
        path=Path(filename)
        if not path.is_file(): continue
        payload=path.read_bytes()
        if path.suffix=='.json': json.loads(payload)  # reject every conflict marker / malformed record
        url=f'https://api.github.com/repos/{repo}/contents/{filename}'
        for attempt in range(8):
            response=session.get(url,timeout=60)
            if response.status_code==404: prior=None
            else: response.raise_for_status();prior=response.json()
            content=payload
            if path.name=='status.json':
                remote={}
                if prior:
                    try: remote=json.loads(base64.b64decode(prior['content']))
                    except (ValueError,KeyError): pass  # recover formerly corrupted record from creation authority
                content=json.dumps(merge_status(remote,json.loads(payload),kind),indent=2).encode()
            if prior and base64.b64decode(prior.get('content',''))==content: break
            body={'message':f'castcut: {job} {path.name}','content':base64.b64encode(content).decode(),'branch':'main'}
            if prior: body['sha']=prior['sha']
            result=session.put(url,json=body,timeout=60)
            if result.status_code in (409,422): time.sleep(min(attempt+1,5));continue
            result.raise_for_status()
            path.write_bytes(content)
            break
        else: raise RuntimeError(f'Concurrent update retry exhausted: {filename}')
        print(f'[sync] committed {filename} with SHA guard',flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('job_id');a=p.parse_args();sync(a.job_id)

if __name__=='__main__': main()

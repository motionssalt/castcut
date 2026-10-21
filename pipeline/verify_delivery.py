"""Read back a published release asset and compare exact on-runner bytes."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
import requests


def verify(path, tag, repository=None):
    path = Path(path)
    repo = repository or os.environ.get('GITHUB_REPOSITORY','motionssalt/castcut')
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        raise RuntimeError('GH_TOKEN required for authenticated release readback')
    session = requests.Session()
    session.headers.update({'Authorization':f'Bearer {token}','Accept':'application/vnd.github+json'})
    with path.open('rb') as f:
        expected = hashlib.file_digest(f,'sha256').hexdigest()
    for attempt in range(5):
        r = session.get(f'https://api.github.com/repos/{repo}/releases/tags/{tag}',timeout=60)
        r.raise_for_status()
        asset = next((a for a in r.json()['assets'] if a['name']==path.name and a['state']=='uploaded'),None)
        if asset:
            break
        time.sleep(2*(attempt+1))
    else:
        raise RuntimeError('Release asset never reached uploaded state')
    if asset['size'] != path.stat().st_size:
        raise RuntimeError('Release length differs from runner output')
    if asset.get('digest') != 'sha256:'+expected:
        raise RuntimeError('GitHub release SHA256 differs from runner output')
    h = hashlib.sha256()
    size = 0
    with session.get(asset['url'],headers={'Accept':'application/octet-stream'},stream=True,timeout=180) as r:
        r.raise_for_status()
        for chunk in r.iter_content(1024*1024):
            h.update(chunk)
            size += len(chunk)
    if size != path.stat().st_size or h.hexdigest() != expected:
        raise RuntimeError('Authenticated delivered bytes differ from runner output')
    result = {'asset_id':asset['id'],'asset_name':asset['name'],'sha256':expected,'size_bytes':size,
              'runner_release_download_identical':True, 'browser_download_url':asset['browser_download_url']}
    path.with_suffix('.delivery.json').write_text(json.dumps(result,indent=2)+'\n')
    # Publish human-checkable integrity alongside every normal export, not just
    # in the job's private JSON. Keep this AFTER successful asset readback.
    import subprocess
    checksum=path.with_name(path.name+'.sha256')
    checksum.write_text(f'{expected}  {path.name}\n')
    receipt=path.with_name(path.name+'.integrity.json')
    receipt.write_text(json.dumps(result,indent=2)+'\n')
    subprocess.run(['gh','release','upload',tag,str(checksum),str(receipt),'--repo',repo,'--clobber'],check=True)
    print(json.dumps(result),flush=True)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('path')
    p.add_argument('--tag',required=True)
    a = p.parse_args()
    verify(a.path,a.tag)

if __name__ == '__main__':
    main()

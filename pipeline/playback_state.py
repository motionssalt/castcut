"""Merge diagnosis fields into latest default-branch state with SHA CAS.

Concurrent export status commits must not lose build history or diagnosis.
"""
import base64, datetime, json, os, subprocess, time
import requests


def commit(changes,message):
    repo=os.environ.get('GITHUB_REPOSITORY','motionssalt/castcut')
    token=os.environ.get('GH_TOKEN') or subprocess.check_output(['gh','auth','token'],text=True).strip()
    session=requests.Session();session.headers.update(Authorization='Bearer '+token,Accept='application/vnd.github+json')
    url=f'https://api.github.com/repos/{repo}/contents/BUILD_STATE.json'
    for attempt in range(8):
        response=session.get(url,timeout=60);response.raise_for_status();record=response.json()
        data=json.loads(base64.b64decode(record['content']));state=data['playback_diagnosis']
        state.update(changes);state['last_updated']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        body={'message':message,'sha':record['sha'],'branch':'main','content':base64.b64encode((json.dumps(data,indent=2)+'\n').encode()).decode()}
        result=session.put(url,json=body,timeout=60)
        if result.status_code in (409,422):time.sleep(attempt+1);continue
        result.raise_for_status();return result.json()['commit']['sha']
    raise RuntimeError('Concurrent BUILD_STATE update exhausted retries')

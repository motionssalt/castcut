"""Unified analysis/repair runner: reuse source, publish each task honestly, gate readiness."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from .project import content_type
from .status import write_status
from .sync_job import sync
from .analysis_assets import publish


def main():
    p=argparse.ArgumentParser();p.add_argument('job_id');p.add_argument('--content-type',default='');p.add_argument('--fps',type=float,default=2);p.add_argument('--repair',choices=['all','scene','faces'],default='all');a=p.parse_args()
    kind=content_type(a.job_id,expected=a.content_type)
    tasks=['scene','faces'] if a.repair=='all' else [a.repair]
    # Initialize both upfront: old ready components may not mask a failed rerun.
    for task in tasks:
        write_status(a.job_id,task=task,task_state='queued',task_message='Queued for analysis',release_verified=False)
    sync(a.job_id)
    active=None
    try:
        subprocess.run([sys.executable,'-m','pipeline.refetch',a.job_id,'--release-tag',f'castcut-{a.job_id}'],check=True)
        for task in tasks:
            active=task
            write_status(a.job_id,task=task,task_state='running',task_message=f'{kind.upper()} {task} analysis running',task_percent=10)
            sync(a.job_id)
            if task=='scene':
                subprocess.run([sys.executable,'-m','pipeline.scene_analyze',a.job_id,'--content-type',kind],check=True)
                subprocess.run([sys.executable,'-m','pipeline.scene_prompt',a.job_id],check=True)
            else:
                subprocess.run([sys.executable,'-m','pipeline.faces',a.job_id,'--content-type',kind,'--fps',str(a.fps)],check=True)
            publish(a.job_id,task)
            sync(a.job_id)
        print(f'[analysis] complete {a.job_id} immutable content_type={kind}',flush=True)
    except Exception as exc:
        for task in tasks:
            record=json.loads((Path('jobs')/a.job_id/'status.json').read_text())
            if task==active or record.get('tasks',{}).get(task,{}).get('state')=='queued':
                write_status(a.job_id,'incomplete',task=task,task_state='failed',task_message=str(exc),message=str(exc),release_verified=False,force=True)
        sync(a.job_id)
        raise

if __name__=='__main__': main()

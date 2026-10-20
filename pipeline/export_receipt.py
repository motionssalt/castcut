"""Publish truthful per-export status only after media gate and release readback."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('job_id')
    p.add_argument('kind',choices=['clips','scene'])
    p.add_argument('--export-id',default='')
    p.add_argument('--failed',action='store_true')
    a=p.parse_args()
    root=Path('jobs')/a.job_id
    summary=root/a.kind/'export.json'
    data=json.loads(summary.read_text()) if summary.exists() else {}
    eid=a.export_id or data.get('export_id')
    # Never change a previous export's record when this invocation failed early.
    if a.export_id and data.get('export_id')!=a.export_id:
        data={'export_id':a.export_id,'job_id':a.job_id,'project_id':a.job_id,'type':a.kind}
    if a.failed:
        data.update(status='failed',ok=False)
    else:
        asset=Path('work')/data['asset_name']
        verification=json.loads(asset.with_suffix('.verify.json').read_text())
        delivery=json.loads(asset.with_suffix('.delivery.json').read_text())
        if verification['sha256']!=delivery['sha256'] or not delivery['runner_release_download_identical']:
            raise RuntimeError('Missing or mismatched release receipt')
        data.update(status='done',ok=True,sha256=verification['sha256'],
                    video_peak_10s_kbps=verification['video_peak_10s_kbps'],
                    video_average_kbps=verification['video_average_kbps'],
                    mb_per_minute=verification['mb_per_minute'],reference_commit=verification['reference_commit'],
                    delivery=delivery,checks=verification)
    summary.parent.mkdir(parents=True,exist_ok=True)
    summary.write_text(json.dumps(data,indent=2)+'\n')
    if eid:
        dest=root/'exports'/f'{eid}.json'
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(data,indent=2)+'\n')

if __name__=='__main__':
    main()

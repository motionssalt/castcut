import json
import subprocess
from pathlib import Path
import pytest
from PIL import Image
from pipeline.project import content_type
from pipeline.status import write_status
from pipeline.sync_job import merge_status
from pipeline.analysis_assets import missing_assets
from pipeline.sheets import extract


def project(tmp_path,kind='irl'):
    root=tmp_path/'p';root.mkdir()
    (root/'ingest-request.json').write_text(json.dumps({'content_type':kind}))
    return root


def test_immutable_type_and_wrong_detector_guard(tmp_path):
    project(tmp_path)
    assert content_type('p',str(tmp_path))=='irl'
    with pytest.raises(ValueError,match='mismatch'): content_type('p',str(tmp_path),'anime')
    with pytest.raises(ValueError,match='missing stored'): content_type('missing',str(tmp_path))
    with pytest.raises(ValueError,match='mismatch'): write_status('p','faces_running',content_type='anime',jobs_root=str(tmp_path))


def test_pipeline_failure_not_hidden_and_ready_requires_release(tmp_path):
    project(tmp_path)
    write_status('p',task='faces',task_state='ready',jobs_root=str(tmp_path))
    rec=write_status('p',task='scene',task_state='ready',jobs_root=str(tmp_path))
    assert rec['state']=='incomplete'
    rec=write_status('p',task='scene',task_state='ready',release_verified=True,jobs_root=str(tmp_path))
    assert rec['state']=='ready' and rec['content_type']=='irl'
    rec=write_status('p',task='faces',task_state='failed',task_message='Missing faces-manifest.json',jobs_root=str(tmp_path))
    assert rec['state']=='incomplete' and 'faces-manifest.json' in rec['message']


def test_concurrent_status_tasks_merge_without_losing_type():
    remote={'tasks':{'scene':{'state':'ready','updated_at':'2026-10-20T01:00:00Z'}},'content_type':'irl'}
    local={'tasks':{'faces':{'state':'ready','updated_at':'2026-10-20T01:01:00Z'}},'content_type':'anime','release_verified':True}
    result=merge_status(remote,local,'irl')
    assert result['state']=='ready' and result['content_type']=='irl'
    assert len(result['tasks'])==2


def test_incomplete_release_names_missing_not_ready():
    manifest={'assets':[{'name':'screenshots.zip','release_tag':'castcut-p','size':9},{'name':'faces-manifest.json','release_tag':'castcut-p-out-2'}]}
    assert missing_assets(manifest,{'castcut-p':[{'name':'source.mp4','size':100}]})==['castcut-p/screenshots.zip','castcut-p-out-2/faces-manifest.json']
    assert not missing_assets(manifest,{'castcut-p':[{'name':'screenshots.zip','size':9}],'castcut-p-out-2':[{'name':'faces-manifest.json'}]})


def test_six_panel_grids_tail_and_timestamp_index(tmp_path):
    src=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=640x360:rate=25:duration=7.2','-c:v','libx264',str(src)],check=True)
    sheets=extract(src,tmp_path/'shots',7.2)
    assert len(sheets)==2
    assert sheets[0]['timestamps']==[0,1,2,3,4,5]
    assert sheets[1]['timestamps']==[6,6.2,6.4,6.6,6.8,7]
    for sheet in sheets:
        image=Image.open(tmp_path/'shots'/sheet['name'])
        assert image.size==(1930,726)
        # Each panel contains both its own frame and a burned label box.
        for row in range(2):
            for col in range(3):
                box=image.crop((2+col*642,2+row*362,642+col*642,362+row*362))
                assert box.getbbox() is not None

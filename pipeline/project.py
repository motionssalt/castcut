"""Creation record is the immutable authority; never guess a project type."""
import json
from pathlib import Path


def content_type(job_id, jobs_root='jobs', expected=None):
    root = Path(jobs_root)/job_id
    request = root/'ingest-request.json'
    if request.exists():
        doc = json.loads(request.read_text())
        value = doc.get('content_type')
    else:
        path = root/'status.json'
        doc = json.loads(path.read_text()) if path.exists() else {}
        value = doc.get('content_type')
    if value not in ('anime','irl'):
        raise ValueError(f'Project {job_id}: missing stored content_type; recover creation record, never default to anime.')
    if expected and expected != value:
        raise ValueError(f'Project {job_id}: content_type mismatch: pipeline requested {expected!r}, immutable project is {value!r}.')
    print(f'[project] {job_id}: immutable content_type={value}', flush=True)
    return value

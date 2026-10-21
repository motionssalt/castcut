"""ClipForge terminal delivery pass; no raw-frame rebuild or source fallback.

Argument template is the untouched upstream_compress.py command. The owner
allows bitrate, source fps/size caps and AAC128 changes. Additional safeguards
fill unspecified color/PTS/interleaving policy. See delivery_pass.py.
"""
from pathlib import Path
from . import common
from pipeline.delivery_pass import encode
DELIVERY_CRF='23'
DELIVERY_PRESET='medium'
X264_PROFILE='high'
X264_LEVEL='4.0'


def _validate(source,candidate,resolution='1080p'):
    from pipeline.export_checks import validate
    try:
        validate(candidate,source=source,resolution=resolution)
    except TypeError:
        validate(candidate,source=source)


def compress(source:Path,destination:Path,resolution:str='1080p'):
    source,destination=Path(source),Path(destination)
    if not source.is_file() or source.resolve()==destination.resolve():
        raise common.StageBError('Terminal input must exist and differ from destination')
    destination.parent.mkdir(parents=True,exist_ok=True)
    candidate=destination.with_name(destination.name+'.compressing.mp4')
    candidate.unlink(missing_ok=True)
    try:
        command=encode(source,candidate,resolution=resolution)
    except TypeError:
        command=encode(source,candidate)
    try:
        _validate(source,candidate,resolution=resolution)
    except TypeError:
        _validate(source,candidate)
    before,after=source.stat().st_size,candidate.stat().st_size
    candidate.replace(destination)
    # A smaller original is NOT a normalization substitute. Never resurrect it.
    import json
    destination.with_suffix('.command.json').write_text(json.dumps(command,indent=2)+'\n')
    return before,after,after<before

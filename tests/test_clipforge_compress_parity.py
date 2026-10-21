"""Literal upstream command parity and explicit owner-policy boundaries."""
from pathlib import Path
from unittest.mock import patch
from pipeline.clipforge_vendor import common,compress
from pipeline.delivery_pass import reference_command,command,VERSION
from tests.test_clipforge_export import upstream_module


def test_terminal_reference_command_is_verbatim_upstream(tmp_path):
    source=tmp_path/'merged.mp4';destination=tmp_path/'final.mp4'
    source.write_bytes(b'x'*100);captured=[]
    def encoded(cmd):captured.append(cmd);Path(cmd[-1]).write_bytes(b'y'*50)
    upstream=upstream_module('compress')
    with patch.object(common,'sh',side_effect=encoded),patch.dict(upstream.compress.__globals__,{'_validate':lambda *a:None}):
        upstream.compress(source,destination)
    assert captured[0]==reference_command(source,destination.with_name(destination.name+'.compressing.mp4'))


def test_policy_changes_are_explicit_not_hidden_raw_frames(tmp_path):
    source=tmp_path/'joined.mp4';destination=tmp_path/'final.mp4'
    cmd=command(source,destination,safe=False)
    ref=reference_command(source,destination)
    i=cmd.index('-maxrate');assert cmd[i:i+4]==['-maxrate','1800k','-bufsize','1800k'];del cmd[i:i+4]
    i=cmd.index('-c:a');assert cmd[i:i+10]==['-c:a','aac','-profile:a','aac_low','-b:a','128k','-ar','48000','-ac','2'];cmd[i:i+10]=['-c:a','copy']
    cmd[cmd.index('0:a:0')]='0:a?'
    assert cmd==ref
    assert 'pipe:0' not in cmd
    assert VERSION=='clipforge-terminal-phone-diagnosis-v2'


def test_size_fallback_never_restores_an_original(tmp_path):
    source=tmp_path/'merged.mp4';destination=tmp_path/'final.mp4';source.write_bytes(b'old')
    expected=b'larger-normalized-and-verified-candidate'
    def encoded(src,dst):Path(dst).write_bytes(expected);return ['ffmpeg','-i',str(src),str(dst)]
    with patch.object(compress,'encode',side_effect=encoded),patch.object(compress,'_validate'):
        compress.compress(source,destination)
    assert destination.read_bytes()==expected
    assert destination.with_suffix('.command.json').exists()


def test_failed_verification_cannot_publish_candidate(tmp_path):
    source=tmp_path/'joined.mp4';destination=tmp_path/'final.mp4';source.write_bytes(b'old')
    import pytest
    def encoded(src,dst):Path(dst).write_bytes(b'bad');return []
    with patch.object(compress,'encode',side_effect=encoded),patch.object(compress,'_validate',side_effect=RuntimeError('bad cadence')):
        with pytest.raises(RuntimeError,match='bad cadence'):compress.compress(source,destination)
    assert not destination.exists()

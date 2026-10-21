"""Executable upstream comparison: reference code, same source and same cuts."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
from pipeline.clipforge_export import export_segments
from pipeline.clipforge_vendor import render, compress, common
from pipeline.export_checks import probe, mp4_boxes, validate

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'pipeline/clipforge_vendor'


def upstream_module(name):
    """Execute the actual pinned source, replacing only its import namespace."""
    tree=ast.parse((VENDOR/f'upstream_{name}.py').read_text())
    tree.body=[n for n in tree.body if not (isinstance(n,ast.ImportFrom) and n.module=='pipeline.stage_b')]
    scope={'common':common,'__name__':'clipforge_reference','__file__':str(VENDOR/f'upstream_{name}.py')}
    exec(compile(tree,str(VENDOR/f'upstream_{name}.py'),'exec'),scope)
    return SimpleNamespace(**scope)


def test_vendor_snapshots_are_verbatim():
    expected={'render':'22ecbaf665c7bde294d3aaffbd1a24ea593900327bde69b2dd1afc244a60eb2c','compress':'548eebbd16fa10ca95e8f51665d95d0874546380da9ffee572744105a5036d7a'}
    for name,digest in expected.items():
        assert hashlib.sha256((VENDOR/f'upstream_{name}.py').read_bytes()).hexdigest()==digest


def test_renderer_keeps_reference_contract_with_explicit_normalization():
    text=(VENDOR/'render.py').read_text()
    assert "X264_PROFILE = 'main'" in text
    assert "X264_LEVEL = '4.1'" in text
    assert "-use_editlist" in text and "-brand" in text
    assert 'PTS-STARTPTS' in text and 'trim=end_frame' in text
    assert 'color_filter(source_video)' in text
    assert 'concat=n={n}:v=1:a=1' in text


def test_both_flow_entrypoints_use_one_vendor_path():
    for name in ['clips_export','scene_export']:
        text=(ROOT/f'pipeline/{name}.py').read_text()
        assert 'from .clipforge_export import export_segments' in text
        assert 'export_segments(src, segments, final' in text
        assert 'concat_segments(' not in text
        assert 'encode_final_export(' not in text
        assert 'add_argument("--quality"' not in text
        assert 'add_argument("--crf"' not in text


def test_real_reference_and_ported_multicut_audio_selection(tmp_path):
    source=tmp_path/'source.mkv'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=24:duration=5',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=5',
                    '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=5',
                    '-map','0:v','-map','1:a','-map','2:a','-c:v','libx264','-pix_fmt','yuv420p',
                    '-c:a','pcm_s16le',str(source)],check=True)
    segments=[(.125,1.375),(2.25,3.5),(3.8,4.8)]
    output=tmp_path/'castcut.mp4'
    result=export_segments(source,segments,output,audio_track=1,temp_dir=tmp_path)
    assert result['source_audio_index']==1
    assert result['source_audio_stream_index']==2
    assert result['fps']==24
    assert result['video_peak_10s_kbps']<=2000
    # Run the UNMODIFIED real ClipForge renderer and compressor on identical
    # original segments and identical lossless selected-audio WAVs.
    plan=[];wavs=[]
    for i,(start,end) in enumerate(segments):
        wav=tmp_path/f'ref-{i}.wav'
        subprocess.run(['ffmpeg','-v','error','-y','-ss',f'{start:.3f}','-to',f'{end:.3f}','-i',str(source),
                        '-map','0:a:1','-vn','-c:a','pcm_s16le','-ar','48000','-ac','2',str(wav)],check=True)
        plan.append({'start_seconds':start,'end_seconds':end,'stretch':1.,'audio_samples':round((end-start)*48000),'video_seconds':end-start})
        wavs.append(str(wav))
    merged=tmp_path/'reference-merged.mp4'
    ref=tmp_path/'reference-final.mp4'
    reference=upstream_module('render')
    reference.produce_merged_video(source,plan,wavs,merged,None)
    upstream_module('compress').compress(merged,ref)
    a,b=probe(ref),probe(output)
    # b (output) must strictly adhere to the proven-playable mobile contract
    assert b['streams'][0]['profile'] == 'Main'
    assert b['streams'][0]['level'] == 41
    assert b['streams'][0]['width'] * b['streams'][0]['height'] <= 921600
    assert b['streams'][0]['has_b_frames'] == 0
    assert b['streams'][0]['pix_fmt'] == 'yuv420p'
    def shape(boxes):
        return [(b['type'],shape(b.get('children',[]))) for b in boxes]
    assert shape(mp4_boxes(ref))==shape(mp4_boxes(output))
    assert a['format']['tags']['major_brand']==b['format']['tags']['major_brand']=='mp42'
    for key in ['color_range','color_space','color_transfer','color_primaries']:
        assert b['streams'][0].get(key)==('tv' if key=='color_range' else 'bt709')
    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-map','0:a:0','-ac','1','-ar','48000','-f','f32le','-'])
    samples=np.frombuffer(pcm,dtype=np.float32)
    spectrum=np.abs(np.fft.rfft(samples))
    frequencies=np.fft.rfftfreq(len(samples),1/48000)
    assert abs(frequencies[np.argmax(spectrum)]-880)<2
    assert spectrum[np.argmin(abs(frequencies-880))] > 20*spectrum[np.argmin(abs(frequencies-440))]
    with pytest.raises(ValueError,match='audio track'):
        export_segments(source,segments,tmp_path/'bad.mp4',audio_track=10,temp_dir=tmp_path)
    validate(output,source=source)

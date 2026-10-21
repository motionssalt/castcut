"""One shared, literal ClipForge render -> terminal-compress path for both flows.

The renderer is vendored, not an independent collection of ffmpeg flags. Its
voiceover inputs are replaced by lossless PCM from the chosen source audio.
"""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from .clipforge_vendor import render, compress, common
from .export_checks import probe, validate
from .delivery_pass import VERSION as REBUILD_VERSION


def export_segments(src, segments, output_path, audio_track=0, temp_dir=None):
    source = Path(src).resolve()
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = probe(source)
    video = next(s for s in metadata['streams'] if s['codec_type']=='video')
    audio = [s for s in metadata['streams'] if s['codec_type']=='audio']
    if audio and (audio_track is None or not 0 <= int(audio_track) < len(audio)):
        raise ValueError(f'Selected audio track {audio_track} is not present; refusing silent fallback')
    fps = min(Fraction(video['r_frame_rate']), Fraction(30))
    if fps <= 0:
        raise ValueError('Source frame rate unavailable; refusing to invent one')
    cuts = [(float(s),float(e)) for s,e in segments]
    if not cuts or any(s<0 or e<=s for s,e in cuts):
        raise ValueError('Every export segment must have valid increasing bounds')
    render.SOURCE_FPS = str(fps)
    # Bound CPU scheduling only, NOT encoder arguments or media structure. This
    # also limits ffmpeg's automatic decoder/filter thread allocation for many
    # parallel original-source inputs, preserving the reference single graph.
    affinity = os.sched_getaffinity(0) if hasattr(os,'sched_getaffinity') else None
    if affinity:
        os.sched_setaffinity(0, sorted(affinity)[:2])
    try:
        with tempfile.TemporaryDirectory(prefix='clipforge-export-', dir=temp_dir) as work:
            work = Path(work)
            wavs = []
            plan = []
            for i,(start,end) in enumerate(cuts):
                wav = work/f'selected-{i:05d}.wav'
                cmd = ['ffmpeg','-y','-v','error']
                if audio:
                    # Same reference input seeking order; lossless preparation
                    # supplies the renderer's original narration-WAV interface.
                    cmd += ['-ss',f'{start:.3f}','-to',f'{end:.3f}','-i',str(source),
                            '-map',f'0:a:{int(audio_track)}','-vn','-sn','-dn']
                else:
                    cmd += ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t',f'{end-start:.3f}']
                cmd += ['-c:a','pcm_s16le','-ar','48000','-ac','2',str(wav)]
                common.sh(cmd)
                wavs.append(str(wav))
                frames=max(1,round((end-start)*fps))
                seconds=float(frames/fps)
                plan.append({'start_seconds':start,'end_seconds':end,'stretch':1.0,
                             'frames':frames, 'audio_samples':round(seconds*48000), 'video_seconds':seconds})
            merged = work/'reference-merged.mp4'
            render.produce_merged_video(source,plan,wavs,merged,music=None)
            before, after, reduced = compress.compress(merged,output)
            report = validate(output,expect_audio=True,source=source)
            report.update({'rebuild_version': REBUILD_VERSION,
                           'video_input_boundary': 'direct joined MP4 -> ClipForge terminal re-encode; no raw-frame workaround',
                           'audio_input_boundary': 'chosen track joined as PCM, freshly encoded AAC-LC stereo 48000Hz 128k',
                           'source_audio_index':int(audio_track) if audio else None,
                           'source_audio_stream_index':audio[int(audio_track)]['index'] if audio else None,
                           'audio_target_kbps':128, 'source_fps':str(Fraction(video['r_frame_rate'])),
                           'target_fps':str(fps), 'source_width':video['width'],'source_height':video['height'],
                           'segments':cuts,'join_times':[sum(e-s for s,e in cuts[:i]) for i in range(1,len(cuts))],
                           'reference_commit':'a05846de7c76a66ad42ba6fd37126514bc452167',
                           'path':'clipforge_vendor.render.produce_merged_video -> clipforge_vendor.compress.compress',
                           'terminal_command': json.loads(output.with_suffix('.command.json').read_text()),
                           'policy_exceptions':['1800k VBV to enforce <=2000k video','original source fps/size caps and exact source-rate clock','AAC-LC stereo 128k re-encode','no original fallback'],
                           'size_before_bytes':before,'compressed':reduced})
            output.with_suffix('.verify.json').write_text(json.dumps(report,indent=2)+'\n')
            return report
    finally:
        if affinity:
            os.sched_setaffinity(0, affinity)

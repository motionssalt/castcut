"""Packet/container checks against the actual vendored ClipForge final MP4 contract."""
from __future__ import annotations
import hashlib
import json
import math
import os
import struct
import subprocess
from fractions import Fraction
from pathlib import Path


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def mp4_boxes(path):
    containers = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'edts', b'dinf'}
    def walk(f, start, end):
        out = []
        pos = start
        while pos + 8 <= end:
            f.seek(pos)
            size, kind = struct.unpack('>I4s', f.read(8))
            header = 8
            if size == 1:
                size = struct.unpack('>Q', f.read(8))[0]
                header = 16
            if size == 0:
                size = end - pos
            if size < header or pos + size > end:
                raise RuntimeError(f'Truncated or invalid {kind!r} MP4 box at {pos}')
            item = {'type': kind.decode('latin1'), 'offset': pos, 'size': size}
            if kind in containers:
                item['children'] = walk(f, pos + header, pos + size)
            if kind == b'ftyp':
                f.seek(pos + header)
                item['major_brand'] = f.read(4).decode('ascii')
            out.append(item)
            pos += size
        if pos != end:
            raise RuntimeError('Unparsed/truncated MP4 box tail')
        return out
    with open(path, 'rb') as f:
        return walk(f, 0, os.path.getsize(path))


def flatten_boxes(items):
    for item in items:
        yield item
        yield from flatten_boxes(item.get('children', []))


def packet_report(path, fps, exact_cadence=False):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_packets', '-show_entries',
                             'packet=stream_index,pts,dts,duration,pts_time,dts_time,duration_time,size,flags',
                             '-of', 'json', str(path)], capture_output=True, text=True, check=True)
    packets = json.loads(result.stdout)['packets']
    by_stream = {}
    for packet in packets:
        by_stream.setdefault(packet['stream_index'], []).append(packet)
    for index, group in by_stream.items():
        last_dts = None
        last_end = None
        for p in group:
            pts = float(p['pts_time'])
            dts = float(p['dts_time'])
            duration = float(p.get('duration_time', 0))
            if dts < -0.0001 or pts < -0.0001:
                raise RuntimeError(f'Negative timestamp in stream {index}')
            if last_dts is not None and dts <= last_dts:
                raise RuntimeError(f'Non-monotonic DTS in stream {index}')
            if last_end is not None and abs(dts - last_end) > 0.00015:
                raise RuntimeError(f'Timestamp discontinuity in stream {index}: {dts-last_end:.6f}s')
            if index == 0:
                if abs(pts-dts) > 0.00001:
                    raise RuntimeError('Reference bf=0 contract violated: video PTS != DTS')
                if abs(duration - 1/fps) > 2/15360:
                    raise RuntimeError('Video cadence differs from reference CFR/timescale quantization')
            last_dts = dts
            last_end = dts + duration
    v = by_stream.get(0, [])
    if not v:
        raise RuntimeError('No video packets')
    times = [float(p['dts_time']) for p in v]
    sizes = [int(p['size']) for p in v]
    duration = float(v[-1]['pts_time']) + float(v[-1].get('duration_time', 1/fps))
    average = sum(sizes) * 8 / duration / 1000
    # Every packet-aligned sliding 10-second window, not just disjoint bins.
    # Last partial windows use 10s denominator; overall average covers short clips.
    right = 0
    total = 0
    peak = 0.0
    for left, start in enumerate(times):
        while right < len(times) and times[right] < start + 10 - 0.000001:
            total += sizes[right]
            right += 1
        peak = max(peak, total * 8 / 10 / 1000)
        total -= sizes[left]
    durations = sorted({int(p['duration']) for p in v})
    if exact_cadence:
        rational = Fraction(str(fps)).limit_denominator(1000000)
        ticks = Fraction(math.lcm(15360, rational.numerator), 1) / rational
        if ticks.denominator != 1 or durations != [int(ticks)]:
            raise RuntimeError(f'Frame reconstruction requires one exact sample duration, got {durations}')
        if any(int(p['pts']) != i * int(ticks) or int(p['dts']) != i * int(ticks) for i, p in enumerate(v)):
            raise RuntimeError('Frame reconstruction timestamp clock drift')
    keyframes = [i for i, p in enumerate(v) if 'K' in p.get('flags', '')]
    if not keyframes or keyframes[0] != 0 or any(b-a != 60 for a,b in zip(keyframes,keyframes[1:])):
        raise RuntimeError('Reference fixed GOP 60 violated')
    return {'video_average_kbps': average, 'video_peak_10s_kbps': peak,
            'video_packets': len(v), 'audio_packets': len(by_stream.get(1, [])),
            'all_packet_timestamps_contiguous': True, 'video_pts_equal_dts': True,
            'fixed_gop': 60, 'duration_video': duration,
            'video_sample_duration_ticks': durations, 'exact_frame_cadence': len(durations) == 1}


def rate_within_limit(path):
    info = probe(path)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    r = packet_report(path, float(Fraction(v['r_frame_rate'])))
    return r['video_average_kbps'] <= 2000 and r['video_peak_10s_kbps'] <= 2000


def validate(path, expect_audio=True, source=None):
    path = Path(path)
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError('Empty or absent export')
    info = probe(path)
    streams = info['streams']
    if [s['codec_type'] for s in streams] != (['video', 'audio'] if expect_audio else ['video']):
        raise RuntimeError('Expected exactly one video and one audio track, with no extra streams')
    v = streams[0]
    expected = {'codec_name':'h264', 'codec_tag_string':'avc1', 'profile':'High', 'level':40,
                'pix_fmt':'yuv420p', 'has_b_frames':0, 'sample_aspect_ratio':'1:1'}
    for k, value in expected.items():
        if v.get(k) != value:
            raise RuntimeError(f'ClipForge {k}: expected {value}, got {v.get(k)}')
    w, h = v['width'], v['height']
    if min(w,h) <= 0 or w > 1920 or h > 1080 or w%2 or h%2:
        raise RuntimeError('Resolution must be even and <=1920x1080')
    rational_fps = Fraction(v['r_frame_rate'])
    fps = float(rational_fps)
    expected_timebase = Fraction(1, math.lcm(15360, rational_fps.numerator))
    if Fraction(v['time_base']) != expected_timebase or Fraction(v['avg_frame_rate']) != rational_fps:
        raise RuntimeError('Fractional source frame rate must have exact integer-tick MP4 timing')
    if not 0 < fps <= 30:
        raise RuntimeError('Frame rate must be original, capped at 30')
    if source:
        original = next(s for s in probe(source)['streams'] if s['codec_type']=='video')
        src_fps = min(Fraction(original['r_frame_rate']), Fraction(30))
        if abs(fps-float(src_fps)) > 0.00001 or w>original['width'] or h>original['height']:
            raise RuntimeError('Source frame rate/no-upscale contract violated')
    audio = streams[1] if expect_audio else {}
    if expect_audio:
        for k, value in {'codec_name':'aac','codec_tag_string':'mp4a','profile':'LC','sample_rate':'48000','channels':2,'time_base':'1/48000'}.items():
            if audio.get(k) != value:
                raise RuntimeError(f'ClipForge audio {k}: expected {value}, got {audio.get(k)}')
        # AAC's target is 128k; tiny clip priming/container durations shift measured average.
        if float(audio.get('bit_rate',0)) > 145000:
            raise RuntimeError('AAC rate not consistent with 128k target')
    if any(abs(float(s.get('start_time',0))) > 0.00001 for s in streams):
        raise RuntimeError('Reference tracks must start at zero')
    boxes = mp4_boxes(path)
    types = [b['type'] for b in boxes]
    if types != ['ftyp','moov','free','mdat'] or boxes[0]['major_brand'] != 'mp42':
        raise RuntimeError('Reference MP4 top-level boxes/brand/faststart differ')
    if any(b['type'] in ('elst','edts','ctts') for b in flatten_boxes(boxes)):
        raise RuntimeError('Reference no-edit-list/no-B-frame MP4 structure violated')
    r = packet_report(path, fps, exact_cadence=True)
    if r['video_average_kbps'] > 2000 or r['video_peak_10s_kbps'] > 2000:
        raise RuntimeError(f'Video exceeds 2000 kbps: avg={r["video_average_kbps"]:.3f}, rolling10s={r["video_peak_10s_kbps"]:.3f}')
    decode = subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(path),'-map','0','-f','null','-'],capture_output=True,text=True)
    if decode.returncode or decode.stderr.strip():
        raise RuntimeError('Full decode failed: '+decode.stderr[-1000:])
    dur = float(info['format']['duration'])
    size = path.stat().st_size
    with path.open('rb') as f: digest = hashlib.file_digest(f,'sha256').hexdigest()
    return {'ok':True, 'sha256':digest, 'duration':round(dur,6), 'size_bytes':size,
            'size_human':f'{size/1024/1024:.2f} MiB', 'mb_per_minute':size/dur*60/1e6,
            'bitrate_bps':round(size*8/dur),'bitrate_kbps':round(size*8/dur/1000,3),
            'video_bitrate_kbps':round(r['video_average_kbps'],3), **r,
            'vcodec':v['codec_name'],'profile':v['profile'],'level':v['level'],
            'width':w,'height':h,'fps':fps,'audio_streams':1 if expect_audio else 0,
            'audio_codec':audio.get('codec_name'),'audio_channels':audio.get('channels',0),
            'audio_lang':audio.get('tags',{}).get('language'),'faststart':True,'has_elst':False,
            'full_decode_passed':True,'mp4_boxes':boxes,'streams':streams}

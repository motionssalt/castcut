"""Explicit decoded-frame/PCM boundary for the ClipForge terminal encoder.

No original packet, timestamp, codec extradata, metadata or container crosses
this boundary. Video travels as uncompressed yuv420p over a pipe (not millions
of image files); audio is decoded to a temporary 48k stereo PCM WAV. The encoder
is still ClipForge's terminal command, with owner bitrate/audio/fps constraints.
"""
from __future__ import annotations
import math
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from .export_checks import probe

REBUILD_VERSION = 'decoded-frames-pcm-v1'


def frame_timescale(fps):
    """Keep ClipForge's 15360 ticks where exact; extend only for fractional fps.

    24000/1001 at 15360 forces alternating 640/641 tick samples. The least
    common multiple represents every original-rate frame with equal ticks.
    This does not change the source frame rate, GOP, codec, or picture count.
    """
    return math.lcm(15360, Fraction(fps).numerator)


def encode_decoded_frames(source, candidate, command):
    source, candidate = Path(source), Path(candidate)
    info = probe(source)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    fps = Fraction(v['r_frame_rate'])
    if not 0 < fps <= 30:
        raise RuntimeError('Frame rebuild requires the capped original frame rate')
    width, height = v['width'], v['height']
    if [s['codec_type'] for s in info['streams']] != ['video', 'audio']:
        raise RuntimeError('Frame rebuild requires exactly one chosen audio stream')
    with tempfile.TemporaryDirectory(prefix='decoded-pcm-', dir=candidate.parent) as work:
        wav = Path(work) / 'chosen-audio.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-y', '-i', str(source),
                        '-map', '0:a:0', '-vn', '-sn', '-dn', '-c:a', 'pcm_s16le',
                        '-ar', '48000', '-ac', '2', '-map_metadata', '-1', str(wav)], check=True)
        # Replace only the terminal pass's input interface. Its x264 argument
        # order, profile, level, preset, GOP, no-B-frame, brand and mux flags stay.
        cmd = list(command)
        old = ['-i', str(source), '-map', '0:v:0', '-map', '0:a?']
        index = next(i for i in range(len(cmd)) if cmd[i:i+len(old)] == old)
        cmd[index:index+len(old)] = [
            '-f', 'rawvideo', '-pixel_format', 'yuv420p',
            '-video_size', f'{width}x{height}', '-framerate', str(fps),
            '-i', 'pipe:0', '-i', str(wav), '-map', '0:v:0', '-map', '1:a:0',
            '-vf', 'setsar=1',
        ]
        index = cmd.index('-c:a')
        # Native AAC has a 1024-sample encoder delay. Compensate at its PCM
        # input, not by shifting the video clock or adding forbidden edit lists.
        # Otherwise the MP4 muxer stretches the first video sample by ~21ms.
        cmd[index:index+2] = ['-af', 'asetpts=N/SR/TB+1024/SR/TB',
                              '-c:a', 'aac', '-profile:a', 'aac_low', '-b:a', '128k', '-ar', '48000', '-ac', '2']
        cmd[cmd.index('-video_track_timescale')+1] = str(frame_timescale(fps))
        # Preserve any explicit reference color declarations; never invent them.
        for field, flag in [('color_range','-color_range'), ('color_space','-colorspace'),
                            ('color_primaries','-color_primaries'), ('color_transfer','-color_trc')]:
            value = v.get(field)
            if value and value not in ('unknown', 'unspecified'):
                cmd[-1:-1] = [flag, value]
        from .delivery_pass import fps_mode_flag
        decode = ['ffmpeg', '-v', 'error', '-xerror', '-i', str(source),
                  '-map', '0:v:0', '-an', '-sn', '-dn', '-pix_fmt', 'yuv420p'] + fps_mode_flag('passthrough') + [
                  '-f', 'rawvideo', 'pipe:1']
        print('Frame rebuild decoder: ' + ' '.join(decode), flush=True)
        print('ClipForge terminal encoder: ' + ' '.join(cmd), flush=True)
        with tempfile.TemporaryFile() as errors:
            decoder = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=errors)
            try:
                subprocess.run(cmd, stdin=decoder.stdout, check=True)
                decoder.stdout.close()
                result = decoder.wait(timeout=60)
                if result:
                    errors.seek(0)
                    raise RuntimeError('Decoded-frame extraction failed: '+errors.read().decode(errors='replace')[-1500:])
            except BaseException:
                if decoder.stdout and not decoder.stdout.closed:
                    decoder.stdout.close()
                if decoder.poll() is None:
                    decoder.kill()
                decoder.wait()
                candidate.unlink(missing_ok=True)
                raise

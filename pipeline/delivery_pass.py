"""Literal ClipForge terminal command, plus explicitly documented owner policy.

High@4.0 / bf=0 / GOP60 / mp42 / no edit lists win over generic Main@4.1
suggestions because the owner explicitly gives existing ClipForge flags priority.
No raw-frame pipes, image dumps, source-copy fallback or post-pass remux.
"""
from __future__ import annotations
import json, subprocess
from fractions import Fraction
from pathlib import Path
from .export_checks import probe

VERSION='clipforge-terminal-phone-diagnosis-v2'


def timescale(fps):
    rate=Fraction(fps)
    # Keep the exact upstream clock whenever it represents the original fps.
    # Fractional source fps is the owner's permitted original-rate exception.
    if (Fraction(15360)/rate).denominator==1: return 15360
    # e.g. 25fps -> 25000 ticks, not a needlessly coarse 25-Hz clock.
    return rate.numerator if rate.denominator!=1 else rate.numerator*1000


def color_filter(video):
    """Convert, not merely relabel. Untagged HD assumes BT709; SD BT601.

    PQ/HLG use linear-light tone mapping. Fully tagged SDR is converted through
    libavfilter colorspace. Incomplete/untagged SDR uses the inferred matrix.
    """
    transfer=video.get('color_transfer');primaries=video.get('color_primaries');matrix=video.get('color_space')
    if transfer in ('smpte2084','arib-std-b67'):
        return 'zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=limited,format=yuv420p'
    if all(v and v not in ('unknown','unspecified','reserved') for v in (transfer,primaries,matrix)):
        return 'colorspace=all=bt709:range=tv:format=yuv420p'
    assumed=matrix if matrix and matrix not in ('unknown','unspecified','reserved') else ('bt709' if video['height']>=720 else 'bt601')
    incoming='pc' if video.get('color_range')=='pc' else 'tv'
    return f'scale=in_color_matrix={assumed}:out_color_matrix=bt709:in_range={incoming}:out_range=tv,format=yuv420p'


def reference_command(source,destination):
    """Verbatim argument order from a05846d pipeline/stage_b/compress.py."""
    return ['ffmpeg','-y','-i',str(source),'-map','0:v:0','-map','0:a?',
        '-c:v','libx264','-profile:v','high','-level:v','4.0','-preset','medium',
        '-crf','23','-pix_fmt','yuv420p','-bf','0','-g','60','-keyint_min','60','-sc_threshold','0',
        '-x264-params','force-cfr=1','-video_track_timescale','15360','-c:a','copy',
        '-movflags','+faststart','-use_editlist','0','-brand','mp42','-map_metadata','-1',
        '-map_chapters','-1','-sn','-dn','-ignore_unknown','-fflags','+genpts',
        '-max_muxing_queue_size','9999','-f','mp4',str(destination)]


def command(source,destination,*,safe=True):
    cmd=reference_command(source,destination)
    i=cmd.index('-pix_fmt');cmd[i:i]=['-maxrate','1800k','-bufsize','1800k']
    # Never map arbitrary source tracks: the join already contains chosen track.
    cmd[cmd.index('0:a?')]='0:a:0'
    i=cmd.index('-c:a');cmd[i:i+2]=['-c:a','aac','-profile:a','aac_low','-b:a','128k','-ar','48000','-ac','2']
    if safe:
        info=probe(source);v=next(s for s in info['streams'] if s['codec_type']=='video')
        fps=min(Fraction(v['r_frame_rate']),Fraction(30))
        if fps<=0:raise ValueError('Missing original frame rate')
        cmd[cmd.index('-video_track_timescale')+1]=str(timescale(fps))
        vf=f"setpts=PTS-STARTPTS,fps={fps},scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,"+color_filter(v)+',setsar=1'
        # Explicit priming compensation prevents the no-edit-list muxer from
        # stretching video frame zero to compensate AAC encoder delay.
        duration=float(v.get('duration') or info['format']['duration'])
        cmd[-3:-3]=['-vf',vf,'-r',str(fps),'-fps_mode','cfr',
            '-af',f'aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB',
            '-avoid_negative_ts','make_zero','-color_range','tv','-colorspace','bt709',
            '-color_primaries','bt709','-color_trc','bt709','-max_interleave_delta','0']
    return cmd


def encode(source,destination,*,safe=True):
    cmd=command(source,destination,safe=safe)
    print('Terminal ClipForge-derived command: '+json.dumps(cmd),flush=True)
    subprocess.run(cmd,check=True)
    return cmd

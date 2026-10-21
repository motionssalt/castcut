"""Vendored ClipForge produce_merged_video; only fps/scale caps and AAC rate differ.
Narration inputs are replaced by lossless PCM of the selected original track.
No captions, compositing, enhancement, watermark, retiming or music are called.
The untouched upstream source lives beside this file for reproducible comparison.
"""
from __future__ import annotations
from pathlib import Path
from . import common
TARGET_FPS = 30
SOURCE_FPS = '30'
TARGET_PIX_FMT = 'yuv420p'
X264_PROFILE = 'high'
X264_LEVEL = '4.0'
X264_PRESET = 'veryfast'
X264_CRF = '18'
AAC_BITRATE = '128k'
AAC_SAMPLE_RATE = '48000'
AAC_CHANNELS = '2'
VOICEOVER_VOLUME = 1.0
MUSIC_VOLUME = 0.33
MIX_LIMITER_CEILING = 0.99

def produce_merged_video(src: str | Path, plan: list[dict], vo_wavs: list[str],
                         dst: str | Path, music: str | Path | None,
                         music_volume: float = MUSIC_VOLUME) -> None:
    """Cut every reconciled range from ``src``, retime per-cut video by its
    stretch factor, mute source audio, lay in the voiceover WAVs (padded to the
    cut length), concat everything, optionally mix ducked music under the
    voiceover, and write ONE mobile-safe MP4."""
    if not plan:
        raise common.StageBError("produce_merged_video called with no cuts")

    from pipeline.delivery_pass import color_filter
    from pipeline.export_checks import probe
    source_video=next(s for s in probe(src)['streams'] if s['codec_type']=='video')
    color=color_filter(source_video)
    n = len(plan)
    cmd: list[str] = ["ffmpeg", "-y"]

    # One `-ss <start> -to <end> -i <src>` block per cut. Input-side seek is
    # fast, and because the concat filter downstream re-encodes with fresh PTS
    # we inherit none of the edit-list / non-zero-start problems a stream-copy
    # pipeline would have.
    for p in plan:
        cmd += ["-ss", f"{p['start_seconds']:.3f}", "-to", f"{p['end_seconds']:.3f}", "-i", str(src)]

    for w in vo_wavs:
        cmd += ["-i", str(w)]

    music_idx = None
    if music:
        music_idx = 2 * n
        # `-stream_loop -1` must precede the input it applies to; loop short
        # music so the atrim below always has enough material to cut from.
        cmd += ["-stream_loop", "-1", "-i", str(music)]

    parts: list[str] = []
    concat_inputs: list[str] = []
    for i, p in enumerate(plan):
        stretch = p["stretch"]
        parts.append(
            f"[{i}:v:0]"
            f"setpts={stretch:.6f}*(PTS-STARTPTS),"
            f"fps={SOURCE_FPS},"
            f"scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
            f"trim=end_frame={p.get('frames', round(p['video_seconds']*float(__import__('fractions').Fraction(SOURCE_FPS))))},"
            f"setpts=PTS-STARTPTS,{color},"
            f"setsar=1"
            f"[v{i}]"
        )
        # Audio: normalized voiceover only — source audio is never mapped.
        # Pad to the exact post-resample sample count so concat sees equal A/V
        # segment lengths. end_sample avoids millisecond rounding truncating a
        # final consonant or vowel.
        parts.append(
            f"[{n + i}:a:0]"
            f"aformat=sample_fmts=fltp:sample_rates={AAC_SAMPLE_RATE}:channel_layouts=stereo,"
            f"aresample=async=1:first_pts=0,"
            f"apad,atrim=end_sample={p['audio_samples']},"
            f"volume={VOICEOVER_VOLUME}"
            f"[a{i}]"
        )
        concat_inputs.append(f"[v{i}][a{i}]")

    parts.append("".join(concat_inputs) + f"concat=n={n}:v=1:a=1[vcat][acat]")

    total_seconds = sum(p["video_seconds"] for p in plan)

    if music_idx is not None:
        parts.append(
            f"[{music_idx}:a:0]"
            f"aformat=sample_fmts=fltp:sample_rates={AAC_SAMPLE_RATE}:channel_layouts=stereo,"
            f"aresample=async=1:first_pts=0,"
            f"atrim=0:{total_seconds:.3f},"
            f"volume={music_volume:.6f}"
            f"[music_reduced]"
        )
        # bug-59: the bed is already gain-staged to a CONSTANT loudness-matched
        # level by resolve_music_volume() above (MUSIC_TO_VOICE_LOUDNESS_RATIO,
        # measured via LUFS so a quiet upload is boosted and a loud one is cut
        # down to the same target ratio). Previously this constant level was
        # then fed through sidechaincompress, ducking it further any time the
        # voiceover was present and letting it rise back in gaps — the
        # operator wants a flat, non-fluctuating background level for the
        # whole clip instead. music_reduced is mixed in directly at its
        # already-correct constant gain; no per-moment compression is applied.
        parts.append(
            f"[acat][music_reduced]amix=inputs=2:duration=first:normalize=0,"
            f"alimiter=limit={MIX_LIMITER_CEILING}"
            f"[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = "[acat]"

    filter_complex = ";".join(parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vcat]",
        "-map", audio_map,

        # Video: H.264 High@L4.0, CRF 18, yuv420p — universally decodable.
        "-r", SOURCE_FPS, "-fps_mode", "cfr",
        "-color_range", "tv", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:v", "libx264",
        "-profile:v", X264_PROFILE,
        "-level:v", X264_LEVEL,
        "-preset", X264_PRESET,
        "-crf", X264_CRF,
        "-pix_fmt", TARGET_PIX_FMT,
        # No B-frames -> PTS==DTS, start_time==0.
        "-bf", "0",
        "-g", str(TARGET_FPS * 2),
        "-keyint_min", str(TARGET_FPS * 2),
        "-sc_threshold", "0",
        "-x264-params", "force-cfr=1",
        "-video_track_timescale", "15360",

        # Audio: AAC-LC stereo 48 kHz 192 kbps.
        "-c:a", "aac",
        "-profile:a", "aac_low",
        "-b:a", AAC_BITRATE,
        "-ar", AAC_SAMPLE_RATE,
        "-ac", AAC_CHANNELS,

        # Container: MP4, faststart, mp42 brand, no edit lists; strip
        # metadata/chapters; drop subtitle/data tracks.
        "-movflags", "+faststart",
        "-use_editlist", "0",
        "-brand", "mp42",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-sn", "-dn", "-ignore_unknown",
        "-fflags", "+genpts",
        "-max_muxing_queue_size", "9999",
        "-f", "mp4",
        str(dst),
    ]

    common.sh(cmd)

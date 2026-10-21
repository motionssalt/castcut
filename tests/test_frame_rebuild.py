"""Real fractional-rate MKV -> direct terminal encoder -> reference MP4 regression."""
from fractions import Fraction
import json
import subprocess
import pytest
from pipeline.clipforge_export import export_segments
from pipeline.delivery_pass import timescale as frame_timescale, VERSION as REBUILD_VERSION
from pipeline.export_checks import validate, probe, mp4_boxes, flatten_boxes


@pytest.mark.parametrize('fps,timescale', [('30',15360),('24',15360),('24000/1001',24000),('30000/1001',30000)])
def test_exact_original_rate_clock(fps,timescale):
    assert frame_timescale(fps)==timescale
    assert (Fraction(timescale)/Fraction(fps)).denominator==1


def test_fractional_source_rebuilt_with_selected_audio_and_exact_ticks(tmp_path):
    source=tmp_path/'source.mkv'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=24000/1001:duration=5',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=5',
                    '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=5',
                    '-map','0:v','-map','1:a','-map','2:a','-c:v','libx264','-pix_fmt','yuv420p',
                    '-c:a','ac3',str(source)],check=True)
    destination=tmp_path/'fresh.mp4'
    report=export_segments(source,[(.125,1.375),(2.25,4.5)],destination,audio_track=1,temp_dir=tmp_path)
    assert report['rebuild_version']==REBUILD_VERSION
    assert report['source_audio_index']==1
    assert report['exact_frame_cadence']
    assert report['video_sample_duration_ticks']==[1001]
    video=probe(destination)['streams'][0]
    assert video['r_frame_rate']==video['avg_frame_rate']=='24000/1001'
    assert video['time_base']=='1/24000'
    assert report['all_packet_timestamps_contiguous']
    assert report['video_pts_equal_dts']
    assert report['video_peak_10s_kbps']<=2000
    assert report['video_average_kbps']<=2000
    assert report['full_decode_passed']
    assert destination.read_bytes()[4:12]==b'ftypmp42'
    assert not any(b['type'] in ('elst','edts','ctts') for b in flatten_boxes(mp4_boxes(destination)))
    assert len(probe(destination)['streams'])==2
    validate(destination,source=source)
    # No intermediate raw image forest / WAV / stale candidate remains.
    assert not list(tmp_path.glob('decoded-pcm-*'))
    assert 'pipe:0' not in report['terminal_command']
    assert 'rawvideo' not in report['terminal_command']
    assert report['max_av_interleave_bytes']<=500000
    assert video['color_space']=='bt709'
    assert not list(tmp_path.glob('*.compressing.mp4'))

"""Keep the phone experiments isolated and original controls unmodified."""
from unittest.mock import patch
from pipeline.playback_pack import base_command
from pipeline.delivery_pass import color_filter,timescale

INFO={'streams':[{'codec_type':'video','r_frame_rate':'24000/1001','height':1080}]}

def args(**kw):
    with patch('pipeline.playback_pack.probe',return_value=INFO):return base_command('same-source.mp4','same-output.mp4',**kw)


def test_profile_experiment_changes_one_argument():
    c=args();d=args(profile='high');e=args(profile='baseline')
    i=c.index('-profile:v')+1
    assert [(n,a,b) for n,(a,b) in enumerate(zip(c,d)) if a!=b]==[(i,'main','high')]
    assert [(n,a,b) for n,(a,b) in enumerate(zip(c,e)) if a!=b]==[(i,'main','baseline')]
    assert c[c.index('-bf')+1]=='0'


def test_timescale_and_faststart_experiments_are_isolated():
    c=args();f=args(timescale=False);i=c.index('-video_track_timescale')
    assert c[:i]+c[i+2:]==f
    g=args(faststart=False);i=c.index('-movflags')+1
    assert [(n,a,b) for n,(a,b) in enumerate(zip(c,g)) if a!=b]==[(i,'+faststart','0')]
    h=args(fragment=True)
    assert [(n,a,b) for n,(a,b) in enumerate(zip(c,h)) if a!=b]==[(i,'+faststart','+frag_keyframe+empty_moov+default_base_moof')]


def test_rate_clock_and_color_conversion_not_just_tags():
    assert timescale('24000/1001')==24000
    assert timescale('30000/1001')==30000
    assert timescale('24')==15360
    assert timescale('25')==25000
    assert 'tonemap=' in color_filter({'height':1080,'color_transfer':'smpte2084'})
    assert 'colorspace=' in color_filter({'height':1080,'color_transfer':'bt709','color_space':'bt470bg','color_primaries':'bt470bg'})
    assert 'out_color_matrix=bt709' in color_filter({'height':1080})
    assert 'in_color_matrix=bt601' in color_filter({'height':480})

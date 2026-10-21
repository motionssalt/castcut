"""Build and publish reproducible small phone tests, with intact real controls.

No AI media generation. Every command and ffprobe result is retained. C-L use
identical first 10 seconds of the actual bad export; B/M preserve original bytes
so the test cannot accidentally repair the known-good/known-bad controls.
"""
from __future__ import annotations
import argparse, concurrent.futures, datetime, hashlib, json, os, shutil, subprocess, time, zipfile
from fractions import Fraction
from pathlib import Path
from .delivery_pass import reference_command, command as delivery_command
from .export_checks import probe, validate
from .playback_inspect import inspect, differences
from .verify_delivery import verify


def run(cmd):
    print(json.dumps(cmd),flush=True)
    result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if result.returncode: raise RuntimeError('Command failed: '+json.dumps(cmd)+'\n'+result.stderr[-2500:])


def fetch_asset(repo,asset_id,path,token=None):
    env=dict(os.environ)
    if token:env['GH_TOKEN']=token
    with open(path,'wb') as f:
        subprocess.run(['gh','api',f'repos/{repo}/releases/assets/{int(asset_id)}','-H','Accept: application/octet-stream'],env=env,stdout=f,check=True)


def base_command(source,dest,profile='main',timescale=True,faststart=True,fragment=False,height=1080,audio=True,rate=48000,channels=2,codec='libx264'):
    v=next(s for s in probe(source)['streams'] if s['codec_type']=='video')
    fps=min(Fraction(v['r_frame_rate']),Fraction(30));frames=round(10*fps);duration=float(frames/fps)
    from .delivery_pass import color_filter, fps_mode_flag
    vf=f"setpts=PTS-STARTPTS,fps={fps},scale=w='min({1920 if height==1080 else 1280},iw)':h='min({height},ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,"+color_filter(v)+',setsar=1'
    cmd=['ffmpeg','-y','-v','error','-threads','2','-i',str(source),'-t',f'{duration:.9f}',
         '-map','0:v:0','-vf',vf,'-r',str(fps)]+fps_mode_flag('cfr')+['-c:v',codec]
    if codec=='libx264':
        cmd+=['-profile:v',profile,'-level:v','4.1','-preset','medium','-bf','0','-g','60','-keyint_min','60','-sc_threshold','0']
    cmd+=['-pix_fmt','yuv420p','-b:v','1800k','-maxrate','1800k','-bufsize','1800k']
    if timescale:cmd+=['-video_track_timescale',str(fps.numerator)]
    if audio:
        cmd+=['-map','0:a:0','-c:a','aac','-profile:a','aac_low','-b:a','128k','-ar',str(rate),'-ac',str(channels),
              '-af',f'aresample={rate}:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB']
    else:cmd+=['-an']
    flags='+frag_keyframe+empty_moov+default_base_moof' if fragment else ('+faststart' if faststart else '0')
    return cmd+['-movflags',flags,'-use_editlist','0','-brand','mp42','-avoid_negative_ts','make_zero',
                '-map_metadata','-1','-map_chapters','-1','-sn','-dn','-max_interleave_delta','0',
                '-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-f','mp4',str(dest)]


def build(castcut,clipforge,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    # Restrict automatic filter threading as well as libx264, without altering
    # encoding settings; this avoids runner-wide thread explosions in parallel.
    if hasattr(os,'sched_setaffinity'):os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:4])
    variants=[]
    spec=[('C_h264-main_cfr_faststart_timescale',{},'BASE: Main@4.1, 8bit420, source CFR, matching clock, zero PTS, AAC48k stereo, faststart.'),
          ('D_h264-high',{'profile':'high'},'C except High profile.'),
          ('E_h264-baseline_cavlc_no-bframes',{'profile':'baseline'},'C except Baseline (implies CAVLC and no B frames).'),
          ('F_h264-main_default-timescale',{'timescale':False},'C except omit explicit track timescale; automatic clock may equal C, inspect report.'),
          ('G_h264-main_moov-at-end',{'faststart':False},'C except moov at end (no faststart).'),
          ('H_h264-main_fragmented',{'fragment':True},'C except fragmented MP4 with empty_moov.'),
          ('I_h264-main_720p',{'height':720},'C except 720p maximum, never upscale.'),
          ('J_h264-main_no-audio',{'audio':False},'C except no audio stream.'),
          ('K_h264-main_aac-44100-stereo',{'rate':44100},'C except AAC-LC stereo 44.1kHz.'),
          ('K2_h264-main_aac-48000-mono',{'channels':1},'C except AAC-LC mono 48kHz.'),
          ('L_mpeg4-part2_not-h264',{'codec':'mpeg4'},'C except MPEG-4 Part 2 codec (H264 profile and level flags necessarily removed).')]
    jobs=[]
    for name,options,description in spec:
        path=out/(name+'.mp4');jobs.append((path,base_command(castcut,path,**options),description))
    path=out/'A_castcut_through_exact-clipforge-last-pass.mp4'
    cmd=reference_command(castcut,path)
    i=cmd.index('-pix_fmt');cmd[i:i]=['-maxrate','1800k','-bufsize','1800k']
    cmd[-3:-3]=['-t','10']
    jobs.append((path,cmd,'Real bad CastCut export -> literal ClipForge final command; only owner bitrate ceiling and diagnostic 10s trim added. Audio copied as ClipForge does (already AAC128). No smaller-source fallback.'))
    path=out/'N_actual-shipped-default_clipforge-policy.mp4'
    cmd=delivery_command(castcut,path);cmd[-3:-3]=['-t','10']
    jobs.append((path,cmd,'Additional actual shipped High@4.0 policy pass; compare to C to test the production default itself.'))
    def make(job):
        path,cmd,description=job;run(cmd)
        run(['ffmpeg','-v','error','-xerror','-i',str(path),'-f','null','-'])
        report=inspect(path)
        report['probe']['format'].pop('filename',None)
        path.with_suffix('.inspection.json').write_text(json.dumps(report,indent=2)+'\n')
        return {'name':path.name,'description':description,'command':cmd,'sha256':report['sha256'],'size_bytes':path.stat().st_size,'ffprobe':report['probe'],'max_av_byte_separation':report['max_nearest_av_byte_separation']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:variants.extend(pool.map(make,jobs))
    for prefix,source,desc in [('B',clipforge,'Untouched real ClipForge final.mp4, byte-identical known-good control; longer/larger to avoid altering the control.'),('M',castcut,'Untouched real pre-fix CastCut export, byte-identical owner-reported known-bad control; intentionally larger than 3MB to avoid repairing it by remuxing.')]:
        path=out/(prefix+'_untouched-real-'+('clipforge-known-good' if prefix=='B' else 'castcut-known-bad')+'.mp4')
        shutil.copyfile(source,path)
        entry=make((path,['true'],desc));entry['command']=['byte-for-byte-copy',str(source),str(path)];variants.append(entry)
    variants.sort(key=lambda x:x['name'])
    (out/'variants.json').write_text(json.dumps(variants,indent=2)+'\n')
    checksums=''.join(f"{x['sha256']}  {x['name']}\n" for x in variants)
    (out/'SHA256SUMS.txt').write_text(checksums)
    readme=['CASTCUT PHONE PLAYBACK TEST PACK','Try in this order: B, C, A, M; then N, D, E, I, J, K, K2, F, G, H, L.',
        'Download fully before opening. Test directly from Android Files / normal player, then IMPORT into the editing app. Do not use VLC as evidence and do not convert the files first.',
        'B/M are intact real controls and exceed the usual 10s/1-3MB target deliberately. All other files target ~10s and <=2000kbps video. N is an additional actual-default diagnostic.',
        'Record phone model, Android version, normal player name/version, editing app name/version, download route, actual downloaded byte size and error text.',
        'If size or SHA256 differs from variants.json / SHA256SUMS.txt, repeat download before interpreting playback. Compare L to Audio Video Factory output codec, if available.',
        'Interpretation: B fails => delivery/device/app/control issue; C works and D fails => profile sensitivity; I only works => resolution/decoder limitation; J works => audio path; K/K2 differences => sample rate/channel handling; F/G/H differences => clock/container sensitivity. These are hypotheses, not proven root causes.',
        '', 'VARIANTS:']
    for x in variants:readme.append(f"{x['name']} | {x['size_bytes']} bytes | SHA256 {x['sha256']}\n  {x['description']}")
    readme+=['','RESULTS TO SEND BACK','File | normal phone player yes/no | editing app yes/no | notes']
    readme += [f"{x['name']} | ___ | ___ | ___" for x in variants]
    (out/'README.txt').write_text('\n'.join(readme)+'\n')
    # Archive matches ClipForge delivery: an unchanged MP4 inside ZIP, not another encode.
    with zipfile.ZipFile(out/'playback-test-pack.zip','w',compression=zipfile.ZIP_STORED) as z:
        for path in sorted(out.iterdir()):
            if path.suffix in ('.mp4','.txt') or path.name=='variants.json':z.write(path,path.name)
    return variants


def publish(out,tag,variants):
    repo=os.environ.get('GITHUB_REPOSITORY','motionssalt/castcut')
    exists=subprocess.run(['gh','release','view',tag,'-R',repo],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    if not exists:run(['gh','release','create',tag,'-R',repo,'--title','CastCut phone playback test pack','--notes-file',str(out/'README.txt')])
    for path in sorted(out.iterdir()):
        if path.is_file():run(['gh','release','upload',tag,str(path),'-R',repo,'--clobber'])
    for x in variants:
        receipt=verify(out/x['name'],tag,repository=repo);x['delivery']=receipt
    release=json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/releases/tags/{tag}']))
    return release['html_url']


def main():
    p=argparse.ArgumentParser();p.add_argument('--castcut',default='');p.add_argument('--clipforge',default='');p.add_argument('--out',default='work/playback-pack');p.add_argument('--publish',action='store_true');p.add_argument('--tag',default='playback-test-pack');a=p.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);inputs=out.parent/'playback-inputs';inputs.mkdir(exist_ok=True)
    castcut=Path(a.castcut) if a.castcut else inputs/'castcut-real.mp4';clipforge=Path(a.clipforge) if a.clipforge else inputs/'clipforge-real.mp4'
    if not a.castcut:fetch_asset('motionssalt/castcut',661445503,castcut)
    if not a.clipforge:fetch_asset('motionssalt/clipforge',654439970,clipforge,os.environ.get('CLIPFORGE_READ_TOKEN'))
    variants=build(castcut,clipforge,out);url=publish(out,a.tag,variants) if a.publish else ''
    root=Path('BUILD_STATE.json');s=json.loads(root.read_text());state=s['playback_diagnosis'];state['last_updated']=datetime.datetime.now(datetime.timezone.utc).isoformat();state['test_pack']={'release':url,'tag':a.tag,'variants':variants,'workflow_run_id':os.environ.get('GITHUB_RUN_ID','local'),'controls':{'B_asset_id':654439970,'M_asset_id':661445503}};state['done'].append('Built compatibility pack with exact commands/probes and verified published bytes' if a.publish else 'Built compatibility pack locally');root.write_text(json.dumps(s,indent=2)+'\n')
    if a.publish:
        from .playback_state import commit
        commit({'test_pack':state['test_pack']},'Record published phone compatibility pack commands probes and release readback')
    print('TEST PACK RELEASE: '+url,flush=True)
if __name__=='__main__':main()

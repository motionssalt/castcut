"""ClipForge baseline: 6 seconds, six 640-wide panels, 3x2, 2px black padding/margin, JPEG q5.
CastCut adds per-panel exact requested source timestamps and range filenames.
"""
import json
import math
import subprocess
from pathlib import Path

SHEET_FORMAT = {'window_seconds':6,'frames_per_sheet':6,'layout':'3x2','panel_width':640,'padding':2,'margin':2,'format':'JPEG','ffmpeg_qscale':5,'order':'left to right, top to bottom','labels':'burned source HH:MM:SS.mmm; partial final window evenly sampled at start+i*remaining/6'}
DESCRIPTION = 'screenshots.zip contains sheet_START-END.jpg: one 6-second source window, six consecutive frames in a 3x2 grid (left-to-right, top-to-bottom), up to 640px per panel, JPEG q5. Every panel burns its exact source timestamp HH:MM:SS.mmm. The final shorter window is evenly sampled into six panels. Use the visible action and panel timestamps for JSON start/end seconds, verify boundaries against scene_index.json and transcript.json; never use grid numbers as timestamps.'


def extract(src, out_dir, duration):
    out = Path(out_dir)
    out.mkdir(parents=True,exist_ok=True)
    for p in out.glob('sheet_*.jpg'): p.unlink()
    count = math.ceil(duration/6)
    complete = int(duration//6)
    # Text runs after fps so labels describe sampling timestamps, before tile.
    label = "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.85:x=8:y=8:text='%{pts\\:hms}'"
    scale = "scale='min(640,iw)':-2"
    tile = 'tile=3x2:padding=2:margin=2:color=black'
    if complete:
        subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(src),'-t',str(complete*6),'-vf',f'fps=1:start_time=0,{scale},{label},{tile}','-frames:v',str(complete),'-q:v','5','-start_number','0',str(out/'grid_%06d.jpg')],check=True)
    sheets = []
    for i in range(count):
        start = i*6
        end = min(duration,start+6)
        name = f'sheet_{start:06d}-{end:010.3f}.jpg'
        target = out/name
        times = [round(start+j*(end-start)/6,3) for j in range(6)]
        if i < complete:
            (out/f'grid_{i:06d}.jpg').replace(target)
        else:
            rate = 6/(end-start)
            # Pad clones for duration shorter than a source frame; still six valid panels.
            vf = f'fps={rate}:start_time=0,{scale},tpad=stop_mode=clone:stop_duration=6,setpts=PTS+{start}/TB,{label},{tile}'
            subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-ss',str(start),'-i',str(src),'-vf',vf,'-frames:v','1','-q:v','5',str(target)],check=True)
        if not target.is_file() or not target.stat().st_size:
            raise RuntimeError(f'Missing screenshot sheet {name}')
        sheets.append({'name':name,'start':start,'end':round(end,3),'timestamps':times})
    (out.parent/'sheets.json').write_text(json.dumps({'format':SHEET_FORMAT,'sheets':sheets},indent=2))
    print(f'[sheets] {len(sheets)} sheets = ceil({duration}/6); 6 labeled panels, 3x2, 640px JPEG q5',flush=True)
    return sheets

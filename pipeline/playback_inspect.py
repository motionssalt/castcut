"""Reproducible non-asserting deep MP4/packet/SPS inspection for phone diagnosis."""
from __future__ import annotations
import argparse, bisect, collections, hashlib, json, struct, subprocess
from pathlib import Path
from .export_checks import probe, mp4_boxes, flatten_boxes


def inspect(path):
    path=Path(path)
    info=probe(path)
    cmd=['ffprobe','-v','error','-show_packets','-show_entries','packet=stream_index,pts,dts,duration,pts_time,dts_time,duration_time,size,pos,flags','-of','json',str(path)]
    packets=json.loads(subprocess.check_output(cmd))['packets']
    groups=collections.defaultdict(list)
    for p in packets: groups[p['stream_index']].append(p)
    packet_summary={}
    for index,group in groups.items():
        dts=[float(p['dts_time']) for p in group if 'dts_time' in p]
        packet_summary[str(index)]={'count':len(group),'first':group[0],'last':group[-1],
            'duration_ticks':dict(collections.Counter(p.get('duration','missing') for p in group)),
            'negative_timestamps':sum(float(p.get('dts_time',0))<0 or float(p.get('pts_time',0))<0 for p in group),
            'nonmonotonic_dts':sum(b<=a for a,b in zip(dts,dts[1:])),
            'keyframe_indices':[i for i,p in enumerate(group) if 'K' in p.get('flags','')][:100]}
    # Compare byte locations of temporally nearest audio/video samples.
    a=groups.get(1,[]);v=groups.get(0,[]);at=[float(p.get('pts_time',0)) for p in a]
    separation=[]
    if at:
        for p in v:
            t=float(p.get('pts_time',0));k=bisect.bisect_left(at,t)
            nearest=min((i for i in [k-1,k] if 0<=i<len(a)),key=lambda i:abs(at[i]-t))
            if abs(at[nearest]-t)<0.1 and 'pos' in p and 'pos' in a[nearest]:
                separation.append(abs(int(p['pos'])-int(a[nearest]['pos'])))
    boxes=mp4_boxes(path)
    details=[]
    data=path.read_bytes()
    # Traverse avc1 sample entries explicitly; normal box walkers stop at stsd.
    for box in flatten_boxes(boxes):
        start=box['offset']+8;end=box['offset']+box['size'];kind=box['type'];payload=data[start:end]
        item={'type':kind,'offset':box['offset']}
        if kind=='mdhd':
            offset=20 if payload[0]==1 else 12
            item['timescale']=struct.unpack_from('>I',payload,offset)[0]
        elif kind=='stts':
            count=struct.unpack_from('>I',payload,4)[0]
            item['entries']=[list(struct.unpack_from('>II',payload,8+i*8)) for i in range(count)]
        elif kind=='elst': item['payload_hex']=payload.hex()
        elif kind=='stsd':
            pos=start+8
            while pos+8<=end:
                size,typ=struct.unpack_from('>I4s',data,pos)
                if size<8 or pos+size>end: break
                if typ in (b'avc1',b'avc3',b'mp4v'):
                    child=pos+86
                    while child+8<=pos+size:
                        n,t=struct.unpack_from('>I4s',data,child)
                        if n<8 or child+n>pos+size: break
                        q=data[child+8:child+n]
                        d={'type':t.decode('latin1'),'payload_hex':q.hex()}
                        if t==b'avcC' and len(q)>4: d.update(profile=q[1],compatibility=q[2],level=q[3],nal_length=(q[4]&3)+1)
                        details.append(d);child+=n
                pos+=size
            continue
        else: continue
        details.append(item)
    trace_cmd=['ffmpeg','-v','verbose','-i',str(path),'-map','0:v:0','-c:v','copy','-bsf:v','trace_headers','-frames:v','1','-f','null','-']
    trace=subprocess.run(trace_cmd,capture_output=True,text=True)
    sps=[line.split('] ',1)[-1] for line in trace.stderr.splitlines() if '[trace_headers' in line]
    chapter_cmd=['ffprobe','-v','error','-show_chapters','-of','json',str(path)]
    return {'file':path.name,'sha256':hashlib.sha256(data).hexdigest(),'size_bytes':len(data),
            'probe':info,'boxes':boxes,'sample_tables_and_configuration':details,'packets':packet_summary,
            'max_nearest_av_byte_separation':max(separation,default=0),'sps_pps_vui':sps,
            'chapters':json.loads(subprocess.check_output(chapter_cmd)).get('chapters',[]),
            'inspection_commands':[cmd,trace_cmd,chapter_cmd]}


def differences(left,right):
    rows=[]
    def walk(a,b,key):
        if isinstance(a,dict) and isinstance(b,dict):
            for k in sorted(set(a)|set(b)): walk(a.get(k),b.get(k),f'{key}.{k}' if key else k)
        elif isinstance(a,list) and isinstance(b,list) and len(a)==len(b) and len(a)<100:
            for i,(x,y) in enumerate(zip(a,b)):walk(x,y,f'{key}[{i}]')
        elif a!=b: rows.append({'field':key,'clipforge':a,'castcut':b})
    walk(left,right,'')
    return rows


def main():
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args()
    Path(a.output).write_text(json.dumps(inspect(a.input),indent=2)+'\n')
if __name__=='__main__':main()

# Real reference / old CastCut / new CastCut comparison
Not a hardware playback result. Full SPS/PPS/VUI, sample tables, atom trees, packets and all differences are in the adjacent JSON files.
| Video property | ClipForge | CastCut before | CastCut after |
|---|---|---|---|
| codec_name | h264 | h264 | h264 |
| codec_tag_string | avc1 | avc1 | avc1 |
| profile | High | High | High |
| level | 40 | 40 | 40 |
| pix_fmt | yuv420p | yuv420p | yuv420p |
| has_b_frames | 0 | 0 | 0 |
| sample_aspect_ratio | 1:1 | 1:1 | 1:1 |
| time_base | 1/15360 | 1/384000 | 1/24000 |
| r_frame_rate | 30/1 | 24000/1001 | 24000/1001 |
| avg_frame_rate | 30/1 | 24000/1001 | 24000/1001 |
| start_time | 0.000000 | 0.000000 | 0.000000 |
| color_range | tv | unspecified | tv |
| color_space | unspecified | unspecified | bt709 |
| color_primaries | bt709 | unspecified | bt709 |
| color_transfer | bt709 | unspecified | bt709 |
| Top-level boxes | ftyp,moov,free,mdat | ftyp,moov,free,mdat | ftyp,moov,free,mdat |
| Major brand | mp42 | mp42 | mp42 |
| Max nearest A/V separation bytes | 94542 | 141257 | 134610 |
| Streams | video:h264,audio:aac | video:h264,audio:aac | video:h264,audio:aac |
| Audio sample rate | 48000 | 48000 | 48000 |
| Audio channels | 2 | 2 | 2 |
| AAC measured bitrate | 197181 | 129933 | 130193 |

The new export keeps the actual ClipForge High@4.0/bf0/GOP60/mp42/faststart/no-edit-list contract. It is NOT bit-identical to the reference: source aspect/fps/size differ, owner bitrate and AAC128 differ, complete BT709 color declarations are added. A 24000-Hz track clock is the documented original-fps exception, replacing previous unnecessarily large 384000-Hz LCM clock; integer 24/25/30 use upstream15360 when exact. No raw-picture/PCM terminal pipe remains. The upstream command template is regression-tested verbatim; default normalization additions are explicitly recorded.
Root cause still unproven: profile, frame rate/timebase, metadata/color, SPS/VUI, resolution and audio compatibility remain hypotheses until the owner reports phone/editor tests. Both original delivered files are intact. The new file also traversed the actual live site route byte-for-byte with correct MIME, length, hash, and HTTP Range support.
Unknown-color assumption: untagged HD treated as BT709; untagged SD as BT601, then converted. Untagged content cannot be identified perfectly from absent metadata. Tagged SDR is converted; tagged PQ/HLG is tone-mapped.


## Full production faces export

Run39555912076 succeeded with125 actual face-selected hard-cut ranges. The
850.517354-second result is205605265bytes (14.504MB/min), High@4.0,
yuv420p, zero B frames,23.976023976fps with1001 ticks per frame at24000Hz,
mp42, faststart, no edit lists, and exactly one chosen-track AAC-LC48kHz stereo
stream. Average video1799.705kbps; maximum rolling10s video1903.9624kbps.
Maximum nearest A/V byte separation188549. Full decode and all packet gates
passed. Live Cloudflare download exactly matched release/job SHA256 and size.
The two flows therefore share the validated final-file contract; neither is
claimed to have passed physical-phone/editor testing. Detailed inspection and
full reference diff: faces-after.json, differences-faces-after.json.

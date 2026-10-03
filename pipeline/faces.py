"""CastCut Mode 1 Pipeline A — anime face detection + embedding clustering.

Research decision (recorded in BUILD_STATE.json):
  Detection : hysts/anime-face-detector (YOLOv3, CPU ~0.5-2s/frame at 480p,
              mmdet-based, permissive code). Fallback: OpenCV
              lbpcascade_animeface (fast but poor recall — last resort only).
  Embeddings: deepghs CCIP via the `imgutils` package (ccip_extract_feature,
              512-d, ONNX-friendly, Apache-2.0). Fallback: HSV color-histogram
              embeddings (weak but keeps the pipeline alive offline).
  Clustering: online nearest-centroid assignment with cosine threshold
              (0.35 for CCIP). Deterministic, no sklearn needed.

Sampling: frames at --fps (default 1.0) scaled to 480p wide. Presence ranges
are merged with --presence-gap seconds tolerance.

Output: jobs/<job>/clips/manifest.json — one JSON with embedded 112px WebP
base64 thumbnails (a few KB each), so the gallery loads in ONE request.
"""
from __future__ import annotations

import argparse
import base64
import glob
import io
import json
import math
import os
import sys

from .utils import merge_intervals, probe_video, run


def _load_detector():
    try:
        from anime_face_detector import create_detector  # type: ignore

        det = create_detector("yolov3", device="cpu")

        def detect(img_bgr):
            preds = det(img_bgr)
            out = []
            for p in preds:
                x0, y0, x1, y1 = p["bbox"]
                out.append((float(x0), float(y0), float(x1), float(y1), float(p.get("score", 1.0))))
            return out

        return detect, "anime-face-detector(yolov3)"
    except Exception as exc:  # pragma: no cover
        print(f"[faces] anime_face_detector unavailable ({exc}); falling back to LBP cascade", flush=True)
        import cv2
        import urllib.request

        cascade_path = os.path.join(os.path.dirname(__file__), "cascades", "lbpcascade_animeface.xml")
        if not os.path.exists(cascade_path):
            os.makedirs(os.path.dirname(cascade_path), exist_ok=True)
            urllib.request.urlretrieve(
                "https://raw.githubusercontent.com/motionssalt/clipforge/main/pipeline/stage_b/cascades/lbpcascade_animeface.xml",
                cascade_path,
            )
        cascade = cv2.CascadeClassifier(cascade_path)

        def detect(img_bgr):
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(32, 32))
            return [(float(x), float(y), float(x + w), float(y + h), 0.5) for (x, y, w, h) in boxes]

        return detect, "lbpcascade_animeface(fallback)"


def _load_embedder():
    try:
        from imgutils.metrics import ccip_extract_feature  # type: ignore
        import numpy as np

        def embed(img_bgr):
            import cv2

            rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            from PIL import Image

            vec = np.asarray(ccip_extract_feature(Image.fromarray(rgb)), dtype="float32")
            return vec

        return embed, "ccip(imgutils/deepghs)", 0.35
    except Exception as exc:
        print(f"[faces] CCIP unavailable ({exc}); falling back to HSV histogram embeddings", flush=True)
        import cv2
        import numpy as np

        def embed(img_bgr):
            hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 4], [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            return hist.flatten().astype("float32")

        return embed, "hsv-histogram(fallback)", 0.30


def _cosine(a, b):
    import numpy as np

    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _thumb(img_bgr, box, size=112, pad_ratio=0.25) -> str:
    import cv2

    h, w = img_bgr.shape[:2]
    x0, y0, x1, y1 = box
    bw, bh = (x1 - x0), (y1 - y0)
    side = max(bw, bh) * (1 + pad_ratio)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    xa = max(0, int(cx - side / 2))
    ya = max(0, int(cy - side / 2))
    xb = min(w, int(cx + side / 2))
    yb = min(h, int(cy + side / 2))
    crop = img_bgr[ya:yb, xa:xb]
    if crop.size == 0:
        crop = img_bgr[max(0, int(y0)):min(h, int(y1)), max(0, int(x0)):min(w, int(x1))]
    crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".webp", crop, [cv2.IMWRITE_WEBP_QUALITY, 78])
    if not ok:
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return "data:image/webp;base64," + base64.b64encode(buf.tobytes()).decode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    ap.add_argument("--fps", type=float, default=1.0, help="frame sampling rate (tunable)")
    ap.add_argument("--presence-gap", type=float, default=3.0, help="seconds gap to merge presence (tunable)")
    ap.add_argument("--min-appearances", type=int, default=3, help="drop clusters with fewer detections")
    ap.add_argument("--max-frames", type=int, default=3600, help="safety cap on sampled frames")
    args = ap.parse_args()

    import cv2
    import numpy as np

    src = None
    for cand in glob.glob(os.path.join(args.work_dir, "source.*")):
        if os.path.splitext(cand)[1].lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"):
            src = cand
            break
    if not src:
        raise RuntimeError("no source video found in work dir")

    meta = probe_video(src)
    duration = meta["duration"]
    print(f"[faces] source: {duration:.1f}s {meta['width']}x{meta['height']}", flush=True)

    frames_dir = os.path.join(args.work_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    fps = max(0.5, min(args.fps, 2.0))
    est = min(int(duration * fps) + 2, args.max_frames)
    if duration * fps > args.max_frames:
        fps = args.max_frames / duration
        print(f"[faces] long video — reducing sampling to {fps:.3f} fps to stay under {args.max_frames} frames", flush=True)
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", src,
         "-vf", f"fps={fps},scale=480:-2", "-q:v", "5", os.path.join(frames_dir, "%06d.jpg")])
    frames = sorted(glob.glob(os.path.join(frames_dir, "*.jpg")))[: args.max_frames]
    print(f"[faces] sampled {len(frames)} frames (~{est} expected)", flush=True)

    detect, det_name = _load_detector()
    embed, emb_name, cos_threshold = _load_embedder()
    print(f"[faces] detector={det_name} embedder={emb_name} thr={cos_threshold}", flush=True)

    clusters = []  # each: {id, centroid, count, best_score, best_frame, best_box, times: [t]}
    for idx, frame_path in enumerate(frames):
        t = idx / fps
        img = cv2.imread(frame_path)
        if img is None:
            continue
        try:
            boxes = detect(img)
        except Exception as exc:
            print(f"[faces] detect failed on frame {idx}: {exc}", flush=True)
            continue
        for (x0, y0, x1, y1, score) in boxes:
            if (x1 - x0) < 20 or (y1 - y0) < 20:
                continue
            face = img[max(0, int(y0)):int(y1), max(0, int(x0)):int(x1)]
            if face.size == 0:
                continue
            try:
                vec = embed(face)
            except Exception:
                continue
            best_i, best_sim = -1, -1.0
            for i, cl in enumerate(clusters):
                sim = _cosine(vec, cl["centroid"])
                if sim > best_sim:
                    best_i, best_sim = i, sim
            if best_i >= 0 and (1.0 - best_sim) <= cos_threshold:
                cl = clusters[best_i]
                cl["centroid"] = (cl["centroid"] * cl["count"] + vec) / (cl["count"] + 1)
                cl["count"] += 1
                cl["times"].append(t)
                if score > cl["best_score"]:
                    cl.update(best_score=score, best_frame=frame_path, best_box=(x0, y0, x1, y1))
            else:
                clusters.append({
                    "centroid": vec, "count": 1, "times": [t],
                    "best_score": score, "best_frame": frame_path, "best_box": (x0, y0, x1, y1),
                })
        if idx % 60 == 0:
            print(f"[faces] frame {idx}/{len(frames)} — {len(clusters)} clusters", flush=True)

    clusters = [c for c in clusters if c["count"] >= args.min_appearances]
    clusters.sort(key=lambda c: c["count"], reverse=True)

    characters = []
    for n, cl in enumerate(clusters, start=1):
        img = cv2.imread(cl["best_frame"])
        thumb = _thumb(img, cl["best_box"]) if img is not None else None
        points = sorted(cl["times"])
        ranges = merge_intervals([(t, t + 1.0 / fps) for t in points], gap=args.presence_gap)
        screen_time = sum(e - s for s, e in ranges)
        characters.append({
            "id": f"char{n}",
            "appearances": cl["count"],
            "screen_time": round(screen_time, 1),
            "first_seen": round(points[0], 1),
            "ranges": [[round(s, 2), round(e, 2)] for s, e in ranges],
            "thumb": thumb,
        })

    manifest = {
        "version": 1,
        "job_id": args.job_id,
        "detector": det_name,
        "embedder": emb_name,
        "sample_fps": round(fps, 3),
        "presence_gap": args.presence_gap,
        "duration": round(duration, 1),
        "width": meta["width"],
        "height": meta["height"],
        "characters": characters,
    }
    out_dir = os.path.join(args.jobs_root, args.job_id, "clips")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "manifest.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"[faces] wrote {out_path}: {len(characters)} characters, "
          f"{os.path.getsize(out_path)} bytes (single-request gallery manifest)", flush=True)


if __name__ == "__main__":
    sys.exit(main())

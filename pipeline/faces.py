"""CastCut Mode 1 Pipeline A — face detection + embedding clustering.

Supports two content types:
  1. anime:
     Detection:  hysts/anime-face-detector (YOLOv3, CPU) -> fallback OpenCV lbpcascade_animeface
     Embeddings: deepghs CCIP via imgutils -> fallback HSV color-histogram
  2. irl (real-world / live-action):
     Detection:  OpenCV YuNet (face_detection_yunet_2023mar.onnx, fast 5ms CPU)
     Embeddings: OpenCV SFace ArcFace 128-d (face_recognition_sface_2021dec.onnx)
     Clustering: ArcFace cosine similarity threshold 0.363
     Filters:    Drops tiny background detections (<32px), low confidence (<0.60),
                 and extreme motion blur (Laplacian variance < 30).

Thumbnails: 112px WebP base64 data URIs (< 3KB each) embedded in manifest.json.
Clustering: Online nearest-centroid assignment with cosine distance.
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


def _load_anime_detector():
    try:
        from anime_face_detector import create_detector  # type: ignore
        det = create_detector("yolov3", device="cpu")

        def detect(img_bgr):
            preds = det(img_bgr)
            out = []
            for p in preds:
                bbox = p.get("bbox") if isinstance(p, dict) else p
                if hasattr(bbox, "tolist"):
                    bbox = bbox.tolist()
                elif hasattr(bbox, "__iter__") and not isinstance(bbox, (str, bytes)):
                    bbox = list(bbox)
                else:
                    continue
                if len(bbox) >= 5:
                    x0, y0, x1, y1 = bbox[:4]
                    score = bbox[4]
                elif len(bbox) == 4:
                    x0, y0, x1, y1 = bbox
                    score = p.get("score", 1.0) if isinstance(p, dict) else 1.0
                else:
                    continue
                out.append((float(x0), float(y0), float(x1), float(y1), float(score)))
            return out

        return detect, "anime-face-detector(yolov3)"
    except Exception as exc:
        print(f"[faces] anime-face-detector unavailable ({exc}); trying imgutils.detect", flush=True)

    try:
        from imgutils.detect import detect_faces  # type: ignore

        def detect(img_bgr):
            import cv2
            rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            from PIL import Image
            preds = detect_faces(Image.fromarray(rgb))
            return [(float(x0), float(y0), float(x1), float(y1), float(score))
                    for ((x0, y0, x1, y1), _label, score) in preds]

        return detect, "imgutils.detect_faces"
    except Exception as exc:
        print(f"[faces] imgutils.detect unavailable ({exc}); falling back to LBP cascade", flush=True)

    import cv2
    cascade_path = os.path.join(os.path.dirname(__file__), "cascades", "lbpcascade_animeface.xml")
    cascade = cv2.CascadeClassifier(cascade_path) if os.path.isfile(cascade_path) and hasattr(cv2, "CascadeClassifier") else None

    def detect(img_bgr):
        if cascade is None or cascade.empty():
            return []
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(32, 32))
        return [(float(x), float(y), float(x + w), float(y + h), 0.5) for (x, y, w, h) in boxes]

    return detect, "lbpcascade_animeface(fallback)"


def _load_anime_embedder():
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


def _ensure_irl_models(models_dir: str) -> tuple[str, str]:
    import urllib.request
    os.makedirs(models_dir, exist_ok=True)
    yunet_path = os.path.join(models_dir, "face_detection_yunet.onnx")
    sface_path = os.path.join(models_dir, "face_recognition_sface.onnx")
    
    yunet_urls = [
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ]
    sface_urls = [
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    ]
    
    if not os.path.isfile(yunet_path) or os.path.getsize(yunet_path) < 10000:
        for url in yunet_urls:
            try:
                print(f"[faces] Downloading YuNet model from {url}...", flush=True)
                urllib.request.urlretrieve(url, yunet_path)
                if os.path.getsize(yunet_path) > 10000:
                    break
            except Exception as e:
                print(f"[faces] Download failed for {url}: {e}", flush=True)

    if not os.path.isfile(sface_path) or os.path.getsize(sface_path) < 100000:
        for url in sface_urls:
            try:
                print(f"[faces] Downloading SFace model from {url}...", flush=True)
                urllib.request.urlretrieve(url, sface_path)
                if os.path.getsize(sface_path) > 100000:
                    break
            except Exception as e:
                print(f"[faces] Download failed for {url}: {e}", flush=True)

    return yunet_path, sface_path


def _load_irl_detector_and_embedder():
    import cv2
    import numpy as np

    models_dir = os.path.join(os.path.dirname(__file__), "models")
    yunet_path, sface_path = _ensure_irl_models(models_dir)

    try:
        detector = cv2.FaceDetectorYN.create(
            yunet_path,
            "",
            (320, 320),
            score_threshold=0.60,
            nms_threshold=0.30,
            top_k=5000,
        )
        recognizer = cv2.FaceRecognizerSF.create(sface_path, "")

        def detect_and_embed_irl(img_bgr):
            h, w = img_bgr.shape[:2]
            detector.setInputSize((w, h))
            _, faces = detector.detect(img_bgr)
            detections = []
            if faces is None or len(faces) == 0:
                return detections

            for face in faces:
                x, y, bw, bh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
                score = float(face[14])
                # Filter small background faces or low score
                if bw < 30 or bh < 30 or (bw * bh) < 900 or score < 0.60:
                    continue

                x0 = max(0, min(w - 1, x))
                y0 = max(0, min(h - 1, y))
                x1 = max(0, min(w, x + bw))
                y1 = max(0, min(h, y + bh))
                if x1 <= x0 or y1 <= y0:
                    continue

                face_crop = img_bgr[y0:y1, x0:x1]
                if face_crop.size == 0:
                    continue

                gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
                blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                if blur_var < 25.0 and score < 0.85:
                    continue

                try:
                    aligned = recognizer.alignCrop(img_bgr, face)
                    feat = recognizer.feature(aligned).flatten().astype("float32")
                    norm = np.linalg.norm(feat)
                    if norm > 0:
                        feat /= norm
                    detections.append({
                        "box": (float(x0), float(y0), float(x1), float(y1)),
                        "score": score,
                        "vector": feat,
                        "blur": blur_var,
                    })
                except Exception:
                    continue

            return detections

        return detect_and_embed_irl, "YuNet+SFace(ArcFace)", 0.363
    except Exception as exc:
        print(f"[faces] IRL model init failed ({exc}); falling back to OpenCV Haar cascade", flush=True)
        haar_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(haar_path) if hasattr(cv2, "data") else None

        def detect_and_embed_fallback(img_bgr):
            detections = []
            if cascade is None or cascade.empty():
                return detections
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(32, 32))
            for (x, y, w, h) in boxes:
                face = img_bgr[y:y+h, x:x+w]
                if face.size == 0:
                    continue
                hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 4], [0, 180, 0, 256, 0, 256])
                cv2.normalize(hist, hist)
                vec = hist.flatten().astype("float32")
                blur_var = float(cv2.Laplacian(gray[y:y+h, x:x+w], cv2.CV_64F).var())
                detections.append({
                    "box": (float(x), float(y), float(x + w), float(y + h)),
                    "score": 0.75,
                    "vector": vec,
                    "blur": blur_var,
                })
            return detections

        return detect_and_embed_fallback, "haar_cascade+hsv(fallback)", 0.30


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
    x0, y0, x1, y1 = [int(v) for v in box[:4]]
    bw, bh = (x1 - x0), (y1 - y0)
    side = max(bw, bh) * (1 + pad_ratio)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    xa = max(0, int(cx - side / 2))
    ya = max(0, int(cy - side / 2))
    xb = min(w, int(cx + side / 2))
    yb = min(h, int(cy + side / 2))
    crop = img_bgr[ya:yb, xa:xb]
    if crop.size == 0:
        crop = img_bgr[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
    if crop.size == 0:
        crop = cv2.resize(img_bgr, (size, size))
    else:
        crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".webp", crop, [cv2.IMWRITE_WEBP_QUALITY, 65])
    if not ok:
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 65])
    return "data:image/webp;base64," + base64.b64encode(buf.tobytes()).decode()


def get_project_content_type(job_id: str, jobs_root: str = "jobs", cli_override: str | None = None) -> str:
    if cli_override and cli_override.lower() in ("anime", "irl"):
        return cli_override.lower()
    for fname in ("ingest-request.json", "status.json"):
        p = os.path.join(jobs_root, job_id, fname)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                    ct = doc.get("content_type")
                    if ct in ("anime", "irl"):
                        return ct
            except Exception:
                pass
    return "anime"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    ap.add_argument("--content-type", default=None, choices=["anime", "irl"], help="anime or irl footage")
    ap.add_argument("--fps", type=float, default=1.0, help="frame sampling rate (tunable)")
    ap.add_argument("--presence-gap", type=float, default=3.0, help="seconds gap to merge presence (tunable)")
    ap.add_argument("--min-appearances", type=int, default=3, help="drop clusters with fewer detections")
    ap.add_argument("--max-frames", type=int, default=3600, help="safety cap on sampled frames")
    args = ap.parse_args()

    import cv2
    import numpy as np

    content_type = get_project_content_type(args.job_id, args.jobs_root, args.content_type)
    print(f"[faces] Starting face discovery for {args.job_id} (content_type={content_type})", flush=True)

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

    is_irl = (content_type == "irl")
    if is_irl:
        detect_and_embed_irl, det_name, cos_threshold = _load_irl_detector_and_embedder()
        emb_name = det_name
        print(f"[faces] mode=IRL detector={det_name} thr={cos_threshold}", flush=True)
    else:
        detect, det_name = _load_anime_detector()
        embed, emb_name, cos_threshold = _load_anime_embedder()
        print(f"[faces] mode=ANIME detector={det_name} embedder={emb_name} thr={cos_threshold}", flush=True)

    clusters = []  # each: {centroid, count, best_score, best_blur, best_frame, best_box, times: [t]}

    for idx, frame_path in enumerate(frames):
        t = idx / fps
        img = cv2.imread(frame_path)
        if img is None:
            continue

        detections = []
        if is_irl:
            try:
                detections = detect_and_embed_irl(img)
            except Exception as exc:
                print(f"[faces] irl detect failed on frame {idx}: {exc}", flush=True)
                continue
        else:
            try:
                boxes = detect(img)
            except Exception as exc:
                print(f"[faces] anime detect failed on frame {idx}: {exc}", flush=True)
                continue

            for (x0, y0, x1, y1, score) in boxes:
                h, w = img.shape[:2]
                ix0 = max(0, min(w - 1, int(x0)))
                iy0 = max(0, min(h - 1, int(y0)))
                ix1 = max(0, min(w, int(x1)))
                iy1 = max(0, min(h, int(y1)))
                if (ix1 - ix0) < 20 or (iy1 - iy0) < 20:
                    continue
                face = img[iy0:iy1, ix0:ix1]
                if face.size == 0:
                    continue
                try:
                    vec = embed(face)
                    detections.append({
                        "box": (ix0, iy0, ix1, iy1),
                        "score": score,
                        "vector": vec,
                        "blur": 50.0,
                    })
                except Exception:
                    continue

        for det in detections:
            vec = det["vector"]
            score = det["score"]
            box = det["box"]
            blur = det.get("blur", 50.0)

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
                # Pick sharpest, highest confidence frame as the thumbnail
                if (score > cl["best_score"]) or (score >= cl["best_score"] * 0.9 and blur > cl.get("best_blur", 0)):
                    cl.update(best_score=score, best_blur=blur, best_frame=frame_path, best_box=box)
            else:
                clusters.append({
                    "centroid": vec,
                    "count": 1,
                    "times": [t],
                    "best_score": score,
                    "best_blur": blur,
                    "best_frame": frame_path,
                    "best_box": box,
                })

        if idx % 60 == 0:
            print(f"[faces] frame {idx}/{len(frames)} — {len(clusters)} clusters", flush=True)

    valid_clusters = [c for c in clusters if c["count"] >= args.min_appearances]
    if not valid_clusters and clusters:
        print(f"[faces] No clusters reached min_appearances={args.min_appearances}; keeping {len(clusters)} clusters with count >= 1", flush=True)
        valid_clusters = clusters
    clusters = valid_clusters

    # Sort characters by appearances / total screen time
    characters = []
    for cl in clusters:
        points = sorted(cl["times"])
        ranges = merge_intervals([(t, t + 1.0 / fps) for t in points], gap=args.presence_gap)
        screen_time = sum(e - s for s, e in ranges)
        cl["screen_time"] = screen_time
        cl["ranges"] = ranges

    clusters.sort(key=lambda c: c["screen_time"], reverse=True)

    for n, cl in enumerate(clusters, start=1):
        img = cv2.imread(cl["best_frame"])
        thumb = _thumb(img, cl["best_box"]) if img is not None else None
        points = sorted(cl["times"])
        characters.append({
            "id": f"char{n}",
            "appearances": cl["count"],
            "screen_time": round(cl["screen_time"], 1),
            "first_seen": round(points[0], 1),
            "ranges": [[round(s, 2), round(e, 2)] for s, e in cl["ranges"]],
            "thumb": thumb,
        })

    manifest = {
        "version": 1,
        "job_id": args.job_id,
        "content_type": content_type,
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


# Backward compatibility aliases
_load_detector = _load_anime_detector
_load_embedder = _load_anime_embedder

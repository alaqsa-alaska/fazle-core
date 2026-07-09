import json
import os
import pathlib
import random
import re

import requests

BASE = "http://127.0.0.1:8090"
SOURCES = [
    "/home/azim/whatsapp1/store",
    "/home/azim/whatsapp2/store",
]
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
AUD_EXT = {".ogg"}
MAX_CANDIDATES = int(os.environ.get("FG2_DISCOVERY_MAX_CANDIDATES", "120"))
OCR_TIMEOUT = int(os.environ.get("FG2_DISCOVERY_OCR_TIMEOUT", "90"))
ASR_TIMEOUT = int(os.environ.get("FG2_DISCOVERY_ASR_TIMEOUT", "240"))
TARGET_SAMPLES = int(os.environ.get("FG2_DISCOVERY_TARGET_SAMPLES", "8"))
OUT = "/home/azim/core/reports/phase3/phase3_fg2_bangla_samples.json"

bn_re = re.compile(r"[\u0980-\u09FF]")


def collect_files():
    imgs = []
    auds = []
    for top in SOURCES:
        if not os.path.isdir(top):
            continue
        for dp, _, fns in os.walk(top):
            for fn in fns:
                ext = pathlib.Path(fn).suffix.lower()
                p = os.path.join(dp, fn)
                if ext in IMG_EXT:
                    imgs.append(p)
                elif ext in AUD_EXT:
                    auds.append(p)
    random.seed(17)
    if len(imgs) > MAX_CANDIDATES:
        imgs = random.sample(imgs, MAX_CANDIDATES)
    if len(auds) > MAX_CANDIDATES:
        auds = random.sample(auds, MAX_CANDIDATES)
    return imgs, auds


def probe_ocr(path):
    r = requests.post(f"{BASE}/ocr", json={"file_path": path}, timeout=OCR_TIMEOUT)
    if r.status_code != 200:
        return None
    data = r.json() if "application/json" in r.headers.get("content-type", "") else {}
    txt = (data.get("text") or data.get("raw_text") or "").strip()
    if not txt:
        return None
    words = len(txt.split())
    has_bn = bool(bn_re.search(txt))
    return {"path": path, "words": words, "has_bn": has_bn}


def probe_asr(path):
    r = requests.post(f"{BASE}/transcribe", json={"file_path": path}, timeout=ASR_TIMEOUT)
    if r.status_code != 200:
        return None
    data = r.json() if "application/json" in r.headers.get("content-type", "") else {}
    txt = (data.get("text") or "").strip()
    if not txt:
        return None
    words = len(txt.split())
    has_bn = bool(bn_re.search(txt))
    return {"path": path, "words": words, "has_bn": has_bn}


def main():
    imgs, auds = collect_files()

    image_hits = []
    for p in imgs:
        try:
            rec = probe_ocr(p)
            if rec and rec["has_bn"] and rec["words"] >= 2:
                image_hits.append(rec)
                if len(image_hits) >= TARGET_SAMPLES:
                    break
        except Exception:
            continue

    audio_hits = []
    for p in auds:
        try:
            rec = probe_asr(p)
            if rec and rec["has_bn"] and rec["words"] >= 2:
                audio_hits.append(rec)
                if len(audio_hits) >= TARGET_SAMPLES:
                    break
        except Exception:
            continue

    image_hits.sort(key=lambda x: x["words"], reverse=True)
    audio_hits.sort(key=lambda x: x["words"], reverse=True)

    payload = {
        "source_roots": SOURCES,
        "max_candidates": MAX_CANDIDATES,
        "discovery_timeouts": {"ocr": OCR_TIMEOUT, "asr": ASR_TIMEOUT},
        "image_bn_candidates_found": len(image_hits),
        "audio_bn_candidates_found": len(audio_hits),
        "target_samples": TARGET_SAMPLES,
        "image_samples": [x["path"] for x in image_hits[:TARGET_SAMPLES]],
        "audio_samples": [x["path"] for x in audio_hits[:TARGET_SAMPLES]],
        "image_probe_top": image_hits[:12],
        "audio_probe_top": audio_hits[:12],
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(json.dumps(payload, indent=2))
    print(f"WROTE={OUT}")


if __name__ == "__main__":
    main()

import os
import json
import random
import pathlib
import hashlib
import re
from statistics import mean

import requests

BASE = "http://127.0.0.1:8090"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
AUD_EXT = {".ogg", ".wav", ".mp3", ".m4a"}
OCR_TIMEOUT = int(os.environ.get("PHASE3_OCR_TIMEOUT_SECONDS", "120"))
ASR_TIMEOUT = int(os.environ.get("PHASE3_ASR_TIMEOUT_SECONDS", "180"))
SAMPLE_MANIFEST = "/home/azim/core/reports/phase3/phase3_fg2_bangla_samples.json"
SAMPLE_SIZE = int(os.environ.get("PHASE3_SAMPLE_SIZE", "8"))

SOURCES = [
    "/home/azim/whatsapp1/store",
    "/home/azim/whatsapp2/store",
    "/home/azim/bridges/bridge2/store",
]

bn_re = re.compile(r"[\u0980-\u09FF]")
en_re = re.compile(r"[A-Za-z]")


def collect_files():
    img_files = []
    aud_files = []
    for top in SOURCES:
        if not os.path.isdir(top):
            continue
        for dp, _, fns in os.walk(top):
            for fn in fns:
                ext = pathlib.Path(fn).suffix.lower()
                p = os.path.join(dp, fn)
                if ext in IMG_EXT:
                    img_files.append(p)
                elif ext in AUD_EXT:
                    aud_files.append(p)
    return img_files, aud_files


def sample_paths(items, n=8):
    if not items:
        return []
    random.seed(7)
    return random.sample(items, min(n, len(items)))


def load_manifest_samples():
    if not os.path.exists(SAMPLE_MANIFEST):
        return [], []
    try:
        with open(SAMPLE_MANIFEST, "r", encoding="utf-8") as f:
            data = json.load(f)
        imgs = [p for p in data.get("image_samples", []) if os.path.exists(p)]
        auds = [p for p in data.get("audio_samples", []) if os.path.exists(p)]
        return imgs, auds
    except Exception:
        return [], []


def ocr_metric(path):
    r = requests.post(f"{BASE}/ocr", json={"file_path": path}, timeout=OCR_TIMEOUT)
    data = r.json() if "application/json" in r.headers.get("content-type", "") else {}
    txt = (data.get("text") or data.get("raw_text") or "").strip()
    return {
        "id": hashlib.sha1(path.encode()).hexdigest()[:10],
        "path": path,
        "status": r.status_code,
        "words": len(txt.split()),
        "has_bn": bool(bn_re.search(txt)),
        "has_en": bool(en_re.search(txt)),
        "ok": r.status_code == 200 and len(txt) >= 8,
    }


def asr_metric(path):
    r = requests.post(f"{BASE}/transcribe", json={"file_path": path}, timeout=ASR_TIMEOUT)
    data = r.json() if "application/json" in r.headers.get("content-type", "") else {}
    txt = (data.get("text") or "").strip()
    return {
        "id": hashlib.sha1(path.encode()).hexdigest()[:10],
        "path": path,
        "status": r.status_code,
        "words": len(txt.split()),
        "has_bn": bool(bn_re.search(txt)),
        "has_en": bool(en_re.search(txt)),
        "ok": r.status_code == 200 and len(txt.split()) >= 2,
    }


def summary(items):
    total = len(items)
    ok = sum(1 for x in items if x.get("ok"))
    return {
        "total": total,
        "ok": ok,
        "ok_rate": round(100 * ok / total, 1) if total else 0,
        "bn_hits": sum(1 for x in items if x.get("has_bn")),
        "en_hits": sum(1 for x in items if x.get("has_en")),
        "avg_words": round(mean([x.get("words", 0) for x in items]), 2) if items else 0,
    }


def main():
    img_files, aud_files = collect_files()
    manifest_imgs, manifest_auds = load_manifest_samples()
    img_sample = (
        manifest_imgs[:SAMPLE_SIZE]
        + sample_paths([p for p in img_files if p not in manifest_imgs], SAMPLE_SIZE)
    )[:SAMPLE_SIZE]
    aud_sample = (
        manifest_auds[:SAMPLE_SIZE]
        + sample_paths([p for p in aud_files if p not in manifest_auds], SAMPLE_SIZE)
    )[:SAMPLE_SIZE]

    ocr = []
    for p in img_sample:
        try:
            ocr.append(ocr_metric(p))
        except Exception as e:
            ocr.append(
                {
                    "id": hashlib.sha1(p.encode()).hexdigest()[:10],
                    "status": "ERR",
                    "words": 0,
                    "has_bn": False,
                    "has_en": False,
                    "ok": False,
                    "error": str(e),
                }
            )

    asr = []
    for p in aud_sample:
        try:
            asr.append(asr_metric(p))
        except Exception as e:
            asr.append(
                {
                    "id": hashlib.sha1(p.encode()).hexdigest()[:10],
                    "status": "ERR",
                    "words": 0,
                    "has_bn": False,
                    "has_en": False,
                    "ok": False,
                    "error": str(e),
                }
            )

    report = {
        "sample_manifest": SAMPLE_MANIFEST if os.path.exists(SAMPLE_MANIFEST) else None,
        "sample_size": SAMPLE_SIZE,
        "timeouts": {"ocr": OCR_TIMEOUT, "asr": ASR_TIMEOUT},
        "ocr_summary": summary(ocr),
        "asr_summary": summary(asr),
        "ocr_sample": ocr,
        "asr_sample": asr,
    }

    out = "/home/azim/core/reports/phase3/phase3_step3_live_quality_metrics.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()

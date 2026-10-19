"""Versioned official Item assets and reproducible normalization."""
from __future__ import annotations
import json
import shutil
import math
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import httpx

from .config import settings, REPO_ROOT
from .media import ffmpeg, probe, decode_errors
from .store import sha256_file

POLICY = "item-v1-black-matte-cfr30"
SOURCE = "https://cdn.item.com/design/video/brand/"


def source_frame_count(stream: dict, fps: int = 30) -> int:
    """Natural source duration, rounded up by at most one target frame; never a black-end guess."""
    duration = (Fraction(int(stream["duration_ts"]))*Fraction(stream["time_base"])
                if stream.get("duration_ts") is not None and stream.get("time_base")
                else Fraction(str(stream.get("duration",0))))
    if duration<=0:
        raise ValueError("Bookend source has no usable video duration")
    return math.ceil(duration*fps)


@lru_cache(maxsize=8)
def _bookend_counts(root_name: str, manifest_hash: str) -> dict:
    root=Path(root_name);manifest=json.loads((root/"manifest.json").read_text())
    result={}
    for kind in ("intro","outro"):
        source=root/f"{kind}-item-logo.mp4"
        if sha256_file(source)!=manifest["assets"][source.name]["sha256"]:
            raise ValueError("Official bookend source hash mismatch")
        video=next(s for s in probe(source)["streams"] if s["codec_type"]=="video")
        count=source_frame_count(video)
        if manifest["assets"][f"{kind}-normalized.mp4"]["frames"]!=count:
            raise ValueError("Normalized bookend endpoint differs from natural source; refresh owned cache")
        result[kind]=count
    return result


def bookend_counts():
    return _bookend_counts(str(settings.brand_dir),sha256_file(settings.brand_dir/"manifest.json")).copy()


def refresh_guidelines() -> str:
    """Bounded fixed-host text refresh; retain approved video/font assets unchanged."""
    root=settings.brand_dir;url="https://design.item.com/llms-full.txt"
    chunks=[];size=0
    with httpx.Client(timeout=30,follow_redirects=False) as client:
        with client.stream("GET",url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size+=len(chunk)
                if size>2_000_000:raise ValueError("Brand guideline text exceeds cache limit")
                chunks.append(chunk)
    data=b"".join(chunks);text=data.decode("utf-8")
    if "<html" in text[:500].lower() or len(text)<100:
        raise ValueError("Expected official brand guideline text")
    temporary=root/"llms-full.writing.txt";temporary.write_bytes(data)
    temporary.replace(root/"llms-full.txt")
    manifest=json.loads((root/"manifest.json").read_text())
    manifest["assets"]["llms-full.txt"]={"url":url,"sha256":sha256_file(root/"llms-full.txt")}
    target=root/"manifest.writing.json";target.write_text(json.dumps(manifest,indent=2));target.replace(root/"manifest.json")
    return manifest["assets"]["llms-full.txt"]["sha256"]


def refresh(local_reference: Path | None = None) -> dict:
    root = settings.brand_dir
    root.mkdir(parents=True, exist_ok=True)
    sources = {}
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        response = client.get("https://design.item.com/llms-full.txt")
        response.raise_for_status()
        (root / "llms-full.txt").write_text(response.text)
        sources["llms-full.txt"] = {"url": "https://design.item.com/llms-full.txt"}
        for name in ("intro-item-logo.mp4", "outro-item-logo.mp4"):
            response = client.get(SOURCE + name)
            response.raise_for_status()
            if len(response.content) > 10_000_000:
                raise ValueError("official bookend unexpectedly exceeds asset size cap")
            (root / name).write_bytes(response.content)
            sources[name] = {"url": SOURCE + name}
    if not local_reference:
        raise ValueError("provide an approved local reference for Item logo and Satoshi font assets")
    for name in ("item-logo.svg",):
        shutil.copy2(local_reference / "brand" / name, root / name)
        sources[name] = {"source": "approved local Item brand reference"}
    for weight in (400, 500, 700):
        name = f"Satoshi-{weight}.woff2"
        shutil.copy2(local_reference / "fonts" / name, root / name)
        sources[name] = {"source": "approved local Item font reference"}
    for kind in ("intro","outro"):
        src, out = root / f"{kind}-item-logo.mp4", root / f"{kind}-normalized.mp4"
        stream = next(s for s in probe(src)["streams"] if s["codec_type"] == "video")
        count=source_frame_count(stream)
        # Resolve source display aspect before setsar. Pad the whole image; never crop or stretch.
        vf = ("scale=w='trunc(ih*dar/2)*2':h=ih,setsar=1,"
              "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
              "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,setpts=N/(30*TB),"
              "tpad=stop_mode=clone:stop_duration=0.034")
        ffmpeg("-i", str(src), "-an", "-vf", vf, "-r", "30", "-fps_mode", "cfr", "-frames:v", str(count),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-bf","0","-refs","1","-g","30","-keyint_min","30","-sc_threshold","0",
            "-video_track_timescale","30000","-movflags", "+faststart", str(out))
        check = next(s for s in probe(out)["streams"] if s["codec_type"] == "video")
        if int(check["nb_frames"]) != count or check["sample_aspect_ratio"] != "1:1" or decode_errors(out):
            raise ValueError(f"{kind} normalization failed frame/SAR/decode gate")
        sources[out.name] = {"policy": POLICY, "frames": count, "source_sar": stream.get("sample_aspect_ratio"),
                             "source_dar": stream.get("display_aspect_ratio"),
                             "endpoint":"complete natural source; no assumed black ending",
                             "source_duration_ts":stream.get("duration_ts"),"source_time_base":stream.get("time_base")}
    for name, record in sources.items():
        record["sha256"] = sha256_file(root / name)
    manifest = {"version": POLICY, "assets": sources}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    public = REPO_ROOT / "web" / "public"
    (public / "brand").mkdir(parents=True, exist_ok=True)
    (public / "fonts").mkdir(exist_ok=True)
    shutil.copy2(root / "item-logo.svg", public / "brand" / "item-logo.svg")
    for f in root.glob("*.woff2"):
        if sha256_file(f) != manifest["assets"][f.name]["sha256"]:
            raise ValueError(f"font asset hash mismatch: {f.name}; refresh the versioned cache")
        shutil.copy2(f, public / "fonts" / f.name)
    return manifest


def prepare(public: Path) -> dict:
    root = settings.brand_dir
    manifest = json.loads((root / "manifest.json").read_text())
    (public / "brand").mkdir(parents=True, exist_ok=True)
    (public / "fonts").mkdir(exist_ok=True)
    for name in ("intro-normalized.mp4", "outro-normalized.mp4", "item-logo.svg"):
        if sha256_file(root / name) != manifest["assets"][name]["sha256"]:
            raise ValueError(f"brand asset hash mismatch: {name}; refresh the versioned cache")
        shutil.copy2(root / name, public / "brand" / name)
    for f in root.glob("*.woff2"):
        if sha256_file(f) != manifest["assets"][f.name]["sha256"]:
            raise ValueError(f"font asset hash mismatch: {f.name}; refresh the versioned cache")
        shutil.copy2(f, public / "fonts" / f.name)
    return manifest

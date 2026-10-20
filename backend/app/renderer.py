"""Renderer adapters. The canonical timeline is renderer-neutral; these turn it into pixels.

* remotion — Remocn components on the Remotion runtime (render/). Remotion is source-available,
  not OSI open source: companies above the free tier need a Remotion company license.
* ffmpeg   — dependency-free fallback (filter_complex), used when Node/Remotion is unavailable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from .config import settings
from .media import MediaError, describe, ffmpeg


def frames(ms: int, fps: int) -> int:
    return int(round(ms * fps / 1000))


def remotion_available() -> bool:
    return (settings.render_dir / "node_modules" / "@remotion" / "cli").exists()


def _remotion_bin() -> list[str]:
    cli = settings.render_dir / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    return ["node", str(cli)]


# Remotion's non-TTY progress lines, e.g. "Rendering frames  ━━━━  1234/5286  2m 10s remaining"
_PROGRESS = re.compile(r"(Rendering frames|Encoding video|Encoded|Rendered)\D*?(\d+)/(\d+)(?:.*?([\dhms ]+) remaining)?")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_progress(line: str) -> tuple[str, int, int, str] | None:
    """(phase, done, total, eta) from one Remotion progress line, or None."""
    m = _PROGRESS.search(_ANSI.sub("", line))
    if not m:
        return None
    phase = "encoding" if m.group(1).startswith("Encod") else "rendering"
    return phase, int(m.group(2)), int(m.group(3)), (m.group(4) or "").strip()


def render_remotion(composition: str, props: dict, out: Path, *, public_dir: Path, scale: float = 1.0,
                    log_path: Path | None = None,
                    on_progress: Callable[[str, int, int, str], None] | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    props_path = out.with_suffix(".props.json")
    props_path.write_text(json.dumps(props))
    args = [
        *_remotion_bin(), "render", "src/index.ts", composition, str(out),
        f"--props={props_path}", f"--public-dir={public_dir}", f"--concurrency={settings.render_concurrency}",
        f"--scale={scale}", "--pixel-format=yuv420p", "--color-space=bt709", "--overwrite",
        # info level prints progress lines (stdout is a pipe, so one line per update, not an overwriting bar)
        "--log=info", "--x264-preset=veryfast", "--offthreadvideo-video-threads=4",
    ]
    env = {**os.environ, "PATH": f"{Path(settings.ffmpeg).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    lines: list[str] = []
    last_report = 0.0
    with subprocess.Popen(args, cwd=settings.render_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=env, text=True, encoding="utf-8", errors="replace") as proc:
        for line in proc.stdout:
            lines.append(line.rstrip())
            parsed = parse_progress(line) if on_progress else None
            if parsed and (time.monotonic() - last_report > 2 or parsed[1] == parsed[2]):
                last_report = time.monotonic()
                on_progress(*parsed)
        proc.wait()
    if log_path:
        log_path.write_text("\n".join(lines), encoding="utf-8")
    if proc.returncode != 0 or not out.exists():
        raise MediaError(f"remotion render {composition} failed: {chr(10).join(lines)[-1500:]}")
    _normalise_output(out)


def _normalise_output(out: Path) -> None:
    """Guarantee limited-range yuv420p + fast-start, which every player and LMS accepts."""
    info = describe(out)
    if info["pix_fmt"] == "yuv420p":
        return
    tmp = out.with_suffix(".norm.mp4")
    ffmpeg("-i", str(out), "-map", "0", "-vf", "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p",
           "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-color_range", "tv", "-colorspace", "bt709",
           "-c:a", "copy", "-movflags", "+faststart", str(tmp))
    tmp.replace(out)


def _filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def font_file() -> str:
    """drawtext font option. Windows FFmpeg builds often ship without a fontconfig config."""
    candidates = [os.environ.get("FONT_FILE", ""), "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Helvetica.ttc"]
    found = next((c for c in candidates if c and Path(c).exists()), "")
    return f"fontfile='{_filter_path(found)}':" if found else ""


def fonts_dir() -> str:
    found = next((d for d in ("C:/Windows/Fonts", "/usr/share/fonts", "/System/Library/Fonts") if Path(d).exists()), "")
    return f":fontsdir='{_filter_path(found)}'" if found else ""


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def render_preview_ffmpeg(physical: Path, system: Path, out: Path, *, phys_start_ms: int, sys_start_ms: int,
                          duration_ms: int, fps: int) -> None:
    """Side-by-side preview with burned shared timecode."""
    half_w, h, bar = 954, 1008, 72
    font = font_file()
    vf = (
        f"[0:v]scale={half_w}:{h}:force_original_aspect_ratio=decrease,pad={half_w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"drawtext={font}text='PHYSICAL':x=10:y=10:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.7[p];"
        f"[1:v]scale={half_w}:{h}:force_original_aspect_ratio=decrease,pad={half_w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"drawtext={font}text='SYSTEM':x=10:y=10:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.7[s];"
        f"[p][s]hstack=inputs=2,pad=1920:1080:6:{bar}:color=0x111111,"
        f"drawtext={font}text='T %{{pts\\:hms}}':x=(w-tw)/2:y=16:fontsize=40:fontcolor=white,fps={fps}[v]"
    )
    ffmpeg(
        "-ss", f"{phys_start_ms/1000:.3f}", "-i", str(physical), "-ss", f"{sys_start_ms/1000:.3f}", "-i", str(system),
        "-t", f"{duration_ms/1000:.3f}", "-filter_complex", vf, "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out),
    )


def render_final_ffmpeg(props: dict, public_dir: Path, out: Path, *, captions_srt: Path | None) -> None:
    """Fallback final: fixed split layout, narration mixed at cue offsets, subtitles burned in."""
    fps = props["fps"]
    inputs: list[str] = []
    chains: list[str] = []
    concat: list[str] = []
    phys = public_dir / props["physical"]["src"]
    sys_ = public_dir / props["system"]["src"]
    w = props["width"] // 2
    h = props["height"]
    n_video_inputs = 0
    for i, seg in enumerate(props["segments"]):
        dur = seg["durationInFrames"] / fps
        if seg["kind"] == "intro":
            title = _escape_drawtext(props["title"])
            chains.append(f"color=c=0x0a1020:s={props['width']}x{h}:r={fps}:d={dur:.3f},"
                          f"drawtext={font_file()}text='{title}':x=(w-tw)/2:y=(h-th)/2:fontsize=72:fontcolor=white[seg{i}]")
        else:
            rate = seg.get("playbackRate", 1) or 1
            inputs += ["-ss", f"{seg['physicalStartFrame']/fps:.3f}", "-t", f"{dur * rate:.3f}", "-i", str(phys)]
            inputs += ["-ss", f"{seg['systemStartFrame']/fps:.3f}", "-t", f"{dur * rate:.3f}", "-i", str(sys_)]
            ip, is_ = n_video_inputs, n_video_inputs + 1
            n_video_inputs += 2
            # a hold shows the first frame still for the whole segment; a fast-forward plays `rate` times faster
            still = f"trim=end_frame=1,tpad=stop_mode=clone:stop_duration={max(0, dur - 1 / fps):.3f}," if seg["kind"] == "hold" else ""
            still += f"setpts=PTS/{rate:.6f}," if rate != 1 else ""
            chains.append(
                f"[{ip}:v]{still}scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[pa{i}];"
                f"[{is_}:v]{still}scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[sa{i}];"
                f"[pa{i}][sa{i}]hstack=inputs=2,fps={fps}[seg{i}]"
            )
        concat.append(f"[seg{i}]")
    audio_labels = []
    for j, cue in enumerate(props["cues"]):
        inputs += ["-i", str(public_dir / cue["src"])]
        delay = int(round(cue["fromFrame"] * 1000 / fps))
        chains.append(f"[{n_video_inputs + j}:a]adelay={delay}|{delay},aresample=48000[a{j}]")
        audio_labels.append(f"[a{j}]")
    vchain = "".join(concat) + f"concat=n={len(concat)}:v=1:a=0[vc]"
    if captions_srt and captions_srt.exists() and captions_srt.stat().st_size > 0:
        vchain += (f";[vc]subtitles='{_filter_path(str(captions_srt))}'{fonts_dir()}"
                   ":force_style='FontName=Arial,FontSize=22,BorderStyle=3,Outline=1,MarginV=40'[v]")
    else:
        vchain += ";[vc]null[v]"
    chains.append(vchain)
    total = props["durationInFrames"] / fps
    if audio_labels:
        chains.append("".join(audio_labels) + f"amix=inputs={len(audio_labels)}:normalize=0,apad,atrim=0:{total:.3f}[a]")
    else:
        chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.3f}[a]")
    ffmpeg(*inputs, "-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out))

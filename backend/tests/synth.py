"""Synthetic test recordings with a known offset.

physical = system + LAG_MS: the system (WISE) recording starts 1.5 s after the floor camera.
Both microphones hear the same scanner beeps (plus independent noise), the WISE screen changes
title at known times and the floor camera shows a moving box during physical handling.
Shared time == system time for this pair.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np

from app.media import ffmpeg

LAG_MS = 1500
SYSTEM_MS = 22_000
PHYSICAL_MS = 24_000
SR = 48_000
BEEPS_SHARED_MS = [2100, 5300, 9150, 10350, 13900, 16400, 19800]
FONT = "C\\:/Windows/Fonts/arial.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

SCREENS = [(0, 2, "WISE Home"), (2, 9, "Receiving"), (9, 10.2, "Confirm receipt?"), (10.2, 22, "Received")]
MOVES = [(5.0, 6.0), (13.0, 17.0), (19.5, 20.5)]  # shared seconds with physical motion

SOP_MD = """# Receive Pallet into Location

## Before you begin
- Handheld scanner is charged

## Procedure
1. Open the **Receiving** menu in WISE.
2. Scan the pallet label with the handheld scanner.
3. Tap **Confirm** and wait until the "Received" message appears.
4. Move the pallet to the staging lane.
5. Tap **Put Away** to finish.

> Warning: never stack pallets above the red line.
"""

EVENTS = [
    {"start_ms": 2000, "end_ms": 3200, "view": "system", "action": "The worker taps Receiving", "visible_text": ["Receiving"], "confidence": 0.9},
    {"start_ms": 5000, "end_ms": 6000, "view": "both", "action": "The worker scans the pallet label with the handheld scanner", "confidence": 0.85},
    {"start_ms": 9000, "end_ms": 10000, "view": "system", "action": "The worker taps Confirm", "visible_text": ["Confirm"], "confidence": 0.9},
    {"start_ms": 10200, "end_ms": 12000, "view": "system", "kind": "result", "action": "The Received message appears", "visible_text": ["Received"], "confidence": 0.9},
    {"start_ms": 13000, "end_ms": 17000, "view": "physical", "action": "The worker moves the pallet to the staging lane", "confidence": 0.8},
    {"start_ms": 19500, "end_ms": 20500, "view": "physical", "action": "The worker wraps the pallet with film", "confidence": 0.8},
]


def _audio(path: Path, duration_ms: int, beep_ms: list[int], seed: int, gain: float) -> None:
    rng = np.random.default_rng(seed)
    n = int(SR * duration_ms / 1000)
    sig = rng.normal(0, 0.01, n)
    for i, t in enumerate(beep_ms):
        start = int(SR * t / 1000)
        if 0 <= start < n:
            length = min(int(SR * 0.12), n - start)
            tt = np.arange(length) / SR
            sig[start:start + length] += 0.5 * np.sin(2 * np.pi * (1800 + 150 * i) * tt) * np.hanning(length)
    pcm = (np.clip(sig * gain, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def make_pair(folder: Path) -> tuple[Path, Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    phys_wav, sys_wav = folder / "phys.wav", folder / "sys.wav"
    _audio(phys_wav, PHYSICAL_MS, [b + LAG_MS for b in BEEPS_SHARED_MS], seed=1, gain=0.8)
    _audio(sys_wav, SYSTEM_MS, BEEPS_SHARED_MS, seed=2, gain=1.0)

    text = ",".join(
        f"drawtext=fontfile='{FONT}':text='{label}':x=(w-tw)/2:y=h/3:fontsize=64:fontcolor=black:"
        f"enable='between(t,{a},{b})'" for a, b, label in SCREENS)
    system = folder / "system.mp4"
    ffmpeg("-f", "lavfi", "-i", f"color=c=white:s=1280x720:r=30:d={SYSTEM_MS/1000}", "-i", str(sys_wav),
           "-vf", f"{text},drawtext=fontfile='{FONT}':text='Tap here':x=40:y=h-80:fontsize=36:fontcolor=gray",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(system))

    lag = LAG_MS / 1000
    x = "+".join(f"if(between(t,{a+lag},{b+lag}),(t-{a+lag})*300,0)" for a, b in MOVES)
    physical = folder / "physical.mp4"
    ffmpeg("-f", "lavfi", "-i", f"color=c=0x404850:s=1280x720:r=30:d={PHYSICAL_MS/1000}",
           "-f", "lavfi", "-i", "color=c=orange:s=160x160:r=30", "-i", str(phys_wav),
           "-filter_complex", f"[0:v][1:v]overlay=x='100+{x}':y=280:eval=frame:shortest=1[v]",
           "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
           str(physical))

    sop = folder / "sop.md"
    sop.write_text(SOP_MD, encoding="utf-8")
    return physical, system, sop

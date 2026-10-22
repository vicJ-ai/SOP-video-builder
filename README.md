# UNIS SOP Video Builder

Turns **two raw recordings** (the physical warehouse work and the WMS system screen) plus the
**SOP Markdown file** into a verified, narrated, captioned training video, together with a report of
which SOP steps the footage actually demonstrates.

The core design rule: the rendered MP4 is not the source of truth. The **canonical event
timeline** in `project.json` is. Every stage reads from it and writes to it, so a correction only
reruns the stages it affects.

```
upload ─▶ validate ─▶ parse SOP ─▶ CFR proxies ─▶ sync + verify (start/mid/end) ─▶ canonical timeline (OTIO)
  ─▶ analysis preview (Remocn/Remotion) ─▶ transcript (faster-whisper, context only) ─▶ candidate windows
  ─▶ Qwen3-VL coarse → fine ─▶ OCR verification (PaddleOCR) ─▶ SOP comparison ─▶ narration
  ─▶ ⏸ narration review ─▶ TTS + measured scheduling ─▶ captions + layout ─▶ draft ─▶ ⏸ draft review
  ─▶ final render (Remocn/Remotion) ─▶ QA ─▶ deliverables + provenance manifest
```

## Quick start (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1          # FFmpeg, uv, Python 3.12 venv, npm deps, UI build
scripts\start.ps1                                                    # http://localhost:8080
```

Optional extras: `-Asr` (faster-whisper), `-Ocr` (PaddleOCR), `-Kokoro` (local CPU voice),
`-SkellySync` (installs SkellySync in its own isolated venv).

**Try it without footage or a GPU:**

```powershell
cd backend
.venv\Scripts\python scripts\make_sample.py      # writes samples/ (synthetic pair, known 1.5 s offset)
$env:MOCK_EVENTS_PATH="..\samples\mock_events.json"; $env:TTS_PROVIDER="system"
..\scripts\start.ps1                            # then upload samples/physical.mp4, system.mp4, sop.md
```

## Pointing it at real models

This machine has no NVIDIA GPU, so the models sit behind endpoints. Configure them in `backend/.env`
(see `.env.example`):

| What | Default | Production |
|---|---|---|
| Video understanding | `VLM_PROVIDER=mock` | `vllm serve Qwen/Qwen3-VL-8B-Instruct` on a GPU box → `VLM_PROVIDER=openai`, `VLM_BASE_URL=http://gpu:8000/v1` |
| Voice | `TTS_PROVIDER=mock` (silence) / `system` (Windows voice) | Qwen3-TTS served at `/v1/audio/speech` → `TTS_PROVIDER=openai`; or `kokoro` on CPU |
| Transcript | skipped when not installed | `uv sync --extra asr` (faster-whisper) |
| OCR | skipped → text stays *unverified* and is flagged | `uv sync --extra ocr` (PaddleOCR) |
| Text LLM | `none` (deterministic matching and wording) | `LLM_PROVIDER=openai` reuses the VLM endpoint |

Any OpenAI-compatible server works (vLLM, SGLang, DashScope's compatible mode). Structured output
is requested with a JSON schema. When the server doesn't support schemas, the app falls back to
prompted JSON with validation and a retry.

## How each requirement is met

| Requirement | Where |
|---|---|
| Files readable, audio present, originals kept | `stages/validate.py` runs a strict full decode before any AI step. `originals/` is never modified. |
| SkellySync sync, verified at start/middle/end, manual adjustment | `stages/sync.py` uses SkellySync (out-of-process) plus built-in GCC-PHAT, onset-envelope and brightness estimators. The lag that best survives three independent windows wins. Drift and estimator disagreement are flagged. The **Sync** tab has paired waveforms, landmark players and a nudgeable offset. |
| Footage outside the shared interval flagged, not discarded | `SyncResult.outside` (motion / screen-change activity), shown in the Sync tab |
| Remocn/Remotion analysis preview | `render/src/Preview.tsx`: both views, stable labels, burned-in shared timecode, no overlays |
| Timestamped transcript as context, never proof | `stages/transcribe.py`. In `stages/sop.py`, a spoken-only step becomes `unclear` + `speech-only`, never `demonstrated`. |
| Qwen3-VL action timeline, closer look at quick actions | `stages/analyze.py`: a coarse pass over the preview, then a fine pass (higher fps, **original** WMS frames) on short windows from `stages/windows.py`. Output is Pydantic-validated and clamped. Human edits always win. |
| Compare with SOP: demonstrated / partial / missing / unclear, flag differences, never invent | `sopparse.py` + `stages/sop.py`: order-preserving DP alignment with IDF weighting, control-name and completion-evidence checks, and the flags `not-in-sop`, `out-of-order`, `control-mismatch`, `speech-only` and `missing-completion-evidence` |
| Narration per event, exact control names, reviewable | `stages/narrate.py` + `narration.py`. Unverified names or low confidence mark a line `needs_review`. The `narration_review` gate pauses before TTS. |
| TTS per line, measured, scheduled at/before the action, results only after evidence, shorten instead of slowing down | `stages/tts.py` + `scheduler.py` (pure, unit-tested). Hard rules: no overlap, never crosses a cut, a result never plays before it's visible, no speed changes or freezes. |
| One shared editing timeline for cuts and intro | `models.Timeline` + `stages/timeline.py`. Everything is stored in shared time and projected to output time. OTIO export. |
| Voice, burned captions, Remocn design, muted source audio | `render/src/Final.tsx`: Remocn `caption-subtitle`, `progress`, `stepper`, `soft-blur-in`, `fade-through`, `toast`, `backdrop`. The layout is driven by event type (WMS enlarged for system actions, floor for handling, split for cross-view). |
| Draft preview, corrections without restarting | `draft` stage + `draft_review` gate. Input-hash caching means editing a line reruns only TTS → captions → render → QA. |
| Final QA | `stages/qa.py` checks strict decode, resolution, fps, pix_fmt, duration and frame count, fast-start, per-segment sync offset, no speed change, no overlap, no cue across cuts, captions equal to the schedule, WMS readability, completion claims backed by evidence, frozen footage and traceability. It also exports representative frames for a human check. |
| Deliverables | `out/`: `final.mp4`, `final_720p.mp4`, `narration_transcript.txt`, `captions.srt/.vtt`, `narration_timing.csv`, `source_time_map.csv/.json`, `timeline.otio`, `sop_coverage_report.md/.json`, `qa_report.md/.json`, `poster.jpg`, `manifest.json` (provenance), `project.json` (editable), plus a single zip |

## Layout

```
backend/   FastAPI + pipeline (Python 3.12, uv)      app/stages/*  one module per stage
render/    Remotion compositions + vendored Remocn components (src/components/remocn, src/lib)
web/       Vite + React review UI (served by the backend from web/dist)
samples/   generated demo inputs (scripts/make_sample.py)
projects/  per-project data: originals/ work/ out/ project.json
```

## Tests

```powershell
cd backend
.venv\Scripts\python -m pytest              # unit + media tests on synthetic footage (~2 min, FFmpeg renderer)
$env:TEST_RENDERER="remotion"; .venv\Scripts\python -m pytest -k end_to_end   # same, through Remotion
```

The media tests generate a recording pair with a known 1.5 s offset and assert the following:

- sync recovers the offset within ±40 ms and all three windows verify
- a manual offset wins
- the full mock pipeline produces every deliverable and passes QA
- step 5 (never shown) is reported missing, and an unscripted action is flagged `not-in-sop` and not narrated
- editing one narration line reruns only TTS → package
- adding an intro shifts every cue

## Licensing — read before production use

- **Remotion** is source-available, not OSI open source. Organizations above Remotion's free tier
  need a **Remotion company license** for this kind of automated rendering. Remocn components are
  MIT and vendored here. The timeline is renderer-neutral: `RENDERER=ffmpeg` renders without
  Remotion, and a Revideo renderer could replace it without rerunning any analysis.
- **SkellySync** is **AGPL-3.0**. It is never imported. It's installed in its own venv and called as
  a separate process, and the pipeline works without it. Get a license review before you ship it
  inside a hosted product.
- **FFmpeg** builds can be LGPL or GPL. The winget `Gyan.FFmpeg` "full" build is GPL. Document whichever
  build you deploy.
- Qwen3-VL, Qwen3-TTS, faster-whisper, PaddleOCR, Kokoro and OpenTimelineIO are Apache-2.0 or MIT.
- Use a fixed, approved preset voice. Don't clone an employee's voice without explicit consent.

## Deferred (next steps)

- tus resumable uploads (currently streamed multipart) and Temporal for multi-worker orchestration
- drift *correction* (drift is detected and flagged, and needs a reviewer today)
- a single-view continuation segment when one source ends before a required completion
- benchmarking Qwen3-VL-4B / 8B / larger models on real UNIS recordings

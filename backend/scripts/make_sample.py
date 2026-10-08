"""Generate the synthetic demo pair used by the tests into samples/ (no GPU, no real footage needed).

    uv run python scripts/make_sample.py
Produces samples/physical.mp4, samples/system.mp4, samples/sop.md and samples/mock_events.json
(set MOCK_EVENTS_PATH to the latter to simulate Qwen3-VL output without a model endpoint).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import synth  # noqa: E402

out = Path(__file__).resolve().parents[2] / "samples"
synth.make_pair(out)
(out / "mock_events.json").write_text(json.dumps(synth.EVENTS, indent=2), encoding="utf-8")
for f in ("phys.wav", "sys.wav"):
    (out / f).unlink(missing_ok=True)
print(f"sample written to {out}")

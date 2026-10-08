"""Built-in offset estimators (numpy only). Convention everywhere: physical_time = system_time + lag."""

from __future__ import annotations

import numpy as np


def _peak_confidence(cc: np.ndarray, peak: int, guard: int) -> float:
    """1 - (best competing peak / best peak), competing peaks at least ``guard`` samples away."""
    best = float(cc[peak])
    if best <= 0:
        return 0.0
    masked = cc.copy()
    masked[max(0, peak - guard): peak + guard + 1] = -np.inf
    second = float(np.max(masked)) if np.isfinite(masked).any() else 0.0
    return float(np.clip(1.0 - max(second, 0.0) / best, 0.0, 1.0))


def _parabolic(cc: np.ndarray, i: int) -> float:
    if 0 < i < len(cc) - 1:
        a, b, c = cc[i - 1], cc[i], cc[i + 1]
        denom = a - 2 * b + c
        if denom != 0:
            return i + 0.5 * (a - c) / denom
    return float(i)


def xcorr_lag(a: np.ndarray, b: np.ndarray, rate: float, *, max_lag_s: float, phat: bool,
              guard_s: float = 0.05) -> tuple[float, float]:
    """Lag (seconds) such that a(t) ≈ b(t - lag); returns (lag_s, confidence)."""
    if len(a) < 4 or len(b) < 4:
        return 0.0, 0.0
    a = a - a.mean()
    b = b - b.mean()
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    spec = np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n))
    if phat:
        spec /= np.abs(spec) + 1e-12
    cc = np.fft.irfft(spec, n)
    max_lag = int(min(max_lag_s * rate, n // 2 - 1))
    # reorder to lags [-max_lag, +max_lag]
    window = np.concatenate([cc[-max_lag:], cc[: max_lag + 1]]) if max_lag > 0 else cc[:1]
    i = int(np.argmax(window))
    conf = _peak_confidence(window, i, max(1, int(guard_s * rate)))
    lag = (_parabolic(window, i) - max_lag) / rate
    return lag, conf


def onset_envelope(samples: np.ndarray, sr: int, hop_s: float = 0.01) -> np.ndarray:
    """Positive log-energy flux at 1/hop_s Hz — robust to different mics / gains."""
    hop = max(1, int(sr * hop_s))
    n = len(samples) // hop
    if n < 3:
        return np.zeros(0, dtype=np.float32)
    frames = samples[: n * hop].reshape(n, hop)
    energy = np.log1p(1000 * np.sqrt(np.mean(frames ** 2, axis=1)))
    flux = np.maximum(np.diff(energy, prepend=energy[0]), 0)
    return flux.astype(np.float32)


def audio_lag(phys: np.ndarray, sys: np.ndarray, sr: int, *, max_lag_s: float) -> dict[str, tuple[float, float]]:
    out = {"gcc-phat-audio": xcorr_lag(phys, sys, sr, max_lag_s=max_lag_s, phat=True, guard_s=0.03)}
    env_rate = 100.0
    out["onset-envelope"] = xcorr_lag(onset_envelope(phys, sr), onset_envelope(sys, sr), env_rate,
                                      max_lag_s=max_lag_s, phat=False, guard_s=0.15)
    return out


def brightness_lag(phys_frames: np.ndarray, sys_frames: np.ndarray, fps: float, *, max_lag_s: float) -> tuple[float, float]:
    """Align abrupt global brightness changes (flash, light switch, screen wake)."""
    def series(frames: np.ndarray) -> np.ndarray:
        mean = frames.reshape(len(frames), -1).mean(axis=1).astype(np.float32)
        return np.abs(np.diff(mean, prepend=mean[:1] if len(mean) else mean))
    return xcorr_lag(series(phys_frames), series(sys_frames), fps, max_lag_s=max_lag_s, phat=False, guard_s=0.2)


def activity_mask(frames: np.ndarray, threshold: float = 6.0) -> np.ndarray:
    """Per-frame boolean: meaningful pixel change vs previous frame."""
    if len(frames) < 2:
        return np.zeros(len(frames), dtype=bool)
    diff = np.abs(np.diff(frames.astype(np.int16), axis=0)).mean(axis=(1, 2))
    return np.concatenate([[False], diff > threshold])

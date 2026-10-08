import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { fmt, type Segment } from "./api";

export interface DualPlayerHandle { seek: (sharedMs: number) => void; time: () => number; play: () => void }

/** A narration clip on the shared clock (already mapped from output time). */
export interface LiveCue { id: string; src: string; startMs: number; endMs: number; text: string }

interface Props {
  physicalSrc: string;
  systemSrc: string;
  sharedStartPhysicalMs: number;
  lagMs: number; // physical = system + lag
  durationMs: number;
  onTime?: (sharedMs: number) => void;
  cues?: LiveCue[]; // when given, narration plays and captions show — a live preview, no render needed
  compact?: boolean;
}

/** Map an output-time interval to shared time; null if it falls in the intro or a cut. */
export function outToShared(segments: Segment[], outMs: number): number | null {
  const seg = segments.find((s) => s.kind === "source" && s.out_start_ms <= outMs && outMs < s.out_end_ms);
  return seg ? seg.shared_start_ms + outMs - seg.out_start_ms : null;
}

/** Two proxies played against one shared clock; the WISE video is the master. */
export const DualPlayer = forwardRef<DualPlayerHandle, Props>(function DualPlayer(
  { physicalSrc, systemSrc, sharedStartPhysicalMs, lagMs, durationMs, onTime, cues = [], compact }, ref,
) {
  const phys = useRef<HTMLVideoElement>(null);
  const sys = useRef<HTMLVideoElement>(null);
  const [shared, setShared] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [voice, setVoice] = useState(true);
  const audio = useRef(new Map<string, HTMLAudioElement>());
  const started = useRef(new Set<string>());

  const physAt = useCallback((s: number) => (sharedStartPhysicalMs + s) / 1000, [sharedStartPhysicalMs]);
  const sysAt = useCallback((s: number) => (sharedStartPhysicalMs + s - lagMs) / 1000, [sharedStartPhysicalMs, lagMs]);

  // one <audio> per clip, reused across renders
  useEffect(() => {
    const map = audio.current;
    const keep = new Set(cues.map((c) => c.id + c.src));
    for (const [k, a] of map) if (!keep.has(k)) { a.pause(); map.delete(k); }
    for (const c of cues) if (!map.has(c.id + c.src)) { const a = new Audio(c.src); a.preload = "auto"; map.set(c.id + c.src, a); }
  }, [cues]);
  useEffect(() => () => { for (const a of audio.current.values()) a.pause(); }, []);

  const stopVoices = useCallback(() => {
    for (const a of audio.current.values()) a.pause();
    started.current.clear();
  }, []);

  const seek = useCallback((s: number) => {
    const t = Math.max(0, Math.min(durationMs, s));
    if (sys.current) sys.current.currentTime = sysAt(t);
    if (phys.current) phys.current.currentTime = physAt(t);
    stopVoices();
    setShared(t);
    onTime?.(t);
  }, [durationMs, physAt, sysAt, onTime, stopVoices]);

  const toggle = useCallback(async () => {
    const s = sys.current, p = phys.current;
    if (!s || !p) return;
    if (playing) { s.pause(); p.pause(); stopVoices(); setPlaying(false); return; }
    if (shared >= durationMs - 50) seek(0);
    await Promise.all([s.play(), p.play()]).catch(() => undefined);
    setPlaying(true);
  }, [playing, shared, durationMs, seek, stopVoices]);

  useImperativeHandle(ref, () => ({ seek, time: () => shared, play: () => { if (!playing) void toggle(); } }),
    [seek, shared, playing, toggle]);

  useEffect(() => { seek(shared); /* re-align when the offset changes */ }, [lagMs]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const s = sys.current;
      const p = phys.current;
      if (s && p) {
        const t = s.currentTime * 1000 - sharedStartPhysicalMs + lagMs;
        if (Math.abs(p.currentTime - physAt(t)) > 0.08) p.currentTime = physAt(t);
        if (t >= durationMs && !s.paused) { s.pause(); p.pause(); setPlaying(false); }
        if (voice) {
          for (const c of cues) {
            const a = audio.current.get(c.id + c.src);
            if (!a || started.current.has(c.id) || t < c.startMs || t >= c.endMs) continue;
            started.current.add(c.id);
            a.currentTime = Math.max(0, (t - c.startMs) / 1000);
            void a.play().catch(() => undefined);
          }
        }
        setShared(t);
        onTime?.(t);
      }
      raf = requestAnimationFrame(tick);
    };
    if (playing) raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, sharedStartPhysicalMs, lagMs, durationMs, physAt, onTime, cues, voice]);

  const caption = useMemo(() => cues.find((c) => shared >= c.startMs && shared < c.endMs)?.text, [cues, shared]);

  return (
    <div className={`dual ${compact ? "compact" : ""}`}>
      <div className="dual-videos">
        <figure><video ref={phys} src={physicalSrc} muted preload="auto" onLoadedMetadata={() => seek(shared)} /><figcaption>Floor</figcaption></figure>
        <figure><video ref={sys} src={systemSrc} muted preload="auto" onLoadedMetadata={() => seek(shared)} /><figcaption>WISE</figcaption></figure>
        {cues.length > 0 && caption && <div className="live-caption">{caption}</div>}
      </div>
      <div className="dual-controls">
        <button onClick={toggle} className="icon" aria-label={playing ? "Pause" : "Play"} title="Play / pause (Space)">{playing ? "❚❚" : "▶"}</button>
        <button onClick={() => seek(shared - 1000 / 30)} className="icon" title="Previous frame">‹</button>
        <button onClick={() => seek(shared + 1000 / 30)} className="icon" title="Next frame">›</button>
        <input type="range" min={0} max={durationMs} value={shared} onChange={(e) => seek(Number(e.target.value))} aria-label="Position" />
        <span className="mono">{fmt(shared)}</span>
        {cues.length > 0 && (
          <label className="check small" title="Play the narration while previewing">
            <input type="checkbox" checked={voice} onChange={(e) => { setVoice(e.target.checked); if (!e.target.checked) stopVoices(); }} />
            Narration
          </label>
        )}
      </div>
    </div>
  );
});

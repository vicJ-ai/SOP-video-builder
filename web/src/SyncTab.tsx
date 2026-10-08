import { useEffect, useRef, useState } from "react";
import { api, fmt } from "./api";
import { DualPlayer, type DualPlayerHandle } from "./DualPlayer";
import type { TabProps } from "./ProjectPage";

interface Waves { rate: number; physical: number[]; system: number[] }
const HALF_MS = 1500;

function WavePair({ waves, physCentreMs, lagMs }: { waves: Waves; physCentreMs: number; lagMs: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const ctx = c.getContext("2d")!;
    const w = (c.width = c.clientWidth * devicePixelRatio);
    const h = (c.height = 120 * devicePixelRatio);
    ctx.clearRect(0, 0, w, h);
    const style = getComputedStyle(c);
    const draw = (data: number[], centreMs: number, top: number, color: string) => {
      ctx.fillStyle = color;
      const n = Math.round((2 * HALF_MS * waves.rate) / 1000);
      const start = Math.round(((centreMs - HALF_MS) * waves.rate) / 1000);
      for (let i = 0; i < n; i++) {
        const v = data[start + i] ?? 0;
        const x = (i / n) * w;
        const bh = v * (h / 2 - 8);
        ctx.fillRect(x, top + (h / 2 - bh) / 2, Math.max(1, w / n - 0.5), bh);
      }
    };
    draw(waves.physical, physCentreMs, 0, style.getPropertyValue("--c-physical"));
    draw(waves.system, physCentreMs - lagMs, h / 2, style.getPropertyValue("--c-system"));
    ctx.fillStyle = style.getPropertyValue("--fg");
    ctx.fillRect(w / 2, 0, 1, h);
  }, [waves, physCentreMs, lagMs]);
  return <canvas ref={canvas} className="waves" />;
}

export function SyncTab({ p, refresh, rerun }: TabProps) {
  const s = p.sync;
  const [lag, setLag] = useState(s?.lag_ms ?? 0);
  const [waves, setWaves] = useState<Waves | null>(null);
  const player = useRef<DualPlayerHandle>(null);
  useEffect(() => { setLag(s?.lag_ms ?? 0); }, [s?.lag_ms]);
  useEffect(() => {
    if (p.exists.waveforms) fetch(api.file(p.id, p.files.waveforms!, p.updated_at)).then((r) => r.json()).then(setWaves).catch(() => undefined);
  }, [p.id, p.exists.waveforms, p.files.waveforms, p.updated_at]);

  if (!s) return <p className="muted">Synchronization has not run yet.</p>;
  const changed = lag !== s.lag_ms;

  async function apply(value: number | null) {
    refresh(await api.setSync(p.id, value));
    await rerun("sync");
  }

  return (
    <div className="stack">
      <div className="grid2">
        <div className="card">
          <h3>Offset</h3>
          <p className="big mono">{lag} ms</p>
          <p className="muted small">physical time = WISE time + offset · shared interval {fmt(s.shared_duration_ms)} · chosen by <b>{s.method}</b>
            {s.manual && " (manual)"} · drift {s.drift_ms} ms</p>
          <div className="row wrap">
            {[-200, -40, -10, 10, 40, 200].map((d) => (
              <button key={d} onClick={() => setLag(lag + d)}>{d > 0 ? `+${d}` : d}</button>
            ))}
            <input type="number" value={lag} onChange={(e) => setLag(Number(e.target.value))} style={{ width: 110 }} />
          </div>
          <div className="row">
            <button className="primary" disabled={!changed || p.running} onClick={() => apply(lag)}>Use this offset & re-sync</button>
            {p.sync_override_ms !== null && <button onClick={() => apply(null)} disabled={p.running}>Clear manual offset</button>}
          </div>
          {s.review_reasons.length > 0 && (
            <ul className="flags">{s.review_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          )}
        </div>
        <div className="card">
          <h3>Estimators</h3>
          <table>
            <thead><tr><th>Method</th><th>Offset</th><th>Confidence</th><th /></tr></thead>
            <tbody>
              {s.estimates.map((e) => (
                <tr key={e.method} className={e.method === s.method ? "selected" : ""}>
                  <td>{e.method}</td><td className="mono">{e.lag_ms} ms</td><td>{(e.score * 100).toFixed(0)}%</td>
                  <td><button className="link" onClick={() => setLag(e.lag_ms)}>try</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {s.outside.length > 0 && (<>
            <h4>Footage outside the shared interval</h4>
            <ul className="flags">{s.outside.map((o, i) => (
              <li key={i}><b>{o.role}</b> {fmt(o.source_start_ms)}–{fmt(o.source_end_ms)}: {o.reason}</li>
            ))}</ul>
          </>)}
        </div>
      </div>

      <div className="grid3">
        {s.checks.map((c) => (
          <div key={c.label} className={`card check ${c.ok ? "ok" : "bad"}`}>
            <div className="row between">
              <h3>{c.label}</h3>
              <span className={`pill ${c.ok ? "done" : "failed"}`}>{c.ok ? "confirmed" : "check"}</span>
            </div>
            <p className="muted small">at {fmt(c.shared_ms)} · local offset {c.local_lag_ms ?? "—"} ms · error {c.error_ms ?? "—"} ms</p>
            {waves && <WavePair waves={waves} physCentreMs={s.shared_start_physical_ms + c.shared_ms} lagMs={lag} />}
            <button onClick={() => player.current?.seek(c.shared_ms - 500)}>Watch this landmark</button>
          </div>
        ))}
      </div>
      {!waves && <p className="muted small">No audio waveforms (one recording has no audio) — verify visually.</p>}

      {p.files.physical && p.files.system && (
        <div className="card">
          <h3>Paired preview {changed && <span className="pill stale">previewing {lag} ms</span>}</h3>
          <DualPlayer ref={player} physicalSrc={api.file(p.id, p.files.physical)} systemSrc={api.file(p.id, p.files.system)}
                      sharedStartPhysicalMs={s.shared_start_physical_ms} lagMs={lag}
                      durationMs={s.shared_duration_ms} />
          <p className="muted small">Place a distinct landmark (scanner beep, tap, flash) in both views. Never shift a WISE tap to coincide with a later physical action.</p>
        </div>
      )}
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fmt, isHint, PLACEHOLDERS, type Event, type Project, type View } from "./api";
import { DualPlayer, outToShared, type DualPlayerHandle, type LiveCue } from "./DualPlayer";
import type { TabProps } from "./ProjectPage";

const FRAME = 1000 / 30;
const EDGE_PX = 7;
const snap = (ms: number) => Math.round(ms / FRAME) * FRAME;

type Drag = { id: string; mode: "start" | "end" | "move"; x0: number; s0: number; e0: number; s: number; e: number; moved: boolean };

/** Timeline track: drag a block's edges to resize, its middle to move; click empty space to seek. */
function Track({ events, duration, now, selected, onSelect, onCommit, onSeek, cuts }: {
  events: Event[]; duration: number; now: number; selected: string | null; cuts: { start_ms: number; end_ms: number }[];
  onSelect: (e: Event) => void; onCommit: (id: string, start: number, end: number) => void; onSeek: (ms: number) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const msPerPx = () => duration / Math.max(1, box.current?.getBoundingClientRect().width ?? 1);
  const pct = (ms: number) => `${(100 * ms) / Math.max(1, duration)}%`;

  function down(ev: React.PointerEvent, e: Event) {
    ev.stopPropagation();
    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    const x = ev.clientX - r.left;
    const mode = r.width > 3 * EDGE_PX && x < EDGE_PX ? "start" : r.width > 3 * EDGE_PX && x > r.width - EDGE_PX ? "end" : "move";
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    setDrag({ id: e.id, mode, x0: ev.clientX, s0: e.start_ms, e0: e.end_ms, s: e.start_ms, e: e.end_ms, moved: false });
    onSelect(e);
  }
  function move(ev: React.PointerEvent) {
    if (!drag) return;
    const d = (ev.clientX - drag.x0) * msPerPx();
    let { s0: s, e0: e } = drag;
    if (drag.mode === "start") s = Math.min(snap(s + d), e - 100);
    else if (drag.mode === "end") e = Math.max(snap(e + d), s + 100);
    else { const len = e - s; s = Math.max(0, Math.min(duration - len, snap(s + d))); e = s + len; }
    setDrag({ ...drag, s: Math.max(0, s), e: Math.min(duration, e), moved: drag.moved || Math.abs(ev.clientX - drag.x0) > 4 });
    onSeek(drag.mode === "end" ? e : s);
  }
  function up() {
    if (drag?.moved) onCommit(drag.id, Math.round(drag.s), Math.round(drag.e));
    setDrag(null);
  }

  return (
    <div ref={box} className="track editable" aria-label="Action timeline"
         onPointerDown={(ev) => { const r = box.current!.getBoundingClientRect(); onSeek((ev.clientX - r.left) * msPerPx()); }}>
      <span className="lane-label" style={{ top: 4 }}>Floor</span>
      <span className="lane-label" style={{ top: 44 }}>WISE</span>
      {cuts.map((c, i) => <div key={i} className="cut" style={{ left: pct(c.start_ms), width: pct(c.end_ms - c.start_ms) }} />)}
      {events.map((e) => {
        const live = drag?.id === e.id ? drag : null;
        const s = live ? live.s : e.start_ms;
        const en = live ? live.e : e.end_ms;
        return (
          <div key={e.id} role="button" tabIndex={-1} title={`${fmt(s)}–${fmt(en)}  ${isHint(e) ? "(not described yet)" : e.action}`}
               className={`ev ${e.view} ${e.kind} ${isHint(e) ? "hint" : ""} ${selected === e.id ? "sel" : ""} ${live ? "dragging" : ""}`}
               style={{ left: pct(s), width: `max(8px, ${pct(en - s)})` }}
               onPointerDown={(ev) => down(ev, e)} onPointerMove={move} onPointerUp={up} onPointerCancel={() => setDrag(null)}>
            <span className="grip l" /><span className="grip r" />
          </div>
        );
      })}
      <div className="playhead" style={{ left: pct(now) }} />
    </div>
  );
}

const VIEWS: { v: View; label: string }[] = [{ v: "physical", label: "Floor" }, { v: "system", label: "WISE" }, { v: "both", label: "Both" }];

function Row({ e, p, autoLine, audio, selected, locked, onSelect, save, remove }: {
  e: Event; p: Project; autoLine: string | undefined; audio: string | undefined; selected: boolean; locked: boolean;
  onSelect: () => void; save: (patch: Partial<Event>) => void; remove: () => void;
}) {
  const hint = isHint(e);
  const savedAction = PLACEHOLDERS.has(e.action) ? "" : e.action; // generic detector text shows as empty
  const [action, setAction] = useState(savedAction);
  const [narr, setNarr] = useState(e.narration ?? "");
  const rowRef = useRef<HTMLDivElement>(null);
  // never overwrite text the reviewer is still typing
  const dirtyAction = action.trim() !== savedAction && action.trim() !== "";
  const dirtyNarr = narr.trim() !== (e.narration ?? "");
  useEffect(() => { if (!dirtyAction) setAction(savedAction); }, [savedAction]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!dirtyNarr) setNarr(e.narration ?? ""); }, [e.narration]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (selected) rowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [selected]);
  const play = () => { if (audio) void new Audio(audio).play(); };
  // every save carries any unsaved text, so changing the view or step never loses a description
  const saveAll = (patch: Partial<Event>) => save({
    ...(dirtyAction ? { action: action.trim() } : {}), ...(dirtyNarr ? { narration: narr.trim() } : {}), ...patch,
  });
  const commitAction = () => { if (dirtyAction) saveAll({}); };
  const commitNarr = () => { if (dirtyNarr) saveAll({}); };
  const enter = (fn: () => void) => (ev: React.KeyboardEvent) => { if (ev.key === "Enter") { (ev.target as HTMLElement).blur(); fn(); } };

  return (
    <div ref={rowRef} className={`edit-row ${hint ? "hint" : ""} ${selected ? "sel" : ""} ${e.view}`} onClick={onSelect}>
      <button className="time mono" title="Jump here" onClick={onSelect}>{fmt(e.start_ms)}</button>
      <div className="edit-main">
        <input className="action" disabled={locked} value={action} onChange={(x) => setAction(x.target.value)}
               onBlur={commitAction} onKeyDown={enter(() => undefined)}
               placeholder={hint ? "What happens here? (skipped until you describe it or pick a step)" : "Describe what happens (optional)"} />
        <div className="row wrap">
          <div className="seg" role="group" aria-label="Where it happens">
            {VIEWS.map(({ v, label }) => (
              <button key={v} disabled={locked} className={e.view === v ? "active" : ""} onClick={(x) => { x.stopPropagation(); if (e.view !== v) saveAll({ view: v }); }}>{label}</button>
            ))}
          </div>
          <select disabled={locked} value={e.sop_step_id ?? ""} onChange={(x) => saveAll({ sop_step_id: x.target.value || null })} aria-label="SOP step">
            <option value="">— no SOP step —</option>
            {p.sop_steps.map((s) => <option key={s.id} value={s.id}>{s.number}. {s.title || s.text.slice(0, 50)}</option>)}
          </select>
        </div>
        {!hint || autoLine || e.sop_step_id ? (
          <div className="narr">
            <span className="muted small">Says:</span>
            <input disabled={locked} value={narr} onChange={(x) => setNarr(x.target.value)} onBlur={commitNarr}
                   onKeyDown={enter(() => undefined)} placeholder={autoLine ?? "(not narrated — pick an SOP step or write a line)"} />
            <button className="icon" title="Hear this line" disabled={!audio} onClick={(x) => { x.stopPropagation(); play(); }}>🔊</button>
          </div>
        ) : null}
      </div>
      <button className="icon trash" disabled={locked} title="Remove (Delete)" aria-label="Remove" onClick={(x) => { x.stopPropagation(); remove(); }}>🗑</button>
    </div>
  );
}

export function EditTab({ p, refresh, setTab }: TabProps) {
  const player = useRef<DualPlayerHandle>(null);
  const [now, setNow] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [undo, setUndo] = useState<{ id: string; label: string } | null>(null);
  const [showHints, setShowHints] = useState(true);
  const [mark, setMark] = useState<number | null>(null);
  const [error, setError] = useState("");
  const s = p.sync;

  const rendering = ["render", "qa", "package"].some((n) => p.stages[n]?.state === "running") || (p.running && p.review.approved);
  const updating = p.running && !rendering;
  const live = useMemo(() => p.events.filter((e) => !e.rejected).sort((a, b) => a.start_ms - b.start_ms), [p.events]);
  const removed = p.events.filter((e) => e.rejected);
  const rows = showHints ? live : live.filter((e) => !isHint(e));
  const cueFor = useMemo(() => new Map(p.cues.map((c) => [c.event_id, c])), [p.cues]);
  const liveCues: LiveCue[] = useMemo(() => p.cues.flatMap((c) => {
    if (c.dropped || c.out_start_ms == null || c.out_end_ms == null || !c.audio) return [];
    const a = outToShared(p.timeline.segments, c.out_start_ms);
    if (a == null) return [];
    return [{ id: c.id, src: api.file(p.id, `work/${c.audio}`), startMs: a, endMs: a + (c.out_end_ms - c.out_start_ms), text: c.spoken_text || c.text }];
  }), [p.cues, p.timeline.segments, p.id]);

  const wrap = useCallback(async (fn: () => Promise<Project>) => {
    setError("");
    try { refresh(await fn()); } catch (err) { setError(String((err as Error).message)); }
  }, [refresh]);
  const save = (id: string) => (patch: Partial<Event>) => wrap(() => api.editEvent(p.id, id, patch));
  const remove = (e: Event) => {
    setUndo({ id: e.id, label: isHint(e) ? "Detected moment" : e.action });
    if (selected === e.id) setSelected(null);
    return wrap(() => api.removeEvent(p.id, e.id));
  };
  const select = (e: Event) => { setSelected(e.id); player.current?.seek(e.start_ms); };

  useEffect(() => { if (!undo) return; const t = setTimeout(() => setUndo(null), 8000); return () => clearTimeout(t); }, [undo]);

  // keyboard: Space play/pause · ←/→ nudge selected 0.1 s (Shift = 1 s) · Delete remove selected
  useEffect(() => {
    const on = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || rendering) return;
      const e = live.find((x) => x.id === selected);
      if (ev.key === " ") { ev.preventDefault(); document.querySelector<HTMLButtonElement>(".dual-controls .icon")?.click(); }
      else if (e && (ev.key === "ArrowLeft" || ev.key === "ArrowRight")) {
        ev.preventDefault();
        const d = (ev.key === "ArrowLeft" ? -1 : 1) * (ev.shiftKey ? 1000 : 100);
        void save(e.id)({ start_ms: Math.max(0, e.start_ms + d), end_ms: e.end_ms + d });
      } else if (e && (ev.key === "Delete" || ev.key === "Backspace")) { ev.preventDefault(); void remove(e); }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  if (!s || !p.files.physical || !p.files.system) return <p className="muted">Synchronize first.</p>;
  const cov = p.coverage.steps;
  const shown = cov.filter((c) => (c.human_override ?? c.status) === "demonstrated" || (c.human_override ?? c.status) === "partial").length;
  const hints = live.filter(isHint).length;
  const finalReady = p.stages.package?.state === "done" && p.review.approved && !p.running;

  return (
    <div className="stack">
      <div className="card">
        <DualPlayer ref={player} physicalSrc={api.file(p.id, p.files.physical)} systemSrc={api.file(p.id, p.files.system)}
                    sharedStartPhysicalMs={s.shared_start_physical_ms} lagMs={s.lag_ms} durationMs={s.shared_duration_ms}
                    onTime={setNow} cues={liveCues} />
        <Track events={live} duration={s.shared_duration_ms} now={now} selected={selected} cuts={p.timeline.cuts}
               onSelect={(e) => setSelected(e.id)} onSeek={(ms) => player.current?.seek(ms)}
               onCommit={(id, start, end) => void save(id)({ start_ms: start, end_ms: end })} />
        <div className="row wrap between">
          <div className="row wrap">
            <button disabled={rendering} onClick={() => wrap(async () => {
              const r = await api.addEvent(p.id, { start_ms: Math.round(now), end_ms: Math.round(now) + 2000, view: "both" });
              setSelected(r.created);
              return r;
            })}>+ Add action here</button>
            <span className="sep" />
            {mark === null
              ? <button disabled={rendering} onClick={() => setMark(Math.round(now))} title="Remove a stretch of footage from the video">✂ Cut from here…</button>
              : <>
                  <button className="primary" disabled={now <= mark} onClick={() => {
                    void wrap(() => api.setTimeline(p.id, { cuts: [...p.timeline.cuts, { start_ms: mark, end_ms: Math.round(now), reason: "reviewer cut" }] }));
                    setMark(null);
                  }}>✂ Cut {fmt(mark)} → here</button>
                  <button onClick={() => setMark(null)}>Cancel</button>
                </>}
            {p.timeline.cuts.map((c) => (
              <span key={c.id} className="chip">cut {fmt(c.start_ms)}–{fmt(c.end_ms)}
                <button className="link" disabled={rendering} onClick={() => wrap(() => api.setTimeline(p.id, { cuts: p.timeline.cuts.filter((x) => x.id !== c.id) }))}>undo</button>
              </span>
            ))}
          </div>
          <span className="muted small">Drag a block's edges to change its timing, drag the middle to move it. Space plays · ←/→ nudge · Del removes.</span>
        </div>
      </div>

      <div className="render-bar card">
        <div>
          <strong>{shown} of {p.sop_steps.length} SOP steps shown</strong>
          <span className="muted small"> · {p.cues.filter((c) => !c.dropped).length} narration lines{hints ? ` · ${hints} detected moments not described (skipped)` : ""}</span>
          <div className="small muted">
            {rendering ? "Rendering the final video — editing is paused until it finishes."
              : updating ? "Updating narration and preview…"
              : finalReady ? "Final video is up to date." : "Edits save automatically. Play the video above to preview narration and captions."}
          </div>
        </div>
        {finalReady
          ? <button className="primary" onClick={() => setTab("deliverables")}>Open deliverables →</button>
          : <button className="primary big-btn" disabled={rendering} onClick={() => wrap(() => api.renderFinal(p.id))}>
              {rendering ? "Rendering…" : "Render final video"}
            </button>}
      </div>
      {error && <p className="error">{error}</p>}

      <div className="row between">
        <h3>{live.length - hints} actions{hints ? <span className="muted small"> + {hints} detected moments</span> : null}</h3>
        {hints > 0 && (
          <label className="check small"><input type="checkbox" checked={showHints} onChange={(x) => setShowHints(x.target.checked)} />Show undescribed moments</label>
        )}
      </div>
      <div className={`edit-list ${rendering ? "locked" : ""}`}>
        {rows.map((e) => {
          const cue = cueFor.get(e.id);
          return (
            <Row key={e.id} e={e} p={p} selected={selected === e.id} locked={rendering}
                 autoLine={cue && !e.narration ? cue.text : undefined}
                 audio={cue?.audio ? api.file(p.id, `work/${cue.audio}`) : undefined}
                 onSelect={() => select(e)} save={save(e.id)} remove={() => remove(e)} />
          );
        })}
        {rows.length === 0 && <p className="muted">No actions yet — play the video and use “+ Add action here”.</p>}
      </div>
      {removed.length > 0 && (
        <details className="removed">
          <summary className="muted small">{removed.length} removed</summary>
          {removed.map((e) => (
            <div key={e.id} className="row small">
              <span className="mono">{fmt(e.start_ms)}</span><span>{isHint(e) ? "Detected moment" : e.action}</span>
              <button className="link" disabled={rendering} onClick={() => wrap(() => api.restoreEvent(p.id, e.id))}>restore</button>
            </div>
          ))}
        </details>
      )}
      {undo && (
        <div className="toast" role="status">
          Removed “{undo.label.slice(0, 40)}”
          <button className="link" onClick={() => { void wrap(() => api.restoreEvent(p.id, undo.id)); setUndo(null); }}>Undo</button>
        </div>
      )}
    </div>
  );
}

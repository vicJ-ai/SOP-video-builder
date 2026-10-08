import { api, fmt, isHint, type Evidence } from "./api";
import type { TabProps } from "./ProjectPage";

const STATUSES: Evidence[] = ["demonstrated", "partial", "missing", "unclear"];

function effective(c: { status: Evidence; human_override: Evidence | null }) {
  return c.human_override ?? c.status;
}

export function OverviewTab({ p, setTab }: TabProps) {
  const counts = Object.fromEntries(STATUSES.map((s) => [s, p.coverage.steps.filter((c) => effective(c) === s).length]));
  const live = p.events.filter((e) => !e.rejected);
  const hints = live.filter(isHint).length;
  const qa = p.qa.find((q) => q.target === "final.mp4");
  const outdated = !p.review.approved && ["stale", "pending"].includes(p.stages.render?.state ?? "");
  const errors = p.provenance.errors ?? {};
  return (
    <div className="stack">
      <div className="stats">
        <button className="stat" onClick={() => setTab("sync")}>
          <span className="label">Sync offset</span>
          <span className="value mono">{p.sync ? `${p.sync.lag_ms} ms` : "—"}</span>
          <span className={`small ${p.sync?.needs_review ? "warn-text" : "muted"}`}>
            {p.sync ? (p.sync.needs_review ? "needs a look" : `${p.sync.checks.filter((c) => c.ok).length}/3 landmarks confirmed`) : "pending"}
          </span>
        </button>
        <button className="stat" onClick={() => setTab("edit")}>
          <span className="label">Actions</span><span className="value">{live.length - hints}</span>
          <span className="small muted">{hints ? `${hints} detected moments not described` : "on the timeline"}</span>
        </button>
        <button className="stat" onClick={() => setTab("sop")}>
          <span className="label">SOP steps demonstrated</span>
          <span className="value">{counts.demonstrated}/{p.sop_steps.length}</span>
          <span className="small muted">{counts.partial} partial · {counts.missing} missing · {counts.unclear} unclear</span>
        </button>
        <button className="stat" onClick={() => setTab("edit")}>
          <span className="label">Narration lines</span><span className="value">{p.cues.filter((c) => !c.dropped).length}</span>
          <span className="small muted">edit in Edit &amp; review</span>
        </button>
        <button className="stat" onClick={() => setTab("deliverables")}>
          <span className="label">Final QA</span>
          <span className="value">{!qa ? "—" : outdated ? "Outdated" : qa.checks.every((c) => c.ok || c.severity !== "error") ? "Pass" : "Fail"}</span>
          <span className="small muted">
            {!qa ? "after render" : outdated ? "edited since the last render"
              : (n => `${n} finding${n === 1 ? "" : "s"}`)(qa.checks.filter((c) => !c.ok).length)}
          </span>
        </button>
      </div>
      {p.exists.preview && (
        <div className="card">
          <h3>Analysis preview</h3>
          <video className="player" src={api.file(p.id, p.files.preview!, p.stages.preview?.finished_at)} controls preload="metadata" />
          <p className="muted small">Clean synchronized composition with burned-in shared timecode — this is what the vision model analyses.</p>
        </div>
      )}
      <div className="card">
        <h3>Stage log</h3>
        <table>
          <tbody>
            {p.stage_order.map((s) => {
              const st = p.stages[s.name];
              return (
                <tr key={s.name}>
                  <td><span className={`pill ${st.state}`}>{st.state.replace("_", " ")}</span></td>
                  <td>{s.title}</td>
                  <td className="muted small">{st.message}</td>
                  <td className="muted small mono">{st.duration_s ? `${st.duration_s}s` : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {Object.entries(errors).map(([k, v]) => (
          <details key={k}><summary>Error details: {k}</summary><pre>{v}</pre></details>
        ))}
      </div>
    </div>
  );
}

export function SopTab({ p, refresh }: TabProps) {
  const rows = new Map(p.coverage.steps.map((c) => [c.step_id, c]));
  const ev = new Map(p.events.map((e) => [e.id, e]));
  return (
    <div className="stack">
      <div className="card">
        <h3>Steps <span className="muted small">compared with the observed actions ({p.coverage.method})</span></h3>
        <table className="steps">
          <thead><tr><th>#</th><th>SOP step</th><th>Status</th><th>Evidence</th><th>Override</th></tr></thead>
          <tbody>
            {p.sop_steps.map((s) => {
              const c = rows.get(s.id);
              return (
                <tr key={s.id}>
                  <td className="mono">{s.number}</td>
                  <td>
                    {s.title && s.text.startsWith(s.title)
                      ? <><strong>{s.title}</strong><div className="muted small step-body">{s.text.slice(s.title.length).replace(/^[\s.:–—-]+/, "")}</div></>
                      : s.text}
                    {s.controls.length > 0 && <div className="small muted">controls: {s.controls.map((x) => <code key={x}>{x}</code>)}</div>}
                    {s.warnings.map((w) => <div key={w} className="small warn-text">⚠ {w}</div>)}
                  </td>
                  <td>
                    {c && <span className={`pill ${effective(c)}`}>{effective(c)}</span>}
                    {c?.human_override && <div className="small muted">reviewer</div>}
                    {c?.notes.map((n) => <div key={n} className="small muted">{n}</div>)}
                  </td>
                  <td className="small">{c?.event_ids.map((id) => {
                    const e = ev.get(id);
                    return e ? <div key={id}><span className="mono">{fmt(e.start_ms)}</span> {e.action}</div> : null;
                  })}</td>
                  <td>
                    <select disabled={!c || p.running} value={c?.human_override ?? ""}
                            onChange={async (x) => refresh(await api.editCoverage(p.id, s.id, (x.target.value || null) as Evidence | null))}>
                      <option value="">automatic</option>
                      {STATUSES.map((st) => <option key={st} value={st}>{st}</option>)}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="card">
        <h3>Flagged differences ({p.coverage.discrepancies.length})</h3>
        {p.coverage.discrepancies.length === 0 && <p className="muted">None — footage and SOP agree.</p>}
        <ul className="flags">
          {p.coverage.discrepancies.map((d, i) => <li key={i}><code>{d.kind}</code> {d.message}</li>)}
        </ul>
        <p className="muted small">Differences are reported, never “fixed” by inventing actions. Speech is context, not proof.</p>
      </div>
    </div>
  );
}

export function DeliverablesTab({ p }: TabProps) {
  const qa = p.qa.find((q) => q.target === "final.mp4");
  return (
    <div className="stack">
      {p.exists.final && (
        <div className="card">
          <video className="player" controls preload="metadata" src={api.file(p.id, p.files.final!, p.stages.render?.finished_at)} />
        </div>
      )}
      <div className="grid2">
        <div className="card">
          <h3>Files</h3>
          {p.deliverables.length === 0 && <p className="muted">Nothing packaged yet.</p>}
          <ul className="files">
            {p.deliverables.filter((f) => !f.endsWith(".props.json") && !f.endsWith(".log")).map((f) => (
              <li key={f}><a href={api.file(p.id, `out/${f}`)} download>{f}</a></li>
            ))}
          </ul>
          {p.deliverables.some((f) => f.endsWith("_deliverables.zip")) && <a className="button primary" href={api.download(p.id)}>Download everything (.zip)</a>}
          <p className="muted small">The project stays editable: corrections later rerun only the affected stages.</p>
        </div>
        <div className="card">
          <h3>QA {qa && <span className={`pill ${qa.checks.every((c) => c.ok || c.severity !== "error") ? "done" : "failed"}`}>
            {qa.checks.every((c) => c.ok || c.severity !== "error") ? "pass" : "fail"}</span>}</h3>
          {!qa && <p className="muted">Runs after the final render.</p>}
          <ul className="qa">
            {qa?.checks.map((c) => (
              <li key={c.name} className={c.ok ? "ok" : c.severity}>
                <span>{c.ok ? "✓" : c.severity === "error" ? "✕" : "!"}</span>
                <div><div>{c.name}</div><div className="small muted">{c.detail}</div></div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

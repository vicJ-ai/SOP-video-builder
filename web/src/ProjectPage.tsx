import { useCallback, useEffect, useState } from "react";
import { api, type Project } from "./api";
import { EditTab } from "./EditTab";
import { DeliverablesTab, OverviewTab, SopTab } from "./tabs";
import { SyncTab } from "./SyncTab";

const TABS = ["overview", "sync", "edit", "sop", "deliverables"] as const;
export type Tab = (typeof TABS)[number];
const LABEL: Record<Tab, string> = {
  overview: "Overview", sync: "Sync", edit: "Edit & review", sop: "SOP coverage", deliverables: "Deliverables",
};

export interface TabProps { p: Project; refresh: (next?: Project) => void; rerun: (from?: string) => Promise<void>; setTab: (t: Tab) => void }

function suggestedTab(p: Project): Tab {
  if (p.stages.review?.state === "awaiting_review") return "edit";
  if (p.stages.package?.state === "done" && p.review.approved) return "deliverables";
  return "overview";
}

export function ProjectPage({ id }: { id: string }) {
  const [p, setP] = useState<Project | null>(null);
  const [tab, setTab] = useState<Tab | null>(null);
  const [error, setError] = useState("");

  const [pollUntil, setPollUntil] = useState(0);
  const refresh = useCallback((next?: Project) => {
    if (next) {
      setP(next);
      setPollUntil(Date.now() + 15_000); // the server refreshes narration ~1.5 s after an edit
      return;
    }
    api.get(id).then(setP).catch((e) => setError(String(e.message)));
  }, [id]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (!p?.running && Date.now() > pollUntil) return;
    const t = setInterval(() => refresh(), 1500);
    return () => clearInterval(t);
  }, [p?.running, pollUntil, refresh]);
  useEffect(() => { if (p && tab === null) setTab(suggestedTab(p)); }, [p, tab]);

  const rerun = useCallback(async (from?: string) => {
    setError("");
    try { await api.run(id, from); } catch (e) { setError(String((e as Error).message)); }
    refresh();
  }, [id, refresh]);

  if (!p) return <p className="muted">{error || "Loading…"}</p>;
  const failed = p.stage_order.find((s) => p.stages[s.name]?.state === "failed");
  const waiting = p.stage_order.find((s) => p.stages[s.name]?.state === "awaiting_review");
  const stale = p.stage_order.some((s) => p.stages[s.name]?.state === "stale");
  const props: TabProps = { p, refresh, rerun, setTab };

  return (
    <div className="project">
      <div className="project-head">
        <div>
          <h1>{p.title}</h1>
          <p className="muted small">{p.sop?.title} · {p.physical?.filename} + {p.system?.filename}</p>
        </div>
        <div className="row">
          {p.running ? <span className="pill running">working…</span> :
            !waiting && (failed || stale) ? <button onClick={() => rerun()}>{failed ? "Retry" : "Apply changes"}</button> : null}
        </div>
      </div>

      <ol className="pipeline">
        {p.stage_order.map((s) => {
          const st = p.stages[s.name];
          return (
            <li key={s.name} className={`stage ${st?.state ?? "pending"} ${s.gate ? "gate" : ""}`} title={`${s.title}\n${st?.message ?? ""}`}>
              <span className="stage-inner">
                <span className="stage-dot" />
                <span className="stage-name">{s.title}</span>
              </span>
            </li>
          );
        })}
      </ol>

      {error && <p className="error">{error}</p>}
      {failed && (
        <div className="banner error">
          <strong>{failed.title} failed:</strong> {p.stages[failed.name].message}
          <button onClick={() => rerun(failed.name)}>Retry</button>
        </div>
      )}
      {waiting && !p.running && tab !== "edit" && (
        <div className="banner wait">
          <strong>Ready for your review.</strong> Check the actions and narration, then click Render final video.
          <button onClick={() => setTab("edit")}>Open Edit &amp; review</button>
        </div>
      )}
      {stale && !p.running && !waiting && !failed && (
        <div className="banner info">Edits are saved. Only the affected stages will rerun.</div>
      )}

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{LABEL[t]}</button>
        ))}
      </nav>

      <section className="tab-body">
        {tab === "overview" && <OverviewTab {...props} />}
        {tab === "sync" && <SyncTab {...props} />}
        {tab === "edit" && <EditTab {...props} />}
        {tab === "sop" && <SopTab {...props} />}
        {tab === "deliverables" && <DeliverablesTab {...props} />}
      </section>
    </div>
  );
}

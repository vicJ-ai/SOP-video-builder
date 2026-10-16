import { useCallback, useEffect, useState } from "react";
import { api, type Project } from "./api";
import { EditTab } from "./EditTab";
import { DeliverablesTab, OverviewTab, SopTab } from "./tabs";
import { SyncTab } from "./SyncTab";

export const TABS = ["overview", "sync", "edit", "sop", "deliverables"] as const;
export type Tab = (typeof TABS)[number];
export const TAB_LABEL: Record<Tab, string> = {
  overview: "Overview", sync: "Sync", edit: "Edit & review", sop: "SOP coverage", deliverables: "Deliverables",
};

export interface TabProps { p: Project; refresh: (next?: Project) => void; rerun: (from?: string) => Promise<void>; setTab: (t: Tab) => void }

function suggestedTab(p: Project): Tab {
  if (p.stages.review?.state === "awaiting_review") return "edit";
  if (p.stages.package?.state === "done" && p.review.approved) return "deliverables";
  return "overview";
}

function status(p: Project): { label: string; tone: string } {
  const st = (n: string) => p.stages[n]?.state;
  if (p.stage_order.some((s) => st(s.name) === "failed")) return { label: "Needs attention", tone: "failed" };
  if (p.running) {
    const cur = p.stage_order.find((s) => st(s.name) === "running");
    return { label: cur ? `${cur.title}…` : "Working…", tone: "running" };
  }
  if (st("review") === "awaiting_review") return { label: "Ready for review", tone: "awaiting_review" };
  if (st("package") === "done" && p.review.approved) return { label: "Final video ready", tone: "done" };
  return { label: "In progress", tone: "pending" };
}

export function ProjectPage({ id, tab: urlTab }: { id: string; tab: Tab | null }) {
  const [p, setP] = useState<Project | null>(null);
  const [error, setError] = useState("");
  const [pollUntil, setPollUntil] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

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

  const setTab = useCallback((t: Tab) => { window.location.hash = `#/p/${id}/${t}`; }, [id]);
  const rerun = useCallback(async (from?: string) => {
    setError("");
    try { await api.run(id, from); } catch (e) { setError(String((e as Error).message)); }
    refresh();
  }, [id, refresh]);
  const remove = useCallback(async () => {
    setError("");
    setDeleting(true);
    try {
      await api.remove(id);
      window.location.hash = "#/";
    } catch (e) {
      setError(String((e as Error).message));
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [id]);

  if (!p) return <div className="page"><p className="muted">{error || "Loading…"}</p></div>;
  const tab = urlTab ?? suggestedTab(p);
  const failed = p.stage_order.find((s) => p.stages[s.name]?.state === "failed");
  const waiting = p.stage_order.find((s) => p.stages[s.name]?.state === "awaiting_review");
  const stale = p.stage_order.some((s) => p.stages[s.name]?.state === "stale");
  const done = p.stage_order.filter((s) => ["done", "skipped"].includes(p.stages[s.name]?.state)).length;
  const st = status(p);
  const props: TabProps = { p, refresh, rerun, setTab };

  return (
    <div className="page project">
      <header className="page-head">
        <div className="eyebrow">{p.sop?.title || "SOP video"}</div>
        <div className="row between wrap">
          <h1>{p.title}</h1>
          <div className="row">
            <span className={`status ${st.tone}`}><span className="dot" />{st.label}</span>
            {!p.running && !waiting && (failed || stale) &&
              <button className="secondary" onClick={() => rerun()}>{failed ? "Retry" : "Apply changes"}</button>}
            {confirmDelete ? (
              <span className="row delete-confirm">
                <span className="small">{p.running ? "Delete when it finishes?" : "Delete this video?"}</span>
                <button className="secondary" onClick={() => setConfirmDelete(false)} disabled={deleting}>Cancel</button>
                <button className="danger" onClick={remove} disabled={deleting}>{deleting ? "Deleting…" : "Delete"}</button>
              </span>
            ) : (
              <button className="secondary delete-video" onClick={() => setConfirmDelete(true)}>Delete</button>
            )}
          </div>
        </div>
        <div className="progress-strip" title={`${done} of ${p.stage_order.length} steps complete`}>
          {p.stage_order.map((s) => {
            const state = p.stages[s.name]?.state ?? "pending";
            return <span key={s.name} className={`seg-${state}`} title={`${s.title}: ${state.replace("_", " ")}${p.stages[s.name]?.message ? `\n${p.stages[s.name].message}` : ""}`} />;
          })}
        </div>
        <nav className="tabs" aria-label="Project sections">
          {TABS.map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{TAB_LABEL[t]}</button>
          ))}
        </nav>
      </header>

      {error && <p className="error">{error}</p>}
      {failed && (
        <div className="banner error">
          <strong>{failed.title} failed.</strong> <span>{p.stages[failed.name].message}</span>
          <button className="secondary" onClick={() => rerun(failed.name)}>Retry</button>
        </div>
      )}
      {waiting && !p.running && tab !== "edit" && (
        <div className="banner wait">
          <strong>Ready for your review.</strong> <span>Check the actions and narration, then render the final video.</span>
          <button className="primary" onClick={() => setTab("edit")}>Open Edit &amp; review</button>
        </div>
      )}

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

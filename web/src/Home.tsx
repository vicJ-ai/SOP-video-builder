import { useRef, useState } from "react";
import { api, type Summary } from "./api";

type Slot = "physical" | "system" | "sop";
const SLOTS: { key: Slot; n: string; label: string; hint: string; accept: string; tone: string }[] = [
  { key: "physical", n: "01", label: "Floor recording", hint: "The physical work, filmed on the floor", accept: "video/*", tone: "red" },
  { key: "system", n: "02", label: "WISE recording", hint: "The screen recording of the system", accept: "video/*", tone: "blue" },
  { key: "sop", n: "03", label: "SOP document", hint: "The procedure as a Markdown (.md) file", accept: ".md,.markdown,text/markdown", tone: "green" },
];

const STEPS = [
  ["Sync", "Both recordings are aligned on their shared audio and verified at the start, middle and end."],
  ["Edit & review", "Describe actions, match them to SOP steps and preview narration live — nothing to approve."],
  ["Render", "One click renders the narrated, captioned video with an SOP coverage report."],
];

function DropSlot({ slot, file, onFile }: { slot: (typeof SLOTS)[number]; file: File | null; onFile: (f: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <button
      type="button"
      className={`drop tone-${slot.tone} ${over ? "over" : ""} ${file ? "filled" : ""}`}
      onClick={() => input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}
    >
      <span className="drop-top">
        <span className="drop-n">{slot.n}</span>
        <span className="drop-label">{slot.label}</span>
      </span>
      <span className="drop-body">
        {file
          ? <><span className="drop-file">{file.name}</span><span className="muted small">{(file.size / 1e6).toFixed(1)} MB · click to change</span></>
          : <><span className="drop-cta">Drop file or browse</span><span className="muted small">{slot.hint}</span></>}
      </span>
      <span className="drop-bar" aria-hidden />
      <input ref={input} type="file" hidden accept={slot.accept} onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
    </button>
  );
}

export function Home({ projects }: { projects: Summary[] }) {
  const [files, setFiles] = useState<Record<Slot, File | null>>({ physical: null, system: null, sop: null });
  const [title, setTitle] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ready = files.physical && files.system && files.sop;
  const count = Object.values(files).filter(Boolean).length;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.set("title", title || files.sop!.name.replace(/\.(md|markdown)$/i, ""));
    form.set("physical", files.physical!);
    form.set("system", files.system!);
    form.set("sop", files.sop!);
    form.set("auto_approve", String(autoApprove));
    try {
      const p = await api.create(form);
      window.location.hash = `#/p/${p.id}`;
    } catch (err) {
      setError(String((err as Error).message));
      setBusy(false);
    }
  }

  return (
    <div className="page home">
      <header className="hero">
        <div className="eyebrow">UNIS · Training</div>
        <h1>Turn a process into a<br />training video.</h1>
        <p className="lead">Upload the floor recording, the WISE screen recording and the SOP. The builder syncs them,
          maps every action to the procedure and produces a narrated, captioned video.</p>
      </header>

      <form className="upload" onSubmit={submit}>
        <div className="section-head">
          <h2>New SOP video</h2>
          <span className="muted">{count}/3 files</span>
        </div>
        <div className="drops">
          {SLOTS.map((s) => (
            <DropSlot key={s.key} slot={s} file={files[s.key]} onFile={(f) => setFiles({ ...files, [s.key]: f })} />
          ))}
        </div>
        <div className="upload-actions">
          <label className="field grow">
            <span>Process title</span>
            <input placeholder="Defaults to the SOP title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="checkbox"><input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} />
            Render without review</label>
          <button className="primary lg" disabled={!ready || busy}>{busy ? "Uploading…" : "Upload & start"}</button>
        </div>
        {error && <p className="error">{error}</p>}
        <p className="muted small">Original files are kept unchanged. Footage outside the shared recording time is flagged, never silently discarded.</p>
      </form>

      <section className="how">
        {STEPS.map(([t, d], i) => (
          <div key={t} className="how-item">
            <span className="how-n">{String(i + 1).padStart(2, "0")}</span>
            <h3>{t}</h3>
            <p className="muted">{d}</p>
          </div>
        ))}
      </section>

      {projects.length > 0 && (
        <section>
          <div className="section-head"><h2>Recent projects</h2></div>
          <div className="project-grid">
            {projects.slice(0, 6).map((p) => (
              <a key={p.id} href={`#/p/${p.id}`} className="project-card">
                <span className={`status ${p.running ? "running" : p.current_state}`}>
                  <span className="dot" />{p.running ? "Working" : p.current_state === "awaiting_review" ? "Ready for review"
                    : p.current_state === "done" ? "Complete" : p.current_state === "failed" ? "Needs attention" : "In progress"}
                </span>
                <strong>{p.title}</strong>
                <span className="bar"><span style={{ width: `${Math.round(p.progress * 100)}%` }} /></span>
                <span className="muted small">Updated {new Date(p.updated_at).toLocaleString()}</span>
              </a>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

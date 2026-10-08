import { useEffect, useRef, useState } from "react";
import { api, type Health, type Summary } from "./api";

type Slot = "physical" | "system" | "sop";
const SLOTS: { key: Slot; label: string; hint: string; accept: string }[] = [
  { key: "physical", label: "Physical recording", hint: "Floor camera: the handling work", accept: "video/*" },
  { key: "system", label: "System recording", hint: "WISE screen recording", accept: "video/*" },
  { key: "sop", label: "SOP (.md)", hint: "Numbered steps in Markdown", accept: ".md,.markdown,text/markdown" },
];

function DropSlot({ slot, file, onFile }: { slot: (typeof SLOTS)[number]; file: File | null; onFile: (f: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <button
      type="button"
      className={`drop ${over ? "over" : ""} ${file ? "filled" : ""}`}
      onClick={() => input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}
    >
      <span className="drop-label">{slot.label}</span>
      <span className="muted small">{file ? `${file.name} · ${(file.size / 1e6).toFixed(1)} MB` : slot.hint}</span>
      <input ref={input} type="file" hidden accept={slot.accept} onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
    </button>
  );
}

function HealthPanel({ health }: { health: Health | null }) {
  if (!health) return null;
  const row = (label: string, ok: boolean, detail: string) => (
    <li key={label}><span className={`dot ${ok ? "ok" : "warn"}`} />{label}<span className="muted small">{detail}</span></li>
  );
  return (
    <section className="card">
      <h3>Environment</h3>
      <ul className="health">
        {row("FFmpeg", !!health.ffmpeg, health.ffmpeg ?? "not found")}
        {row("Renderer", health.renderer !== "remotion" || health.remotion, `${health.renderer}${health.remotion ? " (Remocn/Remotion ready)" : ""}`)}
        {row("Vision model", health.vlm.provider !== "mock", health.vlm.provider === "mock" ? "mock — set VLM_PROVIDER=openai" : health.vlm.model)}
        {row("Voice", health.tts.provider !== "mock", `${health.tts.provider} · ${health.tts.voice}`)}
        {row("SkellySync", health.skellysync, health.skellysync ? "installed (isolated)" : "built-in estimators only")}
        {row("Transcription", health.extras["faster-whisper"], health.extras["faster-whisper"] ? "faster-whisper" : "not installed")}
        {row("OCR", health.extras["paddleocr"], health.extras["paddleocr"] ? "PaddleOCR" : "not installed")}
      </ul>
    </section>
  );
}

export function Home() {
  const [projects, setProjects] = useState<Summary[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [files, setFiles] = useState<Record<Slot, File | null>>({ physical: null, system: null, sop: null });
  const [title, setTitle] = useState("");
  const [autoApprove, setAutoApprove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => api.list().then(setProjects).catch(() => undefined);
    load();
    api.health().then(setHealth).catch(() => undefined);
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);

  const ready = files.physical && files.system && files.sop;

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
    <div className="home">
      <form className="card new" onSubmit={submit}>
        <h2>New SOP video</h2>
        <div className="drops">
          {SLOTS.map((s) => (
            <DropSlot key={s.key} slot={s} file={files[s.key]} onFile={(f) => setFiles({ ...files, [s.key]: f })} />
          ))}
        </div>
        <div className="row">
          <input className="grow" placeholder="Process title (defaults to the SOP title)" value={title} onChange={(e) => setTitle(e.target.value)} />
          <label className="check"><input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)} />Skip review gates</label>
          <button className="primary" disabled={!ready || busy}>{busy ? "Uploading…" : "Upload & start"}</button>
        </div>
        {error && <p className="error">{error}</p>}
        <p className="muted small">Originals are kept unchanged. Footage outside the shared recording interval is flagged, never silently discarded.</p>
      </form>

      <div className="home-grid">
        <section className="card">
          <h3>Projects</h3>
          {projects.length === 0 && <p className="muted">No projects yet.</p>}
          <ul className="projects">
            {projects.map((p) => (
              <li key={p.id}>
                <a href={`#/p/${p.id}`}>
                  <strong>{p.title}</strong>
                  <span className={`pill ${p.current_state}`}>{p.running ? "running" : p.current_state.replace("_", " ")}</span>
                  {p.current && <span className="muted small">{p.current}</span>}
                  <span className="bar"><span style={{ width: `${Math.round(p.progress * 100)}%` }} /></span>
                </a>
              </li>
            ))}
          </ul>
        </section>
        <HealthPanel health={health} />
      </div>
    </div>
  );
}

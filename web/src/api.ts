// Typed client for backend/app/main.py. Shapes mirror backend/app/models.py.

export type View = "system" | "physical" | "both";
export type Evidence = "demonstrated" | "partial" | "missing" | "unclear";
export type StageState = "pending" | "running" | "done" | "skipped" | "failed" | "awaiting_review" | "stale";

export interface StageStatus { state: StageState; message: string; started_at: string; finished_at: string; duration_s: number }
export interface SyncEstimate { method: string; lag_ms: number; score: number; detail: string }
export interface SyncCheck { label: "start" | "middle" | "end"; shared_ms: number; local_lag_ms: number | null; error_ms: number | null; score: number; ok: boolean }
export interface SyncResult {
  lag_ms: number; method: string; estimates: SyncEstimate[]; checks: SyncCheck[]; drift_ms: number;
  shared_start_physical_ms: number; shared_duration_ms: number; manual: boolean; needs_review: boolean;
  review_reasons: string[]; outside: { role: string; source_start_ms: number; source_end_ms: number; reason: string }[];
}
export interface OcrHit { text: string; score: number }
export interface Event {
  id: string; start_ms: number; end_ms: number; view: View; action: string; kind: "action" | "result";
  visible_text: string[]; ocr: OcrHit[]; confidence: number; source: string; sop_step_id: string | null;
  evidence_status: Evidence | null; transcript_context: string; review_required: boolean; review_reasons: string[];
  approved: boolean; rejected: boolean; human_edited: boolean; narration: string | null;
}
export interface SopStep { id: string; number: string; section: string; title: string; text: string; controls: string[]; expected_view: View; completion_evidence: string[]; warnings: string[] }
export interface StepCoverage { step_id: string; status: Evidence; event_ids: string[]; notes: string[]; human_override: Evidence | null }
export interface Discrepancy { kind: string; message: string; step_id: string | null; event_id: string | null }
export interface Cue {
  id: string; event_id: string; text: string; spoken_text: string; kind: string; sop_step_id: string | null;
  needs_review: boolean; review_reasons: string[]; approved: boolean; human_edited: boolean; audio: string;
  duration_ms: number; out_start_ms: number | null; out_end_ms: number | null; anchor_out_ms: number | null;
  schedule_notes: string[]; dropped: boolean;
}
export interface QACheck { name: string; ok: boolean; detail: string; severity: "error" | "warning" | "info" }
export interface Segment { kind: "intro" | "source"; out_start_ms: number; out_end_ms: number; shared_start_ms: number }

export interface Project {
  id: string; title: string; created_at: string; updated_at: string;
  physical: { filename: string; duration_ms: number; width: number; height: number; fps: number; has_audio: boolean } | null;
  system: { filename: string; duration_ms: number; width: number; height: number; fps: number; has_audio: boolean } | null;
  sop: { filename: string; title: string } | null;
  stages: Record<string, StageStatus>;
  stage_order: { name: string; title: string; gate: boolean }[];
  running: boolean;
  sync: SyncResult | null; sync_override_ms: number | null;
  timeline: { intro_ms: number; cuts: { id: string; start_ms: number; end_ms: number; reason: string }[]; segments: Segment[]; duration_ms: number };
  transcript: { role: string; start_ms: number; end_ms: number; text: string }[]; transcript_status: string;
  events: Event[]; sop_steps: SopStep[];
  coverage: { steps: StepCoverage[]; discrepancies: Discrepancy[]; method: string };
  cues: Cue[];
  qa: { target: string; checks: QACheck[]; created_at: string }[];
  review: { approved: boolean; auto_approve: boolean };
  history: { at: string; action: string }[];
  provenance: Record<string, unknown> & { errors?: Record<string, string> };
  files: Record<string, string | null>;
  exists: Record<string, boolean>;
  deliverables: string[];
}

export interface Summary { id: string; title: string; updated_at: string; progress: number; current: string | null; current_state: string; running: boolean }
export interface Health {
  ffmpeg: string | null; remotion: boolean; renderer: string; skellysync: boolean;
  vlm: { provider: string; model: string; base_url: string }; llm: string; tts: { provider: string; voice: string };
  extras: Record<string, boolean>;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

const P = (id: string) => `/api/projects/${id}`;

export const api = {
  health: () => req<Health>("GET", "/api/health"),
  list: () => req<Summary[]>("GET", "/api/projects"),
  get: (id: string) => req<Project>("GET", P(id)),
  create: (form: FormData) => req<Project>("POST", "/api/projects", form),
  remove: (id: string) => req("DELETE", P(id)),
  run: (id: string, from_stage?: string) => req("POST", `${P(id)}/run`, { from_stage: from_stage ?? null }),
  setSync: (id: string, lag_ms: number | null) => req<Project>("PUT", `${P(id)}/sync`, { lag_ms }),
  setTimeline: (id: string, body: { intro_ms?: number; cuts?: { start_ms: number; end_ms: number; reason?: string }[] }) =>
    req<Project>("PUT", `${P(id)}/timeline`, body),
  addEvent: (id: string, body: Partial<Event>) => req<Project & { created: string }>("POST", `${P(id)}/events`, body),
  editEvent: (id: string, eid: string, body: Partial<Event>) => req<Project>("PATCH", `${P(id)}/events/${eid}`, body),
  removeEvent: (id: string, eid: string) => req<Project>("DELETE", `${P(id)}/events/${eid}`),
  restoreEvent: (id: string, eid: string) => req<Project>("POST", `${P(id)}/events/${eid}/restore`),
  editCoverage: (id: string, step: string, human_override: Evidence | null) =>
    req<Project>("PATCH", `${P(id)}/coverage/${step}`, { human_override }),
  renderFinal: (id: string) => req<Project>("POST", `${P(id)}/approve`),
  file: (id: string, path: string, bust?: string) => `${P(id)}/files/${path}${bust ? `?v=${encodeURIComponent(bust)}` : ""}`,
  download: (id: string) => `${P(id)}/download`,
};

export const PLACEHOLDERS = new Set(["Screen change on the WISE system", "Physical handling motion", "Activity in both views", "New action"]);
/** Detected but not described: no description, no SOP step, no narration. Skipped in the video. */
export const isHint = (e: Event) => PLACEHOLDERS.has(e.action) && !e.sop_step_id && !e.narration;

export const fmt = (ms: number | null | undefined) => {
  if (ms == null) return "—";
  const neg = ms < 0;
  const t = Math.abs(Math.round(ms));
  const m = Math.floor(t / 60000);
  const s = Math.floor((t % 60000) / 1000);
  const mmm = t % 1000;
  return `${neg ? "-" : ""}${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(mmm).padStart(3, "0")}`;
};

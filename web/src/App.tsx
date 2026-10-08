import { useEffect, useState } from "react";
import { api, type Health, type Summary } from "./api";
import { Home } from "./Home";
import { ProjectPage, TABS, TAB_LABEL, type Tab } from "./ProjectPage";

function useHash() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export const projectHref = (id: string, tab?: Tab) => `#/p/${id}${tab ? `/${tab}` : ""}`;

const TAB_ICON: Record<Tab, string> = {
  overview: "M3 13h8V3H3v10Zm0 8h8v-6H3v6Zm10 0h8V11h-8v10Zm0-18v6h8V3h-8Z",
  sync: "M7 7h10v3l4-4-4-4v3H5v6h2V7Zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4Z",
  edit: "M3 17.25V21h3.75L17.8 9.94l-3.75-3.75L3 17.25ZM20.7 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83Z",
  sop: "M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17Z",
  deliverables: "M5 20h14v-2H5v2Zm14-9h-4V3H9v8H5l7 7 7-7Z",
};

function Icon({ d }: { d: string }) {
  return <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden><path d={d} fill="currentColor" /></svg>;
}

type Theme = "light" | "dark";
function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try { return localStorage.getItem("sopvb-theme") === "dark" ? "dark" : "light"; } catch { return "light"; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("sopvb-theme", theme); } catch { /* storage unavailable */ }
  }, [theme]);
  return [theme, setTheme];
}

function EnvCard({ health }: { health: Health | null }) {
  if (!health) return null;
  const vision = health.vlm.provider !== "mock";
  const items: [string, boolean, string][] = [
    ["Vision model", vision, vision ? health.vlm.model.split("/").pop() ?? "" : "not connected"],
    ["Voice", health.tts.provider !== "mock", health.tts.provider === "system" ? "Windows voice" : health.tts.provider],
    ["Renderer", health.renderer !== "remotion" || health.remotion, health.renderer === "remotion" ? "Remotion" : "FFmpeg"],
  ];
  return (
    <div className="env-card">
      <div className="env-title">Environment</div>
      <ul>
        {items.map(([label, ok, detail]) => (
          <li key={label}><span className={`dot ${ok ? "ok" : "warn"}`} />{label}<span className="muted">{detail}</span></li>
        ))}
      </ul>
      {!vision && <p className="muted small">Without a vision model, detected moments need a description or SOP step.</p>}
    </div>
  );
}

export function App() {
  const hash = useHash();
  const match = hash.match(/^#\/p\/([a-z0-9-]+)(?:\/([a-z]+))?/);
  const pid = match?.[1];
  const tab = (TABS as readonly string[]).includes(match?.[2] ?? "") ? (match![2] as Tab) : null;
  const [projects, setProjects] = useState<Summary[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [menu, setMenu] = useState(false);
  const [theme, setTheme] = useTheme();

  useEffect(() => {
    const load = () => api.list().then(setProjects).catch(() => undefined);
    load();
    api.health().then(setHealth).catch(() => undefined);
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => setMenu(false), [hash]);

  return (
    <div className={`shell ${menu ? "menu-open" : ""}`}>
      <aside className="sidebar">
        <a href="#/" className="brand" aria-label="SOP Video Builder home">
          <img src="/brand/unis-logo.svg" alt="UNIS" className="logo light-only" />
          <img src="/brand/unis-logo-white.svg" alt="UNIS" className="logo dark-only" />
          <span className="brand-text"><strong>SOP Video Builder</strong><span>Training video studio</span></span>
        </a>
        <button className="menu-toggle" aria-label="Menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>☰</button>

        <nav className="side-nav">
          <a href="#/" className={`side-link new-video ${!pid ? "active" : ""}`}>
            <Icon d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2Z" /> New video
          </a>

          <div className="side-section">Projects</div>
          {projects.length === 0 && <p className="muted small side-empty">No projects yet</p>}
          {projects.map((pr) => {
            const active = pr.id === pid;
            return (
              <div key={pr.id} className={`side-project ${active ? "active" : ""}`}>
                <a href={projectHref(pr.id)} className="side-link">
                  <span className={`dot ${pr.running ? "running" : pr.current_state}`} />
                  <span className="side-title">{pr.title}</span>
                </a>
                {active && (
                  <div className="side-sub">
                    {TABS.map((t) => (
                      <a key={t} href={projectHref(pr.id, t)} className={`side-link sub ${tab === t ? "active" : ""}`}>
                        <Icon d={TAB_ICON[t]} /> {TAB_LABEL[t]}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        <EnvCard health={health} />
        <div className="theme-toggle" role="group" aria-label="Theme">
          {(["light", "dark"] as Theme[]).map((t) => (
            <button key={t} className={theme === t ? "active" : ""} onClick={() => setTheme(t)}>{t === "light" ? "☀ Light" : "☾ Dark"}</button>
          ))}
        </div>
      </aside>

      <main className="main">
        {pid ? <ProjectPage id={pid} key={pid} tab={tab} /> : <Home projects={projects} />}
      </main>
    </div>
  );
}

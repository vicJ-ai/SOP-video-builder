import { useEffect, useState } from "react";
import { Home } from "./Home";
import { ProjectPage } from "./ProjectPage";

function useHash() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export function App() {
  const hash = useHash();
  const match = hash.match(/^#\/p\/([a-z0-9-]+)/);
  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          <span className="logo" aria-hidden>▶</span>
          SOP Video Builder
        </a>
        <span className="muted small">two recordings + SOP → verified, narrated training video</span>
      </header>
      <main className="page">{match ? <ProjectPage id={match[1]} key={match[1]} /> : <Home />}</main>
    </div>
  );
}

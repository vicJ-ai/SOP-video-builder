import { useEffect, useState } from "react";
import { Home } from "./Home";
import { Studio } from "./Studio";

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
          <img src="/brand/item-logo.svg" width="76" height="30" alt="Item"/>
          <span className="brand-divider"/> SOP Video Studio
        </a>
      </header>
      <main className="page">{match ? <Studio id={match[1]} key={match[1]}/> : <Home/>}</main>
    </div>
  );
}

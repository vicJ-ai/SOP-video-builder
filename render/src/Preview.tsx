// Clean analysis preview: both synchronized views, stable labels, burned-in shared timecode.
// Deliberately no branding, captions or animation — nothing may hide evidence from the VLM.
import { AbsoluteFill, OffthreadVideo, staticFile, useCurrentFrame } from "remotion";
import type { PreviewProps } from "./types";

const BAR = 72;
const GAP = 12;

export const timecode = (ms: number) => {
  const total = Math.max(0, Math.floor(ms));
  const h = Math.floor(total / 3_600_000);
  const m = Math.floor((total % 3_600_000) / 60_000);
  const s = Math.floor((total % 60_000) / 1000);
  const mmm = total % 1000;
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(h)}:${p(m)}:${p(s)}.${p(mmm, 3)}`;
};

const Pane: React.FC<{ label: string; src: string; trimBefore: number; left: number; width: number; height: number }> = ({
  label, src, trimBefore, left, width, height,
}) => (
  <div style={{ position: "absolute", left, top: BAR, width, height, background: "#000" }}>
    <OffthreadVideo
      src={staticFile(src)}
      trimBefore={trimBefore}
      muted
      style={{ width: "100%", height: "100%", objectFit: "contain" }}
    />
    <div style={{
      position: "absolute", left: 0, top: 0, padding: "6px 14px", background: "rgba(0,0,0,0.75)",
      color: "#fff", font: "600 26px/1.2 Arial, sans-serif", letterSpacing: 1,
    }}>
      {label}
    </div>
  </div>
);

export const Preview: React.FC<PreviewProps> = (props) => {
  const frame = useCurrentFrame();
  const ms = (frame * 1000) / props.fps;
  const paneW = (props.width - GAP) / 2;
  const paneH = props.height - BAR;
  return (
    <AbsoluteFill style={{ background: "#111" }}>
      <div style={{
        height: BAR, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 24px",
        color: "#fff", font: "600 30px/1 Arial, sans-serif",
      }}>
        <span>{props.title}</span>
        <span style={{ fontFamily: "Consolas, monospace", fontSize: 40 }}>T {timecode(ms)}</span>
        <span style={{ fontFamily: "Consolas, monospace", fontSize: 24, opacity: 0.7 }}>frame {frame}</span>
      </div>
      <Pane label={`PHYSICAL · ${props.physical.label}`} src={props.physical.src} trimBefore={props.physicalStartFrame}
            left={0} width={paneW} height={paneH} />
      <Pane label={`SYSTEM · ${props.system.label}`} src={props.system.src} trimBefore={props.systemStartFrame}
            left={paneW + GAP} width={paneW} height={paneH} />
    </AbsoluteFill>
  );
};

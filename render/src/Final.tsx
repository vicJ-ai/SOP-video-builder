// Final narrated SOP video. Every visual decision here is driven by the canonical timeline the
// backend computes (segments, layout keys, cues, captions) — nothing is timed by hand.
import {
  AbsoluteFill, Audio, Easing, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame,
} from "remotion";
import type { Caption } from "@remotion/captions";
import { Backdrop } from "@/components/remocn/backdrop";
import { CaptionSubtitle } from "@/components/remocn/caption-subtitle";
import { FadeThrough } from "@/components/remocn/fade-through";
import { CheckIconStatic } from "@/components/remocn/icon-check";
import { MicroScaleFade } from "@/components/remocn/micro-scale-fade";
import { Progress } from "@/components/remocn/progress";
import { SoftBlurIn } from "@/components/remocn/soft-blur-in";
import { Stepper } from "@/components/remocn/stepper";
import { Toast, toastStyle, type ToastStyle } from "@/components/remocn/toast";
import { RemocnUIProvider, defaultDarkTheme } from "@/lib/remocn-ui";
import type { FinalProps, Focus, LayoutProp, SegmentProp, SourceRef } from "./types";

const C = {
  bg: "#0a1020",
  panel: "#05080f",
  ink: "#f4f6fb",
  muted: "#9aa6bf",
  accent: "#2f7cf6", // UNIS-neutral blue; swap for brand colour
  border: "rgba(255,255,255,0.08)",
};
const FONT = "Inter, 'Segoe UI', Arial, sans-serif";
const MARGIN = 48;
const TOP = 132;
const BOTTOM = 176;
const GAP = 24;
const TRANSITION = 18; // frames for a layout change

const SYSTEM_SHARE: Record<Focus, number> = { system: 0.66, split: 0.5, physical: 0.34 };

function keyAt(layout: LayoutProp[], frame: number): number {
  for (let i = layout.length - 1; i >= 0; i--) if (layout[i].fromFrame <= frame) return i;
  return -1;
}

/** Fraction of the content width given to the WISE panel, eased between layout keys. */
function systemShare(layout: LayoutProp[], frame: number): number {
  const i = keyAt(layout, frame);
  if (i < 0) return SYSTEM_SHARE.split;
  const target = SYSTEM_SHARE[layout[i].focus];
  const prev = i > 0 && layout[i - 1].toFrame >= layout[i].fromFrame - 1 ? SYSTEM_SHARE[layout[i - 1].focus] : SYSTEM_SHARE.split;
  return interpolate(frame, [layout[i].fromFrame, layout[i].fromFrame + TRANSITION], [prev, target], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.bezier(0.4, 0, 0.2, 1),
  });
}

const Panel: React.FC<{
  source: SourceRef; segments: SegmentProp[]; pick: (s: SegmentProp) => number;
  left: number; width: number; height: number; emphasised: boolean;
}> = ({ source, segments, pick, left, width, height, emphasised }) => (
  <div style={{
    position: "absolute", left, top: TOP, width, height, borderRadius: 20, overflow: "hidden",
    background: C.panel, border: `2px solid ${emphasised ? C.accent : C.border}`,
    boxShadow: emphasised ? "0 18px 60px rgba(47,124,246,0.25)" : "0 10px 40px rgba(0,0,0,0.4)",
  }}>
    {segments.filter((s) => s.kind === "source").map((s) => (
      <Sequence key={s.fromFrame} from={s.fromFrame} durationInFrames={s.durationInFrames} layout="none">
        <OffthreadVideo src={staticFile(source.src)} trimBefore={pick(s)} muted
                        style={{ width: "100%", height: "100%", objectFit: "contain" }} />
      </Sequence>
    ))}
    <div style={{
      position: "absolute", left: 16, top: 16, padding: "6px 14px", borderRadius: 999,
      background: "rgba(5,8,15,0.78)", border: `1px solid ${C.border}`,
    }}>
      <MicroScaleFade text={source.label} fontSize={22} color={C.ink} fontWeight={600} />
    </div>
  </div>
);

const Intro: React.FC<{ props: FinalProps; duration: number }> = ({ props, duration }) => {
  const frame = useCurrentFrame();
  const labels = props.steps.slice(0, 6).map((s) => `Step ${s.number}`);
  const position = interpolate(frame, [20, Math.max(21, duration - 15)], [0, labels.length], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const fadeOut = interpolate(frame, [duration - 10, duration], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Backdrop padding={0} radius={0} shadow=""
              fill={{ type: "gradient", value: `radial-gradient(120% 90% at 20% 10%, #18306a 0%, ${C.bg} 60%)` }}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", gap: 36, opacity: fadeOut, fontFamily: FONT }}>
        <div style={{ color: C.muted, fontSize: 30, letterSpacing: 6, textTransform: "uppercase" }}>{props.subtitle}</div>
        {/* SoftBlurIn centres itself absolutely, so it needs a sized box to sit in the column */}
        <div style={{ position: "relative", width: "100%", height: 120 }}>
          <SoftBlurIn text={props.title} fontSize={84} color={C.ink} fontWeight={700} />
        </div>
        {labels.length > 1 && (
          <div style={{ position: "relative", width: 900, height: 90, transform: "scale(1.5)", marginTop: 24 }}>
            <Stepper steps={labels} style={{ position }} theme={defaultDarkTheme} />
          </div>
        )}
      </AbsoluteFill>
    </Backdrop>
  );
};

const Heading: React.FC<{ layout: LayoutProp[] }> = ({ layout }) => (
  <>
    {layout.map((k, i) => (
      <Sequence key={i} from={k.fromFrame} durationInFrames={Math.max(1, k.toFrame - k.fromFrame)} layout="none">
        <FadeThrough fromText={i > 0 ? layout[i - 1].heading : ""} toText={k.heading} fontSize={40} color={C.ink} fontWeight={650} align="start" />
      </Sequence>
    ))}
  </>
);

const tween = (a: ToastStyle, b: ToastStyle, t: number): ToastStyle => ({
  opacity: a.opacity + (b.opacity - a.opacity) * t,
  translateY: a.translateY + (b.translateY - a.translateY) * t,
  scale: a.scale + (b.scale - a.scale) * t,
});

const Flag: React.FC<{ title: string; description: string; duration: number }> = ({ title, description, duration }) => {
  const frame = useCurrentFrame();
  const t = Math.min(interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" }),
    interpolate(frame, [duration - 10, duration], [1, 0], { extrapolateLeft: "clamp" }));
  return (
    <div style={{ position: "absolute", right: MARGIN, top: TOP + 20, width: 520 }}>
      <Toast title={title} description={description} variant="error" style={tween(toastStyle("hidden"), toastStyle("visible"), t)} />
    </div>
  );
};

export const Final: React.FC<FinalProps> = (props) => {
  const frame = useCurrentFrame();
  const intro = props.segments.find((s) => s.kind === "intro");
  const inIntro = intro !== undefined && frame < intro.fromFrame + intro.durationInFrames;
  const contentW = props.width - 2 * MARGIN - GAP;
  const contentH = props.height - TOP - BOTTOM;
  const share = systemShare(props.layout, frame);
  const systemW = Math.round(contentW * share);
  const physicalW = contentW - systemW;
  const k = props.layout[keyAt(props.layout, frame)];
  const done = k?.stepIndex ?? 0;
  const total = props.steps.length;
  const captions: Caption[] = props.captions.map((c) => ({
    text: c.text, startMs: c.startMs, endMs: c.endMs, timestampMs: c.startMs, confidence: 1,
  }));

  return (
    <RemocnUIProvider theme={defaultDarkTheme}>
      <AbsoluteFill style={{ background: C.bg, fontFamily: FONT }}>
        {/* source audio is muted: only narration is heard */}
        <Panel source={props.physical} segments={props.segments} pick={(s) => s.physicalStartFrame}
               left={MARGIN} width={physicalW} height={contentH} emphasised={k?.focus === "physical"} />
        <Panel source={props.system} segments={props.segments} pick={(s) => s.systemStartFrame}
               left={MARGIN + physicalW + GAP} width={systemW} height={contentH} emphasised={k?.focus === "system"} />

        <div style={{ position: "absolute", left: MARGIN, right: MARGIN, top: 28, height: 80, display: "flex",
                      alignItems: "center", justifyContent: "space-between", gap: 32 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
            <div style={{ color: C.muted, fontSize: 22, letterSpacing: 3, textTransform: "uppercase" }}>{props.title}</div>
            <div style={{ position: "relative", height: 52, overflow: "hidden", whiteSpace: "nowrap" }}><Heading layout={props.layout} /></div>
          </div>
          {total > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 18, color: C.ink, fontSize: 26, flexShrink: 0 }}>
              {done >= total && <CheckIconStatic size={30} color="#3ecf8e" strokeWidth={3} />}
              <span>Step {Math.max(done, 1)} of {total}</span>
              <Progress value={(100 * done) / total} width={300} />
            </div>
          )}
        </div>

        <div style={{ position: "absolute", left: MARGIN * 2, right: MARGIN * 2, bottom: 40, height: BOTTOM - 56,
                      display: "flex", alignItems: "flex-end", justifyContent: "center" }}>
          <CaptionSubtitle captions={captions} combineTokensWithinMilliseconds={0} holdMs={0} fontSize={40} />
        </div>

        {props.cues.map((c) => (
          <Sequence key={c.id} from={c.fromFrame} durationInFrames={c.durationInFrames} layout="none">
            <Audio src={staticFile(c.src)} />
          </Sequence>
        ))}

        {props.draft && props.flags.map((f, i) => (
          <Sequence key={i} from={f.fromFrame} durationInFrames={Math.max(20, f.toFrame - f.fromFrame)} layout="none">
            <Flag title={f.title} description={f.description} duration={Math.max(20, f.toFrame - f.fromFrame)} />
          </Sequence>
        ))}

        {intro && (
          <Sequence from={intro.fromFrame} durationInFrames={intro.durationInFrames}>
            <Intro props={props} duration={intro.durationInFrames} />
          </Sequence>
        )}

        {props.draft && !inIntro && (
          <div style={{ position: "absolute", left: MARGIN, bottom: 18, color: "rgba(255,255,255,0.45)", fontSize: 20,
                        letterSpacing: 4 }}>DRAFT · FOR REVIEW</div>
        )}
      </AbsoluteFill>
    </RemocnUIProvider>
  );
};

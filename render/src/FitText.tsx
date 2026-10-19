import React, {useLayoutEffect, useState} from "react";
import {cancelRender, continueRender, delayRender} from "remotion";

type Props = {
  text: string; width: number; height: number; maxFont: number; minFont: number;
  maxLines: number; label: string; lineHeight?: number; letterSpacing?: number;
};

/** Fit with the actual loaded font. Never shorten, clip, ellipsize or hide an instruction. */
const MeasuredText: React.FC<Props> = ({text, width, height, maxFont, minFont, maxLines, label,
  lineHeight = 1.12, letterSpacing = 0}) => {
  const [handle] = useState(() => delayRender(`Measure ${label}`));
  const [fitted, setFitted] = useState<{font: number; lines: string[]} | null>(null);
  useLayoutEffect(() => {
    let active = true;
    document.fonts.load(`700 ${maxFont}px Satoshi`).then(() => {
      if (!active) return;
      const context = document.createElement("canvas").getContext("2d");
      if (!context) throw new Error("Text measurement unavailable");
      const words = text.trim().split(/\s+/);
      for (let font = maxFont; font >= minFont; font--) {
        context.font = `700 ${font}px Satoshi`;
        const measure = (value: string) => context.measureText(value).width + Math.max(0, value.length - 1) * letterSpacing;
        const lines: string[] = [];
        let line = "", tooWide = false;
        for (const word of words) {
          if (measure(word) > width) {tooWide = true; break;}
          const candidate = line ? `${line} ${word}` : word;
          if (line && measure(candidate) > width) {lines.push(line); line = word;}
          else line = candidate;
        }
        if (line) lines.push(line);
        if (!tooWide && lines.length <= maxLines && lines.length * font * lineHeight <= height) {
          setFitted({font, lines});
          continueRender(handle);
          return;
        }
      }
      throw new Error(`${label} cannot fit ${maxLines} readable line(s) at ${minFont}px. ` +
        "Review the full wording and provide a fact-preserving brief heading/title; no text was truncated.");
    }).catch(error => {if (active) cancelRender(error);});
    return () => {active = false; continueRender(handle);};
  }, [handle, text, width, height, maxFont, minFont, maxLines, label, lineHeight, letterSpacing]);
  return <div data-fitted-text={label} style={{width, height, fontWeight:700,
    fontSize:fitted?.font ?? maxFont, lineHeight, letterSpacing, whiteSpace:"pre"}}>
    {fitted?.lines.join("\n") ?? text}
  </div>;
};

// A new string or geometry gets its own render wait handle; frame-to-frame captures stay deterministic.
export const FitText: React.FC<Props> = props => <MeasuredText key={JSON.stringify(props)} {...props}/>;

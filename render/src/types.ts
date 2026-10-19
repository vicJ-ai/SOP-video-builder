// Props contract with backend/app/stages/render.py. All frame numbers are output frames at `fps`.

export type Focus = "system" | "physical" | "split";

export interface SourceRef {
  src: string; // path relative to the public dir (the project's work/ folder)
  width: number;
  height: number;
  label: string;
}

export interface SegmentProp {
  kind: "intro" | "title" | "source" | "outro";
  availableRoles?: string[];
  fromFrame: number;
  durationInFrames: number;
  physicalStartFrame: number; // first source frame to show (trimBefore)
  systemStartFrame: number;
}

export interface CueProp {
  id: string;
  src: string;
  fromFrame: number;
  durationInFrames: number;
  text: string;
}

export interface CaptionProp {
  startMs: number;
  endMs: number;
  text: string;
  fromFrame?: number;
  endFrame?: number;
}

export interface LayoutProp {
  fromFrame: number;
  toFrame: number;
  focus: Focus;
  heading: string;
  stepIndex: number | null;
}

export interface StepProp {
  id: string;
  number: string;
  text: string;
  status: "demonstrated" | "partial" | "missing" | "unclear";
}

export interface FlagProp {
  fromFrame: number;
  toFrame: number;
  title: string;
  description: string;
}

export type PreviewProps = {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  title: string;
  physical: SourceRef;
  system: SourceRef;
  physicalStartFrame: number;
  systemStartFrame: number;
};

export type FinalProps = {
  fps: number;
  width: number;
  height: number;
  durationInFrames: number;
  title: string;
  subtitle: string;
  draft: boolean;
  physical: SourceRef | null;
  system: SourceRef | null;
  template?: string;
  brandFrames?: {intro: string; outro: string};
  workflow?: string[];
  demo?: boolean;
  segments: SegmentProp[];
  cues: CueProp[];
  captions: CaptionProp[];
  layout: LayoutProp[];
  steps: StepProp[];
  flags: FlagProp[];
};

import { Composition, type CalculateMetadataFunction } from "remotion";
import { Final } from "./Final";
import { Preview } from "./Preview";
import {BrandAnimation, type BrandProps} from "./Brand";
import type { FinalProps, PreviewProps } from "./types";

const source = { src: "", width: 1920, height: 1080, label: "" };

const metadata = <T extends { fps: number; width: number; height: number; durationInFrames: number }>(): CalculateMetadataFunction<T> =>
  ({ props }) => ({
    fps: props.fps,
    width: props.width,
    height: props.height,
    durationInFrames: Math.max(1, props.durationInFrames),
  });

const previewDefaults: PreviewProps = {
  fps: 30,
  width: 1920,
  height: 1080,
  durationInFrames: 90,
  title: "Analysis preview",
  physical: { ...source, label: "Physical" },
  system: { ...source, label: "WMS" },
  physicalStartFrame: 0,
  systemStartFrame: 0,
};

const finalDefaults: FinalProps = {
  fps: 30,
  width: 1920,
  height: 1080,
  durationInFrames: 150,
  title: "SOP title",
  subtitle: "Standard Operating Procedure",
  draft: true,
  physical: { ...source, label: "Floor" },
  system: { ...source, label: "WMS" },
  segments: [{ kind: "intro", fromFrame: 0, durationInFrames: 90, physicalStartFrame: 0, systemStartFrame: 0 }],
  cues: [],
  captions: [],
  layout: [],
  steps: [],
  flags: [],
};

export const Root: React.FC = () => (
  <>
    <Composition id="BrandPreflight" component={BrandAnimation}
      defaultProps={{clip:"outro",frameCount:126} as BrandProps}
      calculateMetadata={({props})=>({durationInFrames:props.frameCount})}
      fps={30} width={1920} height={1080} durationInFrames={126}/>
    <Composition
      id="Preview"
      component={Preview}
      defaultProps={previewDefaults}
      calculateMetadata={metadata<PreviewProps>()}
      fps={30}
      width={1920}
      height={1080}
      durationInFrames={90}
    />
    <Composition
      id="Final"
      component={Final}
      defaultProps={finalDefaults}
      calculateMetadata={metadata<FinalProps>()}
      fps={30}
      width={1920}
      height={1080}
      durationInFrames={150}
    />
  </>
);

import {AbsoluteFill, Img, OffthreadVideo, staticFile, useCurrentFrame} from "remotion";

export type BrandProps = {clip: "intro" | "outro"; frameCount: number; frameDirectory?: string};

/** Complete official animation, with no overlay. Image frames are an exact decoder alternative. */
export const BrandAnimation: React.FC<BrandProps> = ({clip, frameCount, frameDirectory}) => {
  const frame = useCurrentFrame();
  if (frame < 0 || frame >= frameCount) throw new Error("Bookend frame outside approved bounds");
  const style = {width: "100%", height: "100%", objectFit: "contain" as const};
  return <AbsoluteFill style={{background: "#000000"}}>
    {frameDirectory
      ? <Img src={staticFile(`${frameDirectory}/${String(frame).padStart(4, "0")}.png`)} style={style}/>
      : <OffthreadVideo src={staticFile(`brand/${clip}-normalized.mp4`)} muted trimBefore={0} trimAfter={frameCount} style={style}/>}
  </AbsoluteFill>;
};

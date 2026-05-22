import { Composition, getInputProps } from "remotion";
import { MainComp } from "./MainComp";
import type { RenderInput } from "./types";

// Sensible defaults for `remotion studio` previewing without props.
const FALLBACK: RenderInput = {
  videoSrc: "",
  audioSrc: "",
  fps: 60,
  outFps: 60,
  outWidth: 1080,
  outHeight: 1920,
  duration: 1,
  totalFrames: 60,
  camera: {
    fps: 60,
    width: 1920,
    height: 1080,
    out_w: 1080,
    out_h: 1920,
    base_crop_w: 607.5,
    base_crop_h: 1080,
    frames: [],
  },
  subtitles: [],
  title: "",
  style: "hype",
};

export const RemotionRoot: React.FC = () => {
  const props = getInputProps() as Partial<RenderInput>;
  const merged: RenderInput = { ...FALLBACK, ...props } as RenderInput;
  // Ensure correct duration even when previewing.
  const totalFrames = Math.max(1, Math.round(merged.duration * merged.outFps));

  return (
    <>
      <Composition
        id="MainComp"
        component={MainComp}
        durationInFrames={totalFrames}
        fps={merged.outFps}
        width={merged.outWidth}
        height={merged.outHeight}
        defaultProps={merged}
      />
    </>
  );
};

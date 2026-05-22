export type CameraFrame = {
  cx: number;
  cy: number;
  zoom: number;
};

export type CameraPlan = {
  fps: number;
  width: number;
  height: number;
  out_w: number;
  out_h: number;
  base_crop_w: number;
  base_crop_h: number;
  frames: CameraFrame[];
};

export type SubtitleWord = {
  text: string;
  start: number;
  end: number;
};

export type SubtitleGroup = {
  start: number;
  end: number;
  words: SubtitleWord[];
};

export type HudRect = {
  x: number;
  y: number;
  w: number;
  h: number;
};

export type HudConfig = {
  left: HudRect;
  right: HudRect;
  outSize: number;
  outMargin: number;
  swapCorners: boolean;
  show: boolean;
};

export type RenderInput = {
  videoSrc: string;
  audioSrc: string;
  fps: number;
  outFps: number;
  outWidth: number;
  outHeight: number;
  duration: number;
  totalFrames: number;
  camera: CameraPlan;
  subtitles: SubtitleGroup[];
  title: string;
  style: "chill" | "hype" | "clutch";
  framing?: "wide" | "tight";
  wideZoom?: number;
  hud?: HudConfig;
};

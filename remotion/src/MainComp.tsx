import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Subtitles } from "./Subtitles";
import { TitleHook } from "./TitleHook";
import { HudOverlay } from "./HudOverlay";
import type { RenderInput, CameraFrame } from "./types";

/**
 * Map an output frame number to a position in the source video, then to a
 * camera plan entry. Source FPS may differ from output FPS (we keep both 60
 * by default but this stays correct if they ever diverge).
 */
const cameraAt = (
  outFrame: number,
  outFps: number,
  srcFps: number,
  frames: CameraFrame[]
): CameraFrame => {
  if (frames.length === 0) {
    return { cx: 0, cy: 0, zoom: 1 };
  }
  const t = outFrame / outFps; // seconds
  const idx = Math.min(frames.length - 1, Math.max(0, Math.round(t * srcFps)));
  return frames[idx];
};

export const MainComp: React.FC<RenderInput> = (props) => {
  const frame = useCurrentFrame();
  const { fps: outFps, width: outW, height: outH } = useVideoConfig();
  const { camera, videoSrc, audioSrc, baseVideo, subtitles, title, hud, framing, wideZoom } = props;
  const { width: srcW, height: srcH, fps: srcFps } = camera;

  const cam = cameraAt(frame, outFps, srcFps, camera.frames);

  // Framing mode "wide": full source visible, centered, letterboxed with a
  // blurred copy of the source behind. No tracking, no zoom — entire action
  // is always visible.
  const isWide = (framing ?? "wide") === "wide";

  // Compute the wide-mode band geometry up here so we can pass it cleanly
  // to subtitles regardless of which branch renders.
  const wideZ = Math.max(1.0, wideZoom ?? 1.0);
  const wideVisibleW = srcW / wideZ;
  const wideFitScale = outW / wideVisibleW;
  const wideBandH = srcH * wideFitScale;
  const wideBandBottomFrac = ((outH - wideBandH) / 2 + wideBandH) / outH;

  // ---------------------------------------------------------------------
  // FAST PATH: when baseVideo is provided, the entire visual composite
  // (bg, fg, HUD) was pre-baked by ffmpeg with NVENC. Remotion just plays
  // it as a single layer and overlays subtitles + title on top. Decoding
  // one stream instead of four is dramatically faster for headless Chrome.
  // ---------------------------------------------------------------------
  let videoLayer: React.ReactNode;
  if (baseVideo) {
    const url = staticFile(baseVideo);
    videoLayer = (
      <OffthreadVideo
        src={url}
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: outW,
          height: outH,
          display: "block",
        }}
        muted
      />
    );
  } else if (isWide) {
    const fgW = outW;
    const fgH = wideBandH;
    const fgY = (outH - fgH) / 2;
    const innerW = srcW * wideFitScale;
    const innerX = (fgW - innerW) / 2;
    const innerH = srcH * wideFitScale;
    // Background: source video scaled to fill the output entirely, blurred.
    const bgScale = Math.max(outW / srcW, outH / srcH);
    const bgW = srcW * bgScale;
    const bgH = srcH * bgScale;
    const bgX = (outW - bgW) / 2;
    const bgY = (outH - bgH) / 2;

    const url = videoSrc ? staticFile(videoSrc) : "";

    videoLayer = (
      <>
        {/* Blurred background fill */}
        {url ? (
          <div
            style={{
              position: "absolute",
              left: bgX,
              top: bgY,
              width: bgW,
              height: bgH,
              filter: "blur(60px) brightness(0.55) saturate(1.1)",
              transform: "scale(1.1)",
              transformOrigin: "center",
            }}
          >
            <OffthreadVideo
              src={url}
              style={{ width: bgW, height: bgH, display: "block" }}
              muted
            />
          </div>
        ) : (
          <div
            style={{
              position: "absolute",
              inset: 0,
              background:
                "linear-gradient(180deg, #0b0d12 0%, #1a1f2e 100%)",
            }}
          />
        )}

        {/* Foreground: source band (cropped sides via inner offset) */}
        {url ? (
          <div
            style={{
              position: "absolute",
              left: 0,
              top: fgY,
              width: fgW,
              height: fgH,
              boxShadow: "0 0 80px rgba(0,0,0,0.55)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                position: "absolute",
                left: innerX,
                top: 0,
                width: innerW,
                height: innerH,
              }}
            >
              <OffthreadVideo
                src={url}
                style={{ width: innerW, height: innerH, display: "block" }}
                muted
              />
            </div>
          </div>
        ) : null}
      </>
    );
  } else {
    // TIGHT mode: crop+zoom following the action centroid.
    const url = videoSrc ? staticFile(videoSrc) : "";
    const scale = (outW * cam.zoom) / camera.base_crop_w;
    const tx = -cam.cx * scale + outW / 2;
    const ty = -cam.cy * scale + outH / 2;
    videoLayer = (
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: srcW,
          height: srcH,
          transformOrigin: "0 0",
          transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
          willChange: "transform",
        }}
      >
        {url ? (
          <OffthreadVideo
            src={url}
            style={{ width: srcW, height: srcH, display: "block" }}
            muted
          />
        ) : (
          <div style={{ width: srcW, height: srcH, background: "#222" }} />
        )}
      </div>
    );
  }

  const audioUrl = audioSrc ? staticFile(audioSrc) : "";

  return (
    <AbsoluteFill style={{ backgroundColor: "black", overflow: "hidden" }}>
      {videoLayer}

      {/* Audio track (mixed: voice + ducked music) */}
      {audioUrl ? <Audio src={audioUrl} /> : null}

      {/* Subtitles — positioned just below the gameplay band in wide mode,
          or in the lower third in tight mode. */}
      <Subtitles
        groups={subtitles}
        framing={isWide ? "wide" : "tight"}
        bandBottomFrac={wideBandBottomFrac}
      />

      {/* HP / Portrait HUD overlay (top corners). Skipped when baseVideo is
          provided, because the composite already contains the HUD. */}
      {!baseVideo && hud && hud.show !== false && videoSrc ? (
        <HudOverlay
          videoSrc={videoSrc}
          srcW={srcW}
          srcH={srcH}
          left={hud.left}
          right={hud.right}
          outSize={hud.outSize}
          outMargin={hud.outMargin}
          swapCorners={hud.swapCorners}
        />
      ) : null}

      {/* Optional hook title at the very start */}
      {title ? <TitleHook title={title} /> : null}
    </AbsoluteFill>
  );
};

import {
  OffthreadVideo,
  staticFile,
  useVideoConfig,
} from "remotion";

type HudRect = { x: number; y: number; w: number; h: number };
type Props = {
  videoSrc: string;
  srcW: number;
  srcH: number;
  left: HudRect;
  right: HudRect;
  outSize: number;
  outMargin: number;
  swapCorners: boolean;
};

/**
 * Renders the two HUD portraits in the corners by sampling the SOURCE video
 * twice (with different transforms cropping out only the portrait region).
 *
 * Trick: instead of using <canvas> per frame (slow under Remotion's headless
 * Chromium), we lay out an <OffthreadVideo> at full source resolution inside
 * a clipped/scaled wrapper. The wrapper does the cropping & scaling via CSS.
 * This way Remotion fetches the source frame once (cached) and we just paint
 * two views of it.
 */
export const HudOverlay: React.FC<Props> = ({
  videoSrc, srcW, srcH, left, right, outSize, outMargin, swapCorners,
}) => {
  const { width: outW } = useVideoConfig();

  // Pixel rects in source coords.
  const leftPx = {
    x: left.x * srcW, y: left.y * srcH, w: left.w * srcW, h: left.h * srcH,
  };
  const rightPx = {
    x: right.x * srcW, y: right.y * srcH, w: right.w * srcW, h: right.h * srcH,
  };

  // Where each crop ends up in the OUTPUT.
  // swapCorners=true → source-LEFT goes top-RIGHT, source-RIGHT goes top-LEFT.
  const leftCorner = swapCorners ? "right" : "left";
  const rightCorner = swapCorners ? "left" : "right";

  return (
    <>
      <PortraitSlot
        videoSrc={videoSrc}
        srcW={srcW} srcH={srcH}
        rect={leftPx}
        outSize={outSize}
        outMargin={outMargin}
        outW={outW}
        corner={leftCorner}
      />
      <PortraitSlot
        videoSrc={videoSrc}
        srcW={srcW} srcH={srcH}
        rect={rightPx}
        outSize={outSize}
        outMargin={outMargin}
        outW={outW}
        corner={rightCorner}
      />
    </>
  );
};

const PortraitSlot: React.FC<{
  videoSrc: string;
  srcW: number; srcH: number;
  rect: { x: number; y: number; w: number; h: number };
  outSize: number;
  outMargin: number;
  outW: number;
  corner: "left" | "right";
}> = ({ videoSrc, srcW, srcH, rect, outSize, outMargin, outW, corner }) => {
  // Square output box. The portrait crop is roughly square, but if it's not,
  // we letterbox (object-fit: contain feel) by just keeping its aspect.
  const boxW = outSize;
  const boxH = outSize;

  // Compute scale to map (rect.w x rect.h) to (boxW x boxH), keeping aspect.
  const scale = Math.min(boxW / rect.w, boxH / rect.h);
  const innerW = rect.w * scale;
  const innerH = rect.h * scale;
  const offsetX = (boxW - innerW) / 2;
  const offsetY = (boxH - innerH) / 2;

  // The video transform: scale by `scale` then translate so that
  // the rect's top-left lands at (offsetX, offsetY) inside the box.
  const tx = -rect.x * scale + offsetX;
  const ty = -rect.y * scale + offsetY;

  const left = corner === "left" ? outMargin : outW - boxW - outMargin;
  const top = outMargin;

  const url = videoSrc ? staticFile(videoSrc) : "";

  return (
    <div
      style={{
        position: "absolute",
        left,
        top,
        width: boxW,
        height: boxH,
        overflow: "hidden",
        // Soft shadow so it pops over busy backgrounds.
        filter: "drop-shadow(0 8px 16px rgba(0,0,0,0.7))",
      }}
    >
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
        ) : null}
      </div>
    </div>
  );
};

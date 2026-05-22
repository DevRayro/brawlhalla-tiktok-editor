import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { SubtitleGroup } from "./types";

/**
 * TikTok-style karaoke subtitles. The active word is enlarged + tinted.
 * Uppercase by default per the steering rules.
 */
export const Subtitles: React.FC<{
  groups: SubtitleGroup[];
  framing?: "wide" | "tight";
  /** Vertical center of the gameplay band (fraction of output height, 0-1).
   *  Used in wide mode to anchor subs just below the band. */
  bandBottomFrac?: number;
}> = ({ groups, framing = "tight", bandBottomFrac = 0.7 }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const t = frame / fps;

  let topY: number;
  if (framing === "wide") {
    topY = height * bandBottomFrac + 60;
  } else {
    topY = height * 0.62;
  }

  // Find the active group with a small lookahead so we can fade in/out.
  const FADE = 0.08; // seconds
  const active = groups.find((g) => t >= g.start - FADE && t <= g.end + FADE);
  if (!active) return null;

  // Per-group entrance pop.
  const enter = spring({
    fps,
    frame: frame - Math.round(active.start * fps),
    config: { damping: 14, stiffness: 200, mass: 0.6 },
  });

  const fadeIn = Math.max(0, Math.min(1, (t - (active.start - FADE)) / FADE));
  const fadeOut = Math.max(0, Math.min(1, (active.end + FADE - t) / FADE));
  const groupOpacity = Math.min(fadeIn, fadeOut);

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: topY,
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        padding: "0 80px",
        pointerEvents: "none",
        opacity: groupOpacity,
        transform: `scale(${0.92 + 0.08 * enter})`,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: "16px",
          maxWidth: "920px",
        }}
      >
        {active.words.map((w, i) => {
          const isActive = t >= w.start && t <= w.end;
          const isPast = t > w.end;
          return (
            <span
              key={i}
              style={{
                fontFamily:
                  "'Inter', 'Helvetica Neue', system-ui, -apple-system, sans-serif",
                fontWeight: 900,
                fontSize: isActive ? 92 : 80,
                lineHeight: 1,
                letterSpacing: "-0.01em",
                color: isActive ? "#FFE74C" : isPast ? "#E0E0E0" : "#FFFFFF",
                textTransform: "uppercase",
                textShadow:
                  "0 4px 0 #000, 0 -4px 0 #000, 4px 0 0 #000, -4px 0 0 #000," +
                  "3px 3px 0 #000, -3px 3px 0 #000, 3px -3px 0 #000, -3px -3px 0 #000," +
                  "0 8px 12px rgba(0,0,0,0.6)",
                transform: isActive ? "translateY(-6px) scale(1.06)" : "none",
                transition: "transform 60ms linear, color 60ms linear, font-size 60ms linear",
              }}
            >
              {w.text.replace(/^[\s,]+/, "").replace(/\s+$/, "")}
            </span>
          );
        })}
      </div>
    </div>
  );
};

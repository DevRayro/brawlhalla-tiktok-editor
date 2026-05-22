import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

/**
 * Optional hook title shown for the first ~2.5 seconds.
 */
export const TitleHook: React.FC<{ title: string }> = ({ title }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const durFrames = Math.round(2.5 * fps);
  if (frame > durFrames) return null;

  const enter = spring({ fps, frame, config: { damping: 14, stiffness: 180, mass: 0.6 } });
  const exit = interpolate(frame, [durFrames - 12, durFrames], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = enter * exit;

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: height * 0.18,
        display: "flex",
        justifyContent: "center",
        pointerEvents: "none",
        opacity,
      }}
    >
      <div
        style={{
          padding: "20px 40px",
          background: "rgba(0,0,0,0.55)",
          borderRadius: 24,
          backdropFilter: "blur(12px)",
          border: "3px solid #FFE74C",
        }}
      >
        <span
          style={{
            fontFamily: "'Inter', system-ui, sans-serif",
            fontWeight: 900,
            fontSize: 72,
            color: "#fff",
            textTransform: "uppercase",
            letterSpacing: "-0.01em",
            textShadow: "0 4px 0 #000, 0 8px 12px rgba(0,0,0,0.6)",
          }}
        >
          {title}
        </span>
      </div>
    </div>
  );
};

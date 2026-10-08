import React from "react";
import { AbsoluteFill, Img, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { FONT } from "./Overlay";

// 代码生成的动画片段：背景图（缓慢运镜）+ 图层（真实产品图 / 文字），每层可设入场动画与时段。
// 坐标单位为像素（1080x1920 坐标系），x/y 为图层中心；时间单位为毫秒。
export type MotionLayer = {
  src?: string; // 图片 data URL（透明底 PNG）
  text?: string;
  x: number;
  y: number;
  w?: number; // 图片宽度 px
  h?: number; // 图片高度 px（由 Python 端按比例算好）
  size?: number; // 字号 px
  color?: string;
  stroke?: string;
  bg?: string;
  strike?: boolean;
  startMs: number;
  endMs?: number;
  anim?: "pop" | "drop" | "slide_left" | "slide_right" | "slide_up" | "fade" | "none";
  float?: boolean; // 入场后轻微上下浮动
  rotate?: number;
  shadow?: boolean;
  z?: number;
  font?: string; // 字体族（默认思源黑体 Heavy）
};
export type MotionProps = {
  width: number;
  height: number;
  fps: number;
  durationMs: number;
  bg?: { src?: string; color?: string; motion?: "zoom_in" | "zoom_out" | "pan_up" | "pan_down" | "none" };
  layers: MotionLayer[];
};

const Layer: React.FC<{ l: MotionLayer; W: number; H: number }> = ({ l, W, H }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = (frame / fps) * 1000;
  if (t < l.startMs || (l.endMs !== undefined && t >= l.endMs)) return null;
  const f = frame - Math.round((l.startMs / 1000) * fps);
  const anim = l.anim ?? "pop";
  const enter = spring({ frame: f, fps, config: { damping: anim === "drop" ? 11 : 12, stiffness: 170 }, durationInFrames: 14 });
  let dx = 0, dy = 0, scale = 1, opacity = 1;
  if (anim === "pop") { scale = interpolate(enter, [0, 1], [0.3, 1]); opacity = Math.min(1, enter * 2); }
  if (anim === "drop") { dy = interpolate(enter, [0, 1], [-H * 0.35, 0]); opacity = Math.min(1, enter * 3); }
  if (anim === "slide_left") { dx = interpolate(enter, [0, 1], [W * 0.6, 0]); }
  if (anim === "slide_right") { dx = interpolate(enter, [0, 1], [-W * 0.6, 0]); }
  if (anim === "slide_up") { dy = interpolate(enter, [0, 1], [H * 0.3, 0]); opacity = Math.min(1, enter * 2); }
  if (anim === "fade") { opacity = interpolate(f, [0, 8], [0, 1], { extrapolateRight: "clamp" }); }
  if (l.float) dy += Math.sin(f / fps * 2.2) * 6;
  const outFade = l.endMs !== undefined ? interpolate(t, [l.endMs - 120, l.endMs], [1, 0], { extrapolateLeft: "clamp" }) : 1;
  const common: React.CSSProperties = {
    position: "absolute",
    left: l.x + dx,
    top: l.y + dy,
    transform: `translate(-50%, -50%) scale(${scale}) rotate(${l.rotate ?? 0}deg)`,
    opacity: opacity * outFade,
    zIndex: l.z ?? 0,
  };
  if (l.src) {
    return (
      <Img src={l.src} style={{ ...common, width: l.w, height: l.h,
        filter: l.shadow === false ? undefined : "drop-shadow(0 14px 22px rgba(0,0,0,0.28))" }} />
    );
  }
  const size = l.size ?? 96;
  return (
    <div style={{ ...common, whiteSpace: "pre", fontFamily: l.font ?? FONT, fontWeight: l.font && l.font !== FONT ? 400 : 900, fontSize: size, lineHeight: 1.15,
      color: l.color ?? "#FFFFFF", WebkitTextStroke: `${Math.round(size * 0.08)}px ${l.stroke ?? "#111111"}`,
      paintOrder: "stroke fill", background: l.bg ?? undefined, padding: l.bg ? `${size * 0.12}px ${size * 0.3}px` : 0,
      borderRadius: size * 0.22, textAlign: "center" }}>
      {l.text}
      {l.strike ? (
        <div style={{ position: "absolute", left: "4%", right: "4%", top: "52%", height: Math.max(6, size * 0.09),
          background: "#E8202A", transform: "rotate(-6deg)", borderRadius: 4 }} />
      ) : null}
    </div>
  );
};

export const Motion: React.FC<MotionProps> = ({ width, height, durationMs, bg, layers }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = Math.min(1, frame / Math.max(1, (durationMs / 1000) * fps));
  const m = bg?.motion ?? "zoom_in";
  const z = m === "zoom_in" ? 1 + 0.08 * p : m === "zoom_out" ? 1.08 - 0.08 * p : 1.08;
  const ty = m === "pan_up" ? 3 - 6 * p : m === "pan_down" ? -3 + 6 * p : 0;
  return (
    <AbsoluteFill style={{ background: bg?.color === "transparent" ? undefined : bg?.color ?? "#FFFFFF", overflow: "hidden" }}>
      {bg?.src ? (
        <Img src={bg.src} style={{ position: "absolute", width, height, objectFit: "cover",
          transform: `scale(${z}) translateY(${ty}%)` }} />
      ) : null}
      {[...layers].sort((a, b) => (a.z ?? 0) - (b.z ?? 0)).map((l, i) => <Layer key={i} l={l} W={width} H={height} />)}
    </AbsoluteFill>
  );
};

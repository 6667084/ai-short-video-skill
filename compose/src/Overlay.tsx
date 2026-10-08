import React from "react";
import { AbsoluteFill, Img, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

// 所有时间单位为毫秒；y 为元素中心线距画面顶部的像素
export type CaptionItem = { text: string; startMs: number; endMs: number; y: number; align?: "center" | "left" | "right" };
export type CardItem = {
  text: string;
  startMs: number;
  endMs: number;
  y: number;
  size?: number;
  color?: string;
  bg?: string | null;
  strike?: boolean;
  anim?: "pop" | "slide" | "none";
  stroke?: string | null;
  align?: "center" | "left" | "right";
  font?: string; // 字体族（默认思源黑体 Heavy；风格字体只有常规字重）
  image?: string; // 图片花字（透明底 PNG data URL），宽 = size，高 = imageH
  imageH?: number;
};
// 品牌模板（固定版式）：角标、底部机构条、开场封面（日期块 + 竖排标题）
export type BrandProps = {
  watermark?: { text: string; size: number; color: string; top: number; right: number };
  footer?: { lines: string[]; y: number; size: number; color: string; bg: string; border: string; borderWidth: number };
  cover?: {
    startMs: number;
    endMs: number;
    dateLines: string[];
    dateColor: string;
    dateSize: number;
    dateTop: number;
    dateLeft: number;
    titleColumns: { text: string; color: string }[];
    titleImage?: { src: string; x: number; y: number; w: number; h: number };
    date?: { x: number; y: number; align: "left" | "right" };
    titleSize: number;
    titleTop: number;
    titleRight: number;
    stroke: string;
  };
};
export type OverlayProps = {
  width: number;
  height: number;
  fps: number;
  durationMs: number;
  captions: CaptionItem[];
  cards: CardItem[];
  brand?: BrandProps;
  style?: {
    captionSize?: number;
    captionColor?: string;
    highlightColor?: string;
    strokeColor?: string;
    strokeRatio?: number;
    maxWidth?: number;
    captionFont?: string;
  };
};

export const FONT = "SourceHanSansSC";
// 数字价格自动高亮；【关键词】为手动高亮（括号不显示）
const HIGHLIGHT = /(【[^】]*】|\d+(?:\.\d+)?\s*(?:元|桶|折|%|万|块|L|升)*[!！]?)/g;

const msToFrame = (ms: number, fps: number) => Math.round((ms / 1000) * fps);

// 描边 + 柔和投影：paint-order 让描边画在填充下面，文字不会被“吃细”
const outlined = (stroke: string, w: number): React.CSSProperties => ({
  WebkitTextStroke: `${w}px ${stroke}`,
  paintOrder: "stroke fill",
  textShadow: `0 ${Math.round(w * 0.6)}px ${Math.round(w * 1.5)}px rgba(0,0,0,0.45)`,
});

const Highlighted: React.FC<{ text: string; color: string }> = ({ text, color }) => (
  <>
    {text.split(HIGHLIGHT).map((part, i) =>
      i % 2 === 1 ? (
        <span key={i} style={{ color, fontSize: "1.12em" }}>
          {part.replace(/[【】]/g, "")}
        </span>
      ) : (
        <React.Fragment key={i}>{part}</React.Fragment>
      ),
    )}
  </>
);

const Caption: React.FC<{ item: CaptionItem; props: OverlayProps }> = ({ item, props }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = props.style ?? {};
  const size = s.captionSize ?? 70;
  const enter = spring({ frame, fps, config: { damping: 14, stiffness: 220 }, durationInFrames: 6 });
  const scale = interpolate(enter, [0, 1], [0.86, 1]);
  const align = item.align ?? "center";
  // 与 compose.py 的 LEFT_X / RIGHT_X 保持一致：左对齐距左 6%，右对齐距右 16%（避开抖音按钮）
  const side: React.CSSProperties =
    align === "left" ? { justifyContent: "flex-start", paddingLeft: props.width * 0.06 }
    : align === "right" ? { justifyContent: "flex-end", paddingRight: props.width * 0.16 }
    : { justifyContent: "center" };
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: item.y,
        transform: `translateY(-50%) scale(${scale})`,
        transformOrigin: align === "left" ? "left center" : align === "right" ? "right center" : "center",
        display: "flex",
        ...side,
        opacity: enter,
      }}
    >
      <div
        style={{
          maxWidth: s.maxWidth ?? props.width * 0.86,
          fontFamily: s.captionFont ?? FONT,
          fontWeight: s.captionFont && s.captionFont !== FONT ? 400 : 900,
          fontSize: size,
          lineHeight: 1.18,
          letterSpacing: 1,
          color: s.captionColor ?? "#FFFFFF",
          textAlign: align === "center" ? "center" : align,
          whiteSpace: "pre-line",
          ...outlined(s.strokeColor ?? "#111111", Math.round(size * (s.strokeRatio ?? 0.13))),
        }}
      >
        <Highlighted text={item.text} color={s.highlightColor ?? "#FFE14D"} />
      </div>
    </div>
  );
};

const Card: React.FC<{ item: CardItem; props: OverlayProps }> = ({ item, props }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const size = item.size ?? 84;
  const anim = item.anim ?? "pop";
  const enter = spring({ frame, fps, config: { damping: 9, stiffness: 180 }, durationInFrames: 12 });
  const scale = anim === "pop" ? interpolate(enter, [0, 1], [0.4, 1]) : 1;
  const dy = anim === "slide" ? interpolate(enter, [0, 1], [-40, 0]) : 0;
  const strikeW = item.strike ? interpolate(frame, [6, 14], [0, 100], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 0;
  const lines = item.text.split("\n");
  const align = item.align ?? "center";
  const side: React.CSSProperties =
    align === "left" ? { justifyContent: "flex-start", paddingLeft: props.width * 0.06 }
    : align === "right" ? { justifyContent: "flex-end", paddingRight: props.width * 0.16 }
    : { justifyContent: "center" };
  if (item.image) {
    return (
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: item.y,
          transform: `translateY(calc(-50% + ${dy}px)) scale(${scale})`,
          transformOrigin: "center",
          display: "flex",
          ...side,
          opacity: anim === "none" ? 1 : Math.min(1, enter * 1.5),
        }}
      >
        <Img src={item.image} style={{ width: size, height: item.imageH, filter: "drop-shadow(0 8px 18px rgba(0,0,0,0.35))" }} />
      </div>
    );
  }
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: item.y,
        transform: `translateY(calc(-50% + ${dy}px)) scale(${scale})`,
        transformOrigin: align === "left" ? "left center" : align === "right" ? "right center" : "center",
        display: "flex",
        ...side,
        opacity: anim === "none" ? 1 : Math.min(1, enter * 1.5),
      }}
    >
      <div
        style={{
          position: "relative",
          maxWidth: props.width * 0.92,
          padding: item.bg ? `${Math.round(size * 0.16)}px ${Math.round(size * 0.42)}px` : 0,
          borderRadius: Math.round(size * 0.28),
          background: item.bg ?? "transparent",
          boxShadow: item.bg ? "0 10px 30px rgba(0,0,0,0.30)" : undefined,
          fontFamily: item.font ?? FONT,
          fontWeight: item.font && item.font !== FONT ? 400 : 900,
          fontSize: size,
          lineHeight: 1.15,
          color: item.color ?? "#FFE14D",
          textAlign: "center",
          whiteSpace: "pre",
          ...(item.stroke === null ? {} : outlined(item.stroke ?? "#111111", Math.max(4, Math.round(size * 0.09)))),
        }}
      >
        {lines.map((l, i) => (
          <div key={i} style={i > 0 ? { fontSize: "0.62em", marginTop: "0.18em" } : undefined}>
            {l}
          </div>
        ))}
        {item.strike ? (
          <div
            style={{
              position: "absolute",
              left: "4%",
              top: "50%",
              height: Math.max(6, Math.round(size * 0.1)),
              width: `${strikeW * 0.92}%`,
              background: "#FF2D2D",
              borderRadius: 4,
              transform: "translateY(-50%) rotate(-4deg)",
              boxShadow: "0 0 0 2px rgba(0,0,0,0.25)",
            }}
          />
        ) : null}
      </div>
    </div>
  );
};

const Watermark: React.FC<{ w: NonNullable<BrandProps["watermark"]>; props: OverlayProps }> = ({ w, props }) => (
  <div
    style={{
      position: "absolute",
      top: props.height * w.top,
      right: props.width * w.right,
      fontFamily: FONT,
      fontWeight: 700,
      fontSize: w.size,
      color: w.color,
      textShadow: "0 0 3px rgba(0,0,0,0.55), 0 1px 6px rgba(0,0,0,0.45)",
    }}
  >
    {w.text}
  </div>
);

const Footer: React.FC<{ f: NonNullable<BrandProps["footer"]>; props: OverlayProps }> = ({ f, props }) => (
  <div style={{ position: "absolute", left: 0, right: 0, top: props.height * f.y, transform: "translateY(-50%)", display: "flex", justifyContent: "center" }}>
    <div
      style={{
        background: f.bg,
        border: `${f.borderWidth}px solid ${f.border}`,
        borderRadius: Math.round(f.size * 0.7),
        padding: `${Math.round(f.size * 0.32)}px ${Math.round(f.size * 1.1)}px`,
        fontFamily: FONT,
        fontWeight: 900,
        fontSize: f.size,
        lineHeight: 1.32,
        color: f.color,
        textAlign: "center",
        whiteSpace: "pre",
        boxShadow: "0 6px 18px rgba(0,0,0,0.18)",
      }}
    >
      {f.lines.join("\n")}
    </div>
  </div>
);

const Cover: React.FC<{ c: NonNullable<BrandProps["cover"]>; props: OverlayProps }> = ({ c, props }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = msToFrame(c.endMs - c.startMs, fps);
  const enter = spring({ frame, fps, config: { damping: 12, stiffness: 160 }, durationInFrames: 14 });
  const exit = interpolate(frame, [total - 8, total], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const sw = (size: number) => Math.max(5, Math.round(size * 0.12));
  return (
    <AbsoluteFill style={{ opacity: Math.min(1, enter * 1.4) * exit }}>
      <div
        style={{
          position: "absolute",
          top: c.date ? c.date.y : props.height * c.dateTop,
          left: c.date ? c.date.x : props.width * c.dateLeft,
          textAlign: c.date?.align ?? "left",
          fontFamily: FONT,
          fontWeight: 900,
          color: c.dateColor,
          lineHeight: 1.15,
        }}
      >
        {c.dateLines.map((l, i) => (
          <div key={i} style={{ fontSize: i === 0 ? c.dateSize : Math.round(c.dateSize * 0.68), ...outlined(c.stroke, sw(c.dateSize * (i === 0 ? 1 : 0.68))) }}>
            {l}
          </div>
        ))}
      </div>
      {c.titleImage ? (
        <Img
          src={c.titleImage.src}
          style={{
            position: "absolute",
            left: c.titleImage.x,
            top: c.titleImage.y,
            width: c.titleImage.w,
            height: c.titleImage.h,
            transform: `scale(${interpolate(enter, [0, 1], [0.7, 1])})`,
            transformOrigin: "center top",
          }}
        />
      ) : (
      <div
        style={{
          position: "absolute",
          top: props.height * c.titleTop,
          right: props.width * c.titleRight,
          display: "flex",
          flexDirection: "row-reverse",
          alignItems: "flex-start",
          gap: Math.round(c.titleSize * 0.12),
          transform: `scale(${interpolate(enter, [0, 1], [0.7, 1])})`,
          transformOrigin: "top right",
        }}
      >
        {c.titleColumns.map((col, i) => (
          <div
            key={i}
            style={{
              writingMode: "vertical-rl",
              textOrientation: "upright",
              fontFamily: FONT,
              fontWeight: 900,
              fontSize: c.titleSize,
              lineHeight: 1.0,
              letterSpacing: Math.round(c.titleSize * 0.02),
              color: col.color,
              marginTop: i % 2 === 1 ? Math.round(c.titleSize * 0.6) : 0,
              ...outlined(c.stroke, sw(c.titleSize)),
            }}
          >
            {col.text}
          </div>
        ))}
      </div>
      )}
    </AbsoluteFill>
  );
};

export const Overlay: React.FC<OverlayProps> = (props) => {
  const { fps } = props;
  const b = props.brand;
  return (
    <AbsoluteFill>
      {b?.cover ? (
        <Sequence from={msToFrame(b.cover.startMs, fps)} durationInFrames={Math.max(1, msToFrame(b.cover.endMs - b.cover.startMs, fps))} layout="none">
          <Cover c={b.cover} props={props} />
        </Sequence>
      ) : null}
      {b?.footer ? <Footer f={b.footer} props={props} /> : null}
      {b?.watermark ? <Watermark w={b.watermark} props={props} /> : null}
      {props.cards.map((c, i) => (
        <Sequence key={`c${i}`} from={msToFrame(c.startMs, fps)} durationInFrames={Math.max(1, msToFrame(c.endMs - c.startMs, fps))} layout="none">
          <Card item={c} props={props} />
        </Sequence>
      ))}
      {props.captions.map((c, i) => (
        <Sequence key={`s${i}`} from={msToFrame(c.startMs, fps)} durationInFrames={Math.max(1, msToFrame(c.endMs - c.startMs, fps))} layout="none">
          <Caption item={c} props={props} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};

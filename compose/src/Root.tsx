import React from "react";
import { Composition, staticFile } from "remotion";
import { loadFont } from "@remotion/fonts";
import { FONT, Overlay, OverlayProps } from "./Overlay";
import { Motion, MotionProps } from "./Motion";

loadFont({ family: FONT, url: staticFile("fonts/SourceHanSansSC-Heavy.otf"), weight: "900" });
loadFont({ family: FONT, url: staticFile("fonts/SourceHanSansSC-Bold.otf"), weight: "700" });
// 风格字体（OFL 开源，可商用）：按每条视频的视觉风格选用
for (const [family, file] of [
  ["ZCOOLKuaiLe", "ZCOOLKuaiLe-Regular.ttf"],        // 站酷快乐体：卡通、综艺
  ["ZCOOLQingKeHuangYou", "ZCOOLQingKeHuangYou-Regular.ttf"], // 站酷庆科黄油体：粗圆、促销
  ["MaShanZheng", "MaShanZheng-Regular.ttf"],        // 马善政毛笔楷书：国潮、茶
  ["LongCang", "LongCang-Regular.ttf"],              // 龙藏体：手写、手账、温情
  ["ZCOOLXiaoWei", "ZCOOLXiaoWei-Regular.ttf"],      // 站酷小薇：文艺宋体
]) {
  loadFont({ family, url: staticFile(`fonts/${file}`), weight: "400" });
}

const defaults: OverlayProps = {
  width: 1080,
  height: 1920,
  fps: 30,
  durationMs: 5000,
  captions: [{ text: "现在只要99元！", startMs: 0, endMs: 2500, y: 1400 }],
  cards: [
    { text: "10桶 160元", startMs: 0, endMs: 2500, y: 230, size: 96, color: "#FFFFFF", bg: "rgba(0,0,0,0.62)", strike: true },
    { text: "10桶 99元\n平均每桶9.9元", startMs: 2500, endMs: 5000, y: 260, size: 150, color: "#FFE14D", bg: "#E8202A" },
  ],
};

const motionDefaults: MotionProps = {
  width: 1080,
  height: 1920,
  fps: 30,
  durationMs: 3000,
  bg: { color: "#DDEEFF", motion: "zoom_in" },
  layers: [{ text: "10桶99元", x: 540, y: 960, size: 150, color: "#FFE14D", startMs: 300, anim: "pop" }],
};

const meta = ({ props }: { props: { width: number; height: number; fps: number; durationMs: number } }) => ({
  width: props.width,
  height: props.height,
  fps: props.fps,
  durationInFrames: Math.max(1, Math.ceil((props.durationMs / 1000) * props.fps)),
});

export const RemotionRoot: React.FC = () => (
  <>
  <Composition
    id="Motion"
    component={Motion}
    defaultProps={motionDefaults}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={90}
    calculateMetadata={meta}
  />
  <Composition
    id="Overlay"
    component={Overlay}
    defaultProps={defaults}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={150}
    calculateMetadata={({ props }) => ({
      width: props.width,
      height: props.height,
      fps: props.fps,
      durationInFrames: Math.max(1, Math.ceil((props.durationMs / 1000) * props.fps)),
    })}
  />
  </>
);

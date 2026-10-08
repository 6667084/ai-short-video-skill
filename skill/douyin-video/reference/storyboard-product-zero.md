# 产品零出镜分镜（核心方法）

## 目录
- 为什么
- 三个真实像素模块
- 提示词里的道具禁忌
- 算账板（motion_clip）
- 产品揭幕（透明 motion_clip + broll_insert 叠加）
- 片尾定版
- 卡点：时间从哪来
- 重剪已有成片
- 常见错误

## 为什么

视频模型把文字当纹理画：大 Logo 偶尔对，小字必然乱码（实测“饮用纯净水”被画成“鲜焗凿泉 非伏冰净泉水”）；数量是弱项（台词说 10 桶，玄关堆了 20+ 桶）；物理状态会照抄参考图（饮水机上的桶正放）。这些问题在参考图模式、首帧模式、480p+超分、原生 1080p 下都出现过。

事后补救（裁切、模糊色块、贴片）画面廉价，用户一眼能看出来。所以**在分镜阶段就决定：AI 只拍人和生活，产品只以真实像素出现**。

对比图：仓库 `docs/images/01_label_garble_vs_real_pixels.jpg`。

## 三个真实像素模块

| 模块 | 方式 | 时长 | 盖住哪段 |
|---|---|---|---|
| 算账板 | `motion_clip` 整画面，`broll_insert` replace | 3–5.5 秒 | 人物算账的口播（“原价…现在…”） |
| 产品揭幕 | `motion_clip(transparent=True)` → `.mov`，`broll_insert` overlay | 2–3.5 秒 | 人物做展示手势的镜头（“16.8 升，大品牌…”） |
| 片尾定版 | `motion_clip` 整画面，`video_compose` 拼接在最后 | 2.5–3 秒 | 主片之后（无口播，BGM 收尾） |

口播在插入期间不断（broll_insert 只换画面、保留主片音轨），观感像剪辑师做的 J-cut。

## 提示词里的道具禁忌

写进 Seedance 提示词（见 prompting.md）：

```
【道具禁忌（非常重要）】
全片不出现任何桶装水、水桶、饮水机、矿泉水瓶、饮料瓶或任何带标签、带商标的包装。
喝水和装水只用透明无字玻璃杯、透明无字玻璃凉水壶、透明玻璃茶壶和无字电水壶。不出现手机屏幕。
```

并给揭幕镜头留位置：

```
19.5-22.5秒｜S9｜客厅中景，镜头轻微推近：妈妈站在画面左侧三分之一处，面向镜头，右手掌心向上伸向画面右侧，
做“隆重介绍”的展示手势，表情自信。画面右侧一半是干净明亮、略微虚化的浅色墙面，空无一物，没有家具和人（后期会在这里放置内容）。
｜妈妈出镜（对口型）：“十六点八升，大品牌，喝着放心。”
```

> 换成别的品类同理：把“桶装水/饮水机”换成你的产品及其载体（洗发水瓶、零食袋、酒瓶、手机……）。

## 算账板（motion_clip）

素材：风格背景（`image_generate`，竖版，中间留空，**画面无任何文字/数字/产品**）、透明底产品图、本条价格花字。

图层写法（坐标为 0–1 比例，`start` 为片段内秒数）：

```python
DK = "#9C3D0B"   # 本条描边色
layers = [
  {"text": "原价 16元/桶 × 10桶", "x": .5, "y": .11, "size": 92, "color": "#FFF", "stroke": DK, "font": "快乐体",
   "bg": "rgba(156,61,11,0.85)", "start": .05, "anim": "pop", "z": 6},
  {"text": "= 160元", "x": .5, "y": .19, "size": 120, "color": "#FFF", "stroke": DK, "font": "快乐体",
   "bg": "rgba(156,61,11,0.85)", "start": .3, "end": .9, "anim": "pop", "z": 6},
  {"text": "= 160元", ...同上..., "strike": True, "start": .9, "anim": "none", "z": 6},     # 说到“现在”时划掉
  {"image": "<本条_花字_10桶99元.png>", "x": .5, "y": .36, "w": .88, "start": 1.55, "anim": "pop", "z": 9},  # 说到“九十九”
]
# 10 桶：两排各 5 个，逐个落下（后排小、前排大，有纵深）
for i, x in enumerate([.14, .32, .5, .68, .86]):
    layers.append({"image": PRODUCT, "x": x, "y": .62, "w": .15, "start": .25 + i*.07, "anim": "drop", "z": 2})
for i, x in enumerate([.12, .31, .5, .69, .88]):
    layers.append({"image": PRODUCT, "x": x, "y": .78, "w": .17, "start": .55 + i*.07, "anim": "drop", "z": 3})
motion_clip(job, name="篇名_算账板", duration=3.35, layers=layers, background="<本条_算账板背景.png>")
```

要点：
- **数量与口播一致**：说 10 桶就 10 个图层。
- 文字放在**半透明底条**上（背景有条纹/纹理时不加底条会看不清）。
- 手写/毛笔等细字体字号放大到 ≥90；黑体、快乐体最稳。
- 花字不要压住划线文字，先排好纵向位置（0.11 / 0.19 / 0.36 / 0.62 / 0.78）。

风格示例：手账页逐行记账、综艺警报放射底、宣纸竖排 + 算盘、蓝天水面、晨光暖橙（`docs/images/03_style_matrix_calc_boards.jpg`）。

## 产品揭幕（透明 motion_clip + broll_insert 叠加）

```python
R = [
  {"image": "<本条_揭幕光效.png>", "x": .79, "y": .27, "w": .5, "start": 0, "anim": "fade", "shadow": False, "z": 1},
  {"image": PRODUCT, "x": .79, "y": .27, "w": .23, "start": .15, "anim": "pop", "bob": True, "z": 2},
]
motion_clip(job, name="篇名_产品揭幕", duration=2.15, layers=R, transparent=True)   # → .mov（ProRes 4444 带 alpha）
```

- `x, y` 对准 AI 镜头里人物掌心所指的空位（先 `contact_sheet` 或抽帧看位置），产品不要压到脸。
- 光效风格随本条：手绘涂鸦圈、漫画爆炸星、泡泡光晕、水花、水墨圈、晨光（`docs/images/04_product_reveal_overlays.jpg`）。
- 光效贴图要求“真正透明背景（alpha 透明，不要画棋盘格）”；`image_generate` 返回 `warning` 说不透明时必须重生成。
- 揭幕是**叠加**：人物照常说话、动作，比整画面切到产品图自然得多（前后对比 `docs/images/05_reveal_before_after.jpg`）。

## 片尾定版

```python
E = [
  {"text": "家里喝水多的\n点下方 马上囤！", "x": .5, "y": .14, "size": 86, "color": "#FFF", "stroke": DK, "font": "快乐体", "bg": "#E8711E", "start": .1, "anim": "pop", "z": 7},
  {"image": PRODUCT, "x": .5, "y": .45, "w": .31, "start": .15, "anim": "pop", "bob": True, "z": 2},
  {"image": "<本条_花字_10桶99元.png>", "x": .5, "y": .745, "w": .88, "start": .55, "anim": "pop", "z": 5},
  {"text": "适用区域以团购页为准", "x": .5, "y": .865, "size": 44, "color": DK, "stroke": "#FFF6EA", "start": .9, "anim": "fade", "shadow": False, "z": 6},
]
motion_clip(job, name="篇名_片尾", duration=2.6, layers=E, background="<本条_片尾背景.png>")
```

## 卡点：时间从哪来

1. 主片下载后先 `subtitles_align(video_path, lines)`，拿到每句 `start/end`（文字 100% 来自脚本）。
2. 算账板时长 = 该段口播时长；图层 `start` = 关键词在句内的偏移（“一百六”出 160，“现在”划掉，“九十九”弹出花字）。
3. `broll_insert(base=主片, inserts=[{"video": 算账板.mp4, "start": 11.88, "duration": 3.33}, {"video": 揭幕.mov, "start": 22.3}], job, name="篇名_主片_插入")`。
4. 字幕用同一份 cues 进 `video_compose`；片尾作为第二段视频拼接。

## 重剪已有成片（不重新生成整条）

当旧成片里有 AI 画的产品（乱码、数量错）时：
1. 找回**原始主片**（reAPI 产物可用 `task_status(task_id)` 免费重新下载；即梦在画布里可再次下载）。
2. 按台词时间把有产品的镜头换成算账板 / 产品展示；结尾若必须有人物，用**最短时长补拍**（例：6 秒 720p = 120 积分），只拍人物 + 展示手势，再叠揭幕。
3. 补拍片段保留**原主片的口播音轨**（broll_insert 只换画面），声音前后一致。
4. 补拍分辨率低于原片时会自动放大到成片规格，手机上看不出差别。

时间轴示意：`docs/images/09_recut_timeline.png`。

## 常见错误

| 症状 | 原因 | 处理 |
|---|---|---|
| 算账板里只有 1 桶 | 偷懒用了单张产品图 | 10 个图层，两排 |
| 产品“贴”在实拍场景地板上很假 | 透视、光影、接地阴影都不对 | 不合成进实景，改揭幕叠加或图形化算账板 |
| 揭幕产品压住人脸 | 空位方向没对准 | 抽帧确认手势方向；提示词写清“站左侧 1/3、右侧留空” |
| 贴纸带白底/棋盘格 | 生图模型没给 alpha | 看 `warning`，重生成；不要用“抠白”凑合 |
| 插入后口播断了 | 用了拼接而不是 broll_insert | broll_insert 只换画面 |

# Case study: 10 Douyin promo videos for community group-buy bottled water

> 中文：[case-study.md](case-study.md)

## Background

- **Product**: 16.8-litre purified-water jugs, sold via community group-buying. Regular price ¥16 per jug; promo price 10 jugs for ¥99 (¥9.9 each, saving ¥61).
- **Goal**: ten ≤30 s Douyin / WeChat Channels videos. Each delivers:
  - a final cut with captions, stickers, price animation and music;
  - a cover image;
  - a publish description.
- **Constraints**:
  - Any spend needs prior approval.
  - The client's draft script was "direction only, do not reuse".
  - Brand, size and price must never be misspelled on screen.
- **Execution**: an AI agent (Claude Code) drove the MCP tools in this repo. The human reviewed scripts and images, approved quotes and signed off each video.

## Iterations

| Stage | Approach | Outcome | Takeaway |
|---|---|---|---|
| ① Reference mode | reAPI Seedance 2.5 with 4 refs (incl. product) and a blocking video; 480p draft + cloud upscale; also a 1080p version | Good acting and pacing. **Small label text garbled.** Jug sat **upright** on the dispenser (copied from the ref). Cost $30. | Don't let the model draw the product |
| ② First-frame mode | Real product composited into frame 0, Seedance 2.0 Mini 720p | **Small text redrawn already at frame 0.** Mini actors looked fake and soft. | First frame doesn't save small text; no Mini for ad masters |
| ③ Native 1080p | No product ref, Seedance 2.5 1080p 28 s ($18) | Model invented off-brand jugs, **stacked 20+**, labels garbled | Any "jug" in the prompt gets drawn wrong |
| ④ **Product-zero** | AI films only people; all product shots are real-pixel `motion_clip`, laid over the matching lines with `broll_insert` | 16/16 lines matched, QA passed, **labels 100 % correct**; $6.7 | The method works |
| ⑤ Channel switch | Jimeng Canvas CLI, same model; 11 s test at 480p and at 720p | Same quality as reAPI at about half the price; realistic AI cast refs not blocked | Jimeng becomes the default video channel |
| ⑥ 480p vs 720p | Same theme at 480p (upscaled) and native 720p | 720p close-ups show clearly better lashes and skin texture | Native 720p for ad masters |
| ⑦ Client feedback | Five issues (listed below) | — | Five fixes (listed below) |
| ⑧ Series production | Distinct style per video: scrapbook, game show, neo-Chinese ink, creamy, blue sky, morning glow… | 520–600 credits each, all passed first time | The style matrix is reusable |
| ⑨ Re-cut an old video | Old cut hid garbling with crops and blur and looked crude. Recovered the 1080p master, re-cut it, re-shot only the last 6 s. | 120 credits, QA passed | Recover the master first; re-shoot only the minimum |

Stage ⑦ in detail:

| Client feedback | Fix |
|---|---|
| The opening title covered the actors | Title goes on the cover only |
| Product composited into a fake scene looked off | Use a product-reveal overlay instead |
| Actors looked emotionless | Generate reference images with the character's main emotion |
| The calc board showed 1 jug instead of 10 | Exactly 10 product layers |
| An in-video AI badge isn't needed | Badge removed |

## Per-video results

| Video | Style | Channel · spec | Generation cost | Length | Captions | QA |
|---|---|---|---|---|---|---|
| Group leader | default | reAPI · 720p · 25 s | $6.67 + $0.066 music | 27.6 s | 16/16 | warn¹ |
| Daily life (re-cut) | morning glow | reAPI 1080p master + 6 s Jimeng 720p pickup | 120 credits (pickup) | 30.7 s | 15/15 | pass |
| Husband overpaid | default | Jimeng · 480p→720p · 24 s | 288 credits + $0.066 | 26.6 s | 14/14 | warn¹ |
| Husband, you overpaid | blue sky | Jimeng · native 720p · 26 s | 520 credits | 28.6 s | 18/18 | pass² |
| A day of water | scrapbook | Jimeng · native 720p · 26 s | 520 credits + $0.066 | 28.6 s | all* | pass |
| 7 a.m. battlefield | countdown game show | Jimeng · native 720p · 26 s | 520 credits + $0.066 | 28.6 s | 16/16 | warn¹ |
| Mom & family (remake) | creamy | Jimeng · native 720p · 30 s | 600 credits + $0.066 | 32.7 s | 19/19 | pass² |
| Tea lovers | neo-Chinese ink | Jimeng · native 720p · 26 s | 520 credits + $0.066 | 28.7 s | 15/15 | pass |

All 8 finals were re-checked with `qa_check` on 2026-10-08 before publishing: 0 failures.
\* The model dropped the final call to action; it was restored with a sticker and the end card.
¹ Warning "caption/sticker near facial features": in extreme close-ups the caption sits near the chin; a human confirmed it does not cover the eyes or mouth.
² Passed at delivery. The files were renamed afterwards, so the re-check could not find the matching layout sidecar and could not recompute the face-overlap item; all other checks passed.

**Jimeng total**: 3,088 credits (≈ ¥130) for 6 masters and 1 pickup, plus 388 credits of earlier comparison tests.
**Post-production per video** (images, animation, captions, compositing, QA): ¥0.

## Timeline of one video (native 720p)

| Step | Time |
|---|---|
| Images: 2–3 cast refs + 7 packaging assets (3 in parallel) | 3–6 min |
| Jimeng 26 s at 720p | 5–11 min |
| Suno music | <1 min |
| Caption alignment | <1 min |
| Calc board / reveal / end card render | 2–4 min |
| Compose + QA | 2–3 min |
| Cover + publish copy | <1 min |

## If we did it again

1. Storyboard product-zero from day one. That alone saves the ~$50 spent in stages ①–③.
2. Settle channel and resolution with an 11 s test clip (it cost 388 credits) before committing to the series spec.
3. Give cast reference images emotion from the start.
4. Agree with the client up front: titles go on the cover only, and AI labels are ticked on the platform.
5. Fix all ten styles before production, so shared assets don't need to be redone midway.

Figures in `images/`:

- 01 garbled labels vs real pixels;
- 02 resolution comparison;
- 03 style matrix;
- 04 product reveals;
- 05 reveal before/after;
- 06 cover wall;
- 07 QA review image;
- 08 cost per video;
- 09 re-cut timeline.

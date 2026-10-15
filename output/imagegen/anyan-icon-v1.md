# 安言软件图标 v1

设计：沿用现有深绿、玉绿品牌色，以白色的对话气泡与柔和盾形融合，表达沟通、理解与守护。内部两道横向留白呼应“言”。

生成方式：内置 image_gen 工具。

图稿：`anyan-icon-v1.png`。本次交付设计图稿，尚未替换 Android 启动器资源。

## Android 落地记录（2026-10-15）

以上为原始设计说明，生成提示词和 PNG 均保留。当前 Android 已采用紫藤、墨色、月白，原说明中的深绿、玉绿属于旧配色参考。

实际 PNG 为 1254 × 1254 的 RGBA 图片，背景带半透明像素，不适合直接充当自适应图标前景或单色图层。Android 采用其“对话气泡与守护轮廓”的概念，重建为透明背景的矢量标记，复用到启动器、主题图标及应用内品牌位置；不打包原始 PNG。

具体实现和验证见 [设计稿与图标落地记录](../../docs/设计稿与图标落地-2026-10-15.md)。

## 原始生成提示词

```text
Use case: logo-brand
Asset type: standalone Android application icon, one square 1024x1024 image.
Primary request: Design a polished original app icon for the Chinese software 安言, a conversational banking assistant. The brand conveys calm trust, understanding people's requests, and carefully authorized actions.
Subject: One bold, beautifully balanced ivory-white emblem fusing a rounded conversation bubble with the reassuring silhouette of a soft protective shield. Integrate these into ONE unified distinctive contour rather than stacking separate clip-art objects. The bubble has a gently peaked shelter-like upper edge, a soft shield-like lower curve, and a short understated speech tail. Inside, two generously spaced, substantial rounded horizontal negative-space strokes subtly evoke the Chinese character 言 and calm communication. Strive for very simple, memorable geometry, superb optical balance, generous negative space, and immediate legibility at small app-icon sizes.
Style/medium: Refined flat vector-like brand design, crisp edges, professional and welcoming financial software, restrained subtle tonal depth only.
Scene/backdrop: Full-bleed opaque square background in the existing product's forest/jade green palette, #124C3C and #176B52 with a very subtle smooth shift toward #388269 near the upper right. Background fills all four corners; no external margin, no rounded-square frame drawn inside the canvas. Center the white emblem with ample breathing room, keep its entire silhouette inside the central 64 percent of the image so launcher masks can crop the background safely.
Constraints: Deliver exactly ONE finished app icon asset, front-facing, no design board, no device mockup, no alternate versions. No lettering, no wordmark, no bank buildings, no coins, no currency marks, no padlocks, no robot face, no sparkles, no elaborate gradients, no cast shadow, no texture, no watermark. Strong silhouette and clean composition are essential.
```

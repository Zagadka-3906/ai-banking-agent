# AnYan 标志使用规范

版本 `2026-11-02-graphite-gold`。本目录是“开口 A”唯一品牌原稿来源，保留标准、小尺寸、反白及平台变体。字标为几何路径，不依赖商业字体。

![横向主标](masters/anyan-horizontal.svg)

## 选择文件

| 用途 | 原稿 | 建议最小尺寸 |
| --- | --- | --- |
| 页眉、文档 | [横向组合](masters/anyan-horizontal.svg) | 宽 180 px |
| 封面、居中展示 | [竖向组合](masters/anyan-stacked.svg) | 宽 120 px |
| 导航、聊天头像 | [标准符号](masters/anyan-symbol.svg) | 48 px |
| 小图标 | [小尺寸符号](masters/anyan-symbol-small.svg) | 16–32 px |
| 独立名称 | [路径字标](masters/anyan-wordmark.svg) | 宽 100 px |
| 深色背景 | 对应 `*-reversed.svg` 或 `*-on-dark.svg` | 与对应组合相同 |
| 应用图标 | [圆角预览](masters/anyan-app-icon.svg)、[方形原稿](masters/anyan-app-icon-square.svg) | 按平台导出 |
| 浏览器图标 | [favicon](masters/anyan-favicon.svg) | 16 / 32 / 48 px |

最小尺寸为屏幕使用建议，印刷需按纸张、工艺和观看距离打样。可从符号高 6 mm、横向组合宽 28 mm 起试，不承诺所有介质同样清晰。

## 比例与留白

以标准符号竖笔画宽度为 x，外轮廓四周至少保留 0.5x，空间允许时保留 1x。组合整体等比缩放，不移动符号与字标的相对位置，不用输入文字重建字标。

标准符号使用 256 单位画布，竖笔画 48、横梁 40、内侧开口 32 单位，顶部相对几何中心上移 4 单位。小尺寸版横梁改为 32 并调整顶拱；反白版竖笔画 46、横梁 38，补偿深底视觉增重。请使用对应原稿，不从标准版随意改笔画。

## 色彩与可读性

| 颜色 | HEX | RGB | CMYK 数学近似 |
| --- | --- | --- | --- |
| 暖石墨 | `#30312D` | 48, 49, 45 | 2, 0, 8, 81 |
| 米白 | `#F6F3EB` | 246, 243, 235 | 0, 1, 4, 4 |
| 浅金 | `#B39A68` | 179, 154, 104 | 0, 14, 42, 30 |
| 白色 | `#FFFFFF` | 255, 255, 255 | 0, 0, 0, 0 |

机器数据见[配色文件](palette.json)。静态对比度约为：石墨 / 米白 11.82:1，白 / 石墨 13.10:1，浅金 / 石墨 4.83:1，浅金 / 米白 2.45:1。浅金在浅底只作装饰，不承担正文与功能图标。CMYK 为数学近似，不是指定 ICC 下的印刷校色结果，也不指定未经实物确认的 Pantone 编号。

保持 AnYan 大小写；不拉伸、旋转、重排，不加描边、渐变、发光或阴影。风险红黄绿、涨跌与错误色用于业务状态，不改变 Logo，也不把 Logo 当作支付成功标识。

## 平台资源

Android 原稿含 VectorDrawable、自适应前景和单色图层；108 单位画布上的主体位于中央 66 单位安全圆。替换时保留原应用的业务颜色，检查圆形、方圆形与主题图标裁切。

Web 导出含 favicon、Apple Touch、192 / 512 px、maskable 和 manifest。HTML 片段默认网站根路径，部署到 `/panel/` 时核对实际引用。图标与 manifest 不自动赋予离线 PWA 能力。

## 重新导出与核对

```powershell
.\.venv\Scripts\python.exe scripts/build_anyan_brand.py --output-dir build/anyan-brand
```

脚本依赖本机 logo-design 技能及受支持 SVG 渲染器，输出目录须不存在或为空，不清空已有文件。输出含矢量、PNG、平台图标、规范和校验清单；展示场景是品牌摆放示意，不能用来证明真实银行卡或产品功能。

可加 `--android-sdk <目录>` 用 aapt2 检查 Android 资源；不安装 App。安装 Pillow 时可检查 PNG 解码和透明通道。导出结果见生成的 `validation.json`，目视检查不等于外部用户研究或商标可注册性判断。

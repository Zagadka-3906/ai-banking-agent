# 安言 AnYan 品牌资源

安言使用“开口 A”标志：圆拱构成首字母 A，横梁右侧留出开口。英文拼写固定为 **AnYan**。本目录只保留一套定稿及其尺寸、明暗和平台变体。

![AnYan 横向主标](final/masters/anyan-horizontal.svg)

| 用途 | 入口 |
| --- | --- |
| SVG 原稿、尺寸与留白 | [最终资源与使用规范](final/README.md) |
| 软件中的实际使用 | [Android 与运维台接入](integration.md) |
| 机器可读色值 | [palette.json](final/palette.json) |
| 重新导出 | `scripts/build_anyan_brand.py` |

暖石墨 `#30312D` 用于浅底主标和文字，米白 `#F6F3EB` 用于背景，浅金 `#B39A68` 用于深底符号和点缀，白色用于反白。业务风险颜色不替代品牌色。

`final/` 包含符号、横竖组合、字标、小尺寸版、反白及 Android / Web 原稿；它们是同一标志的使用变体。历史探索从 Git 追溯，不重新加入大批试色和重复预览。源码更新后仍需重新构建 App 或部署网页，资源文件存在不表示远端已经更新。

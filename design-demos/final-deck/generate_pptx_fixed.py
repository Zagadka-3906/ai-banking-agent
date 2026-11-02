#!/usr/bin/env python3
"""生成答辩 PPTX，嵌入 PNG 截图"""
import os
from pptx import Presentation
from pptx.util import Inches

# 确保在正确目录
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

slides_png = [
    'slides/01-cover.png',
    'slides/02-nav.png',
    'slides/03-problem.png',
    'slides/04-capability.png',
    'slides/05-arch.png',
    'slides/06-evidence.png',
    'slides/07-permission.png',
    'slides/08-loop.png',
    'slides/09-interface.png',
    'slides/10-data.png',
    'slides/11-limitations.png',
    'slides/12-deliverables.png',
    'slides/13-closing.png',
]

prs = Presentation()
prs.slide_width = Inches(16)  # 1920px @ 120 DPI
prs.slide_height = Inches(9)  # 1080px @ 120 DPI

for png_path in slides_png:
    if not os.path.exists(png_path):
        print(f'⚠ 跳过缺失: {png_path}')
        continue

    slide = prs.slides.add_slide(prs.slide_layouts[6])  # 空白布局
    slide.shapes.add_picture(
        png_path,
        left=0,
        top=0,
        width=prs.slide_width,
        height=prs.slide_height
    )
    print(f'[OK] Added {os.path.basename(png_path)}')

output = '安言答辩-完整.pptx'
prs.save(output)
print(f'\n[OK] Generated {output}')
print(f'  Size: {os.path.getsize(output) / 1024 / 1024:.1f} MB')
print(f'  Slides: {len(prs.slides)}')

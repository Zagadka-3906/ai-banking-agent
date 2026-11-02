"""Export the approved AnYan logo kit into a fresh directory; preserve user files."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/brand/anyan-20261102/final"
SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


def contrast(first: str, second: str) -> float:
    def luminance(color):
        values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    a, b = sorted((luminance(first), luminance(second)))
    return (b + .05) / (a + .05)


def export(source: Path, output: Path, skill: Path, android_sdk: Path | None = None) -> dict:
    source, output, skill = source.resolve(), output.resolve(), skill.resolve()
    if not (source / "palette.json").is_file() or not (skill / "SKILL.md").is_file():
        raise ValueError("Brand source and installed logo-design skill are required")
    if output == source or source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Export separately from the source directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output must be new or empty; existing files will not be deleted")
    archive = output.with_suffix(".zip")
    if archive.exists():
        raise ValueError("Choose a new output name; its ZIP already exists")
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(skill / "scripts"))
    import render_png
    import svglib
    from concept_sheet import embed

    palette = json.loads((source / "palette.json").read_text(encoding="utf-8"))
    green, ink, white, paper, sage = [c["hex"] for c in palette["colors"]]
    for name in ("masters", "android"):
        shutil.copytree(source / name, output / name)
    for name in ("README.md", "palette.json", "presentation-spec.json"):
        shutil.copy2(source / name, output / name)
    (output / "source").mkdir()
    shutil.copy2(__file__, output / "source/build_anyan_brand.py")
    masters = output / "masters"

    def run(script, *args):
        result = subprocess.run([sys.executable, str(skill / "scripts" / script), *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            raise RuntimeError(f"{script} failed: {result.stderr[-2000:]}")
        return result.stdout

    def render(src, dst, size=1024):
        dst.parent.mkdir(parents=True, exist_ok=True)
        box = svglib.view_box(ET.parse(src).getroot())
        ratio = size / max(box[2:])
        width, height = round(box[2] * ratio), round(box[3] * ratio)
        run("render_png.py", src, "-o", dst, "--width", width, "--height", height)
        if not dst.is_file() or render_png.png_size(str(dst)) != (width, height):
            raise RuntimeError(f"PNG export failed: {dst.name}")

    # Use the skill's recolouring exporter, then apply the authored optical reverse.
    for layout in ("symbol", "horizontal", "stacked", "wordmark"):
        dest = output / "digital" / layout
        run("export_variants.py", masters / f"anyan-{layout}.svg", "--out-dir", dest,
            "--title", "AnYan", "--name", f"anyan-{layout}", "--only", "black", "white", "mono", "--mono", green, "--mono", ink, "--mono", sage)
        shutil.copy2(masters / f"anyan-{layout}.svg", dest / f"anyan-{layout}-color.svg")
        shutil.copy2(masters / f"anyan-{layout}-reversed.svg", dest / f"anyan-{layout}-white.svg")
        for vector in sorted(dest.glob("*.svg")):
            # The bundled exporter inserts a second title; retain one accessible title.
            tree = ET.parse(vector)
            for title in list(tree.getroot().findall(f"{{{SVG}}}title")):
                tree.getroot().remove(title)
            title = ET.Element(f"{{{SVG}}}title", {"id": "brand-title"})
            title.text = "AnYan / " + vector.stem
            tree.getroot().insert(0, title)
            tree.getroot().set("aria-labelledby", "brand-title")
            vector.write_text(ET.tostring(tree.getroot(), encoding="unicode") + "\n", encoding="utf-8")
            render(vector, vector.with_suffix(".png"))
    for size in (512, 2048):
        render(masters / "anyan-symbol.svg", output / "digital/symbol" / f"anyan-symbol-color-{size}.png", size)
    print("Outlined vectors and transparent PNGs exported.", flush=True)

    web = output / "web"
    run("export_variants.py", masters / "anyan-symbol.svg", "--out-dir", web, "--title", "AnYan",
        "--only", "favicon", "app-icon", "--favicon-source", masters / "anyan-symbol-small.svg",
        "--icon-bg", green, "--icon-scale", ".672", "--web-icons")
    # Opaque tile favicons remain legible on both light and dark browser chrome.
    shutil.copy2(masters / "anyan-favicon.svg", web / "favicon.svg")
    for size in (16, 32, 48):
        render(web / "favicon.svg", web / f"favicon-{size}.png", size)
    render_png.write_ico([str(web / f"favicon-{s}.png") for s in (16, 32, 48)], str(web / "favicon.ico"))
    for name, size in (("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)):
        render(masters / "anyan-app-icon-square.svg", web / name, size)
    maskable = (f'<svg xmlns="{SVG}" viewBox="0 0 256 256" width="256" height="256" role="img"><title>AnYan maskable icon</title>'
                f'<rect width="256" height="256" fill="{green}"/><g transform="translate(46.08 48.64) scale(0.64)">'
                f'<path fill="{white}" d="{palette["paths"]["reversed"]}"/></g></svg>')
    (web / "anyan-maskable.svg").write_text(maskable, encoding="utf-8")
    render(web / "anyan-maskable.svg", web / "maskable-512.png", 512)
    manifest = json.loads((web / "site.webmanifest").read_text(encoding="utf-8"))
    manifest["short_name"] = "AnYan"
    (web / "site.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, size, master in (("anyan-app-icon-1024.png", 1024, "anyan-app-icon.svg"),
                               ("anyan-app-icon-square-1024.png", 1024, "anyan-app-icon-square.svg"),
                               ("anyan-store-icon-512.png", 512, "anyan-app-icon-square.svg")):
        render(masters / master, output / "app" / name, size)
    for density, size in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
        render(masters / "anyan-app-icon-square.svg", output / "android" / f"mipmap-{density}/ic_launcher.png", size)
    print("Android and web icon sets exported.", flush=True)

    presentation = output / "presentation"
    presentation.mkdir()
    run("presentation_board.py", output / "presentation-spec.json", "-o", presentation / "brand-board.html")
    board = (presentation / "brand-board.html").read_text(encoding="utf-8")
    translations = {"Final identity": "正式视觉", "What we heard": "设计简报", "The brief": "需求与定位", "in use": "场景示意", "Payment card": "虚构银行卡示意", "App icon": "应用图标", "Website header": "网页页眉", "Browser tab": "浏览器图标", "Business card": "名片示意", "Business cards": "名片示意", "Social avatar": "社交头像", "Social profile": "社交主页", "Website": "网页示意", "Next steps": "使用说明", "Notes": "交付说明", "ALEX MORGAN": "DEMO ACCOUNT", "Alex Morgan": "Demo User", "anyan.com": "anyan.example"}
    for old, new in translations.items():
        board = board.replace(old, new)
    board = board.replace("名片示意s", "名片示意")
    board = re.sub(r"linear-gradient\([^)]*\)", paper, board)
    board = board.replace("</style>", f".m{{background:{paper}!important}}.phone{{background:{ink}!important}}.pcard .chip{{background:#C8A96B!important}}</style>")
    (presentation / "brand-board.html").write_text(board, encoding="utf-8")
    styles = re.search(r"<style>(.*?)</style>", board, re.S)[1]
    slides = re.findall(r'<section class="slide">.*?</section>', board, re.S)
    with tempfile.TemporaryDirectory(prefix="anyan-board-") as temporary:
        for i, slide in enumerate(slides, 1):
            page = Path(temporary) / f"slide-{i}.html"
            page.write_text('<meta charset="utf-8"><style>' + styles + 'body{background:white}.slide{margin:0;box-shadow:none;min-height:800px}</style>' + slide, encoding="utf-8")
            run("render_png.py", page, "-o", presentation / f"board-{i:02}.png", "--width", 1280, "--height", 800)

    def art(file, key, x, y, w, h):
        return embed(str(file), key, x, y, w, h).replace('aria-labelledby="brand-title"', 'aria-label="AnYan"')

    def label(text, x, y, size=24, color=ink):
        return f'<text x="{x}" y="{y}" font-family="Microsoft YaHei, sans-serif" font-size="{size}" fill="{color}">{html.escape(text)}</text>'

    parts = [f'<rect width="1600" height="1100" fill="{paper}"/>', label("AnYan 安言", 72, 78, 38), label("开口 A · 松柏绿主题", 72, 124, 24),
             art(masters / "anyan-horizontal.svg", "hero", 72, 175, 1100, 245),
             art(masters / "anyan-app-icon.svg", "app", 1280, 178, 220, 220)]
    for i, (name, color) in enumerate((("松柏绿", green), ("米白", paper), ("墨黑", ink), ("浅鼠尾草绿", sage))):
        x = 72 + i * 378
        parts += [f'<rect x="{x}" y="470" width="322" height="64" fill="{color}" stroke="{ink}" stroke-width="0.5"/>', label(name, x, 573), label(color, x, 608, 21)]
    for i, (name, file, dark) in enumerate((("标准符号", masters / "anyan-symbol.svg", False), ("墨黑单色", output / f"digital/symbol/anyan-symbol-mono-{ink[1:].lower()}.svg", False), ("反白", masters / "anyan-symbol-reversed.svg", True))):
        x = 72 + i * 378
        parts.append(label(name, x, 705))
        if dark:
            parts.append(f'<rect x="{x}" y="738" width="220" height="220" fill="{ink}"/>')
        parts.append(art(file, f"version-{i}", x + 10, 748, 200, 200))
    parts.append(label("浏览器图标", 1206, 705))
    for i, size in enumerate((16, 32, 64)):
        parts += [art(masters / "anyan-favicon.svg", f"favicon-{size}", 1206 + i * 100, 802, size, size), label(f"{size} px", 1206 + i * 100, 916, 18)]
    parts += [label("纯色 · 无渐变 · 路径字标 · 单色与反白 · Android 与网页图标", 72, 1028, 24), label("正式 Logo 套件 / 2026-11-02", 72, 1070, 18)]
    (presentation / "overview.svg").write_text(f'<svg xmlns="{SVG}" width="1600" height="1100" viewBox="0 0 1600 1100"><title>AnYan final logo kit</title>{"".join(parts)}</svg>', encoding="utf-8")
    render(presentation / "overview.svg", presentation / "overview.png", 1600)

    # Verify vector production rules, platform safe areas and generated artifacts.
    for vector in masters.glob("*.svg"):
        tree = ET.parse(vector)
        tags = {svglib.local(n.tag) for n in tree.getroot().iter()}
        if tags & {"text", "image", "filter", "mask", "linearGradient", "radialGradient"}:
            raise ValueError(f"Non-production vector master: {vector.name}")
        if any("stroke" in n.attrib for n in tree.getroot().iter()):
            raise ValueError("Unexpanded strokes in logo master")
    segments = [s for subpath in svglib.parse_path(palette["paths"]["reversed"]) for s in subpath]
    # A convex circle containing every Bezier control point contains the curves too.
    points = [p for segment in segments for p in segment[1:] if isinstance(p, tuple) and len(p) == 2]
    android_radius = max(math.hypot(x * .23 + 24.56 - 54, y * .23 + 25.48 - 54) for x, y in points)
    mask_radius = max(math.hypot(x * .64 + 46.08 - 128, y * .64 + 48.64 - 128) for x, y in points)
    if android_radius > 33 or mask_radius > 102.4:
        raise ValueError("Platform safe circle exceeded")
    for image in output.rglob("*.png"):
        if render_png.png_size(str(image)) is None:
            raise ValueError(f"Invalid PNG: {image.name}")
    ico = (web / "favicon.ico").read_bytes()
    count = struct.unpack_from("<H", ico, 4)[0]
    ico_sizes = sorted({ico[6 + 16 * i] or 256 for i in range(count)})
    if ico_sizes != [16, 32, 48]:
        raise ValueError("Favicon ICO sizes mismatch")
    ratios = {"pine_on_white": round(contrast(green, white), 2), "pine_on_paper": round(contrast(green, paper), 2), "white_on_ink": round(contrast(white, ink), 2), "sage_on_ink": round(contrast(sage, ink), 2)}
    if min(ratios.values()) < 4.5:
        raise ValueError("Approved colour pair has insufficient contrast")
    raster_validation = "PNG headers and declared dimensions"
    try:
        from PIL import Image
    except ImportError:
        pass
    else:
        for image in output.rglob("*.png"):
            with Image.open(image) as picture:
                picture.verify()
        for image in (output / "digital").rglob("*.png"):
            with Image.open(image) as picture:
                alpha = picture.convert("RGBA").getchannel("A").getextrema()
                if alpha != (0, 255):
                    raise ValueError(f"Expected transparent artwork: {image.name}")
        raster_validation = "All PNGs decoded; digital logo alpha spans 0–255"
    android_validation = "not run (no --android-sdk supplied)"
    if android_sdk:
        executables = sorted(android_sdk.glob("build-tools/*/aapt2.exe")) if os.name == "nt" else sorted(android_sdk.glob("build-tools/*/aapt2"))
        frameworks = sorted(android_sdk.glob("platforms/*/android.jar"))
        if not executables or not frameworks:
            raise ValueError("Android SDK requires aapt2 and an installed platform")
        with tempfile.TemporaryDirectory(prefix="anyan-resources-") as temporary:
            temporary = Path(temporary)
            manifest_file = temporary / "AndroidManifest.xml"
            manifest_file.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.anyan.brand.validation"><application android:label="AnYan" android:icon="@mipmap/ic_launcher"/></manifest>', encoding="utf-8")
            compile_command = [str(executables[-1]), "compile", "--dir", str(output / "android"), "-o", str(temporary / "resources.zip")]
            link_command = [str(executables[-1]), "link", "--manifest", str(manifest_file), "--min-sdk-version", "26", "-I", str(frameworks[-1]), "-o", str(temporary / "validation.apk"), str(temporary / "resources.zip")]
            for command in (compile_command, link_command):
                result = subprocess.run(command, capture_output=True, text=True, errors="replace")
                if result.returncode:
                    raise RuntimeError("Android resource validation failed: " + result.stderr[-2000:])
        android_validation = "aapt2 compile and link passed; minSdk 26"
    validation = {"version": palette["version"], "colors": [c["hex"] for c in palette["colors"]], "contrast_ratios": ratios,
                  "masters": len(list(masters.glob("*.svg"))), "live_text_in_masters": False, "gradients_in_masters": False,
                  "android_safe_circle_radius": 33, "android_max_control_point_radius": round(android_radius, 3),
                  "maskable_safe_circle_radius": 102.4, "maskable_max_control_point_radius": round(mask_radius, 3),
                  "favicon_ico_sizes": ico_sizes, "board_slides": len(slides), "raster_validation": raster_validation, "android_resources": android_validation,
                  "limits": ["No trademark clearance", "CMYK values are mathematical approximations", "Application mockups are illustrative"]}
    (output / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(p for p in output.rglob("*") if p.is_file())
    checksums = {p.relative_to(output).as_posix(): {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}
    (output / "manifest.json").write_text(json.dumps({"brand": "AnYan", "version": palette["version"], "files": checksums}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for p in [*files, output / "manifest.json"]:
            bundle.write(p, "AnYan-Logo-Kit/" + p.relative_to(output).as_posix())
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():
            raise ValueError("Invalid final ZIP")
    return {"output": str(output), "archive": str(archive), "files": len(files) + 1, "contrast": ratios, "bytes": archive.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/anyan-brand")
    parser.add_argument("--skill-dir", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills/logo-design")
    parser.add_argument("--android-sdk", type=Path, help="Optionally compile and link the delivered Android resources")
    args = parser.parse_args()
    print(json.dumps(export(args.source_dir, args.output_dir, args.skill_dir, args.android_sdk), ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Add readable captions to the real emulator recording, without fabricated UI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


def stamp(seconds):
    value = max(0, int(seconds * 100))
    return f"{value // 360000}:{value // 6000 % 60:02}:{value // 100 % 60:02}.{value % 100:02}"


def wrap_caption(text, width=40):
    lines, line, used = [], "", 0
    for token in re.findall(r"[A-Za-z0-9_./:]+|[^\x00-\x7f]|.", text):
        size = sum(2 if ord(char) > 127 else 1 for char in token)
        if line and used + size > width:
            lines.append(line.rstrip())
            line, used = "", 0
        line += token
        used += size
    if line:
        lines.append(line.rstrip())
    return r"\N".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--recording-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    folder = args.recording_dir.resolve()
    chapters = json.loads((folder / "chapters.json").read_text(encoding="utf-8"))
    start = json.loads((folder / "recording-start.json").read_text(encoding="utf-8"))["at_ms"]
    probe = subprocess.run([str(args.ffmpeg), "-hide_banner", "-i", str(folder / "raw-demo.mp4")], capture_output=True, text=True)
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    if not match:
        raise ValueError("Recording has no valid duration")
    duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    trim = max(0, (chapters[0]["at_ms"] - start) / 1000 - 1)
    final_duration = duration - trim
    if not 20 <= final_duration <= 300:
        raise ValueError("Demo must contain 20–300 seconds of actual footage")
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1600", "PlayResY: 900", "WrapStyle: 2", "",
             "[V4+ Styles]", "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
             "Style: Default,Microsoft YaHei,36,&H00FFFFFF,&H00FFFFFF,&H00321B10,&H00321B10,0,0,0,0,100,100,0,0,1,0,0,7,610,70,230,1", "",
             "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]
    for index, chapter in enumerate(chapters):
        begin = max(0, (chapter["at_ms"] - start) / 1000 - trim)
        end = min(final_duration, (chapters[index + 1]["at_ms"] - start) / 1000 - trim) if index + 1 < len(chapters) else final_duration
        title = chapter["title"].replace("{", "（").replace("}", "）")
        caption = chapter["caption"].replace("{", "（").replace("}", "）")
        # Force short lines suitable for a horizontal video beside the phone.
        wrapped = wrap_caption(caption)
        lines.append(f"Dialogue: 0,{stamp(begin)},{stamp(end)},Default,,0,0,0,,{{\\b1\\fs44}}{title}{{\\b0\\fs34}}\\N\\N{wrapped}")
    subtitle = folder / "captions.ass"
    subtitle.write_text("\n".join(lines), encoding="utf-8-sig")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Relative subtitle path avoids Windows drive-letter quoting inside filters.
    command = [str(args.ffmpeg.resolve()), "-hide_banner", "-y", "-ss", str(trim), "-i", "raw-demo.mp4",
               "-vf", "scale=-2:850,pad=1600:900:80:25:color=0x101b32,ass=captions.ass",
               "-an", "-c:v", "libx264", "-threads", "2", "-crf", "21", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(args.output.resolve())]
    with (folder / "video-build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=folder, stdout=log, stderr=subprocess.STDOUT, check=True)
    (args.output.parent / "video-manifest.json").write_text(json.dumps({"duration_seconds": final_duration,
        "source": "real_android_screenrecord", "model_mode": "offline_rules", "audio": "none_captioned",
        "synthetic_ui": False, "chapters": len(chapters)}, indent=2), encoding="utf-8")
    print(json.dumps({"duration_seconds": final_duration, "chapters": len(chapters)}))


if __name__ == "__main__":
    main()

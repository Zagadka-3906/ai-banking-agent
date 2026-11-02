"""Check local Markdown/image paths against tracked repository files only."""
from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)")


class HTMLLinks(HTMLParser):
    """Collect GitHub-supported HTML image sources and navigation targets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        attribute = 'href' if tag == 'a' else 'src' if tag in {'img', 'source'} else None
        if attribute and values.get(attribute):
            self.links.append(str(values[attribute]))
        if tag in {'img', 'source'} and values.get('srcset'):
            # Repository images use URL + optional width/density descriptors.
            # A data URI is external to the repository path check.
            srcset = str(values['srcset']).strip()
            if srcset.startswith('data:'):
                return
            self.links.extend(candidate.strip().split()[0] for candidate in srcset.split(',') if candidate.strip())


def local_links(text: str) -> list[str]:
    # Examples in fenced blocks are not document navigation.
    text = re.sub(r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$", "", text)
    html = HTMLLinks()
    html.feed(text)
    return [m[1].strip("<>") for m in MARKDOWN_LINK.finditer(text)] + html.links


def tracked_files(root: Path) -> set[str]:
    return set(subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").strip("\0").split("\0"))


def check(root: Path) -> tuple[int, int, list[str]]:
    tracked = tracked_files(root)
    errors = []
    documents = links = 0
    for name in sorted(tracked):
        if not name.endswith(".md"):
            continue
        documents += 1
        for target in local_links((root / name).read_text(encoding="utf-8")):
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            links += 1
            path = (root / PurePosixPath(name).parent / unquote(url.path)).resolve()
            if not path.is_relative_to(root.resolve()):
                errors.append(f"{name}: outside repository: {target}")
                continue
            relative = path.relative_to(root.resolve()).as_posix()
            if relative not in tracked and not any(p.startswith(relative.rstrip("/") + "/") for p in tracked):
                errors.append(f"{name}: missing or untracked: {target}")
    return documents, links, errors


def main() -> int:
    documents, links, errors = check(ROOT)
    print(f"Checked {documents} Markdown files, {links} local paths; {len(errors)} errors.")
    for error in errors:
        print(error)
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())

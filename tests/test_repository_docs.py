"""Keep public README picture variants and HTML navigation reproducible."""
from pathlib import Path
import subprocess

import pytest

from scripts.check_repository_docs import check, local_links


def test_picture_collects_all_light_dark_and_density_sources():
    text = '''<picture>
    <source media="(prefers-color-scheme: dark)" srcset="dark.svg 1x, dark@2x.svg 2x">
    <img src="light.svg" srcset="small.webp 320w, large.webp 640w" alt="demo">
    </picture><a href="guide.md#start">guide</a>'''
    assert local_links(text) == ['dark.svg', 'dark@2x.svg', 'light.svg', 'small.webp', 'large.webp', 'guide.md#start']


def test_fenced_examples_do_not_become_required_assets():
    text = '```html\n<img src="not-a-file.svg">\n```\n\n![actual](real.svg)\n<a href="#start">start</a>'
    assert local_links(text) == ['real.svg', '#start']


def test_html_parser_accepts_case_order_and_entities():
    assert local_links("<IMG alt='x' SRC='images/a%20b.svg'><A HREF='guide.md?x=1&amp;y=2'>x</A>") == [
        'images/a%20b.svg', 'guide.md?x=1&y=2',
    ]


@pytest.mark.parametrize('tracked_dark', [False, True])
def test_picture_dark_source_must_be_committed_asset(tmp_path: Path, tracked_dark: bool):
    subprocess.run(['git', 'init', '--quiet', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / 'README.md').write_text('<picture><source srcset="dark.svg"><img src="light.svg"></picture>', encoding='utf-8')
    for name in ['light.svg', 'dark.svg']:
        (tmp_path / name).write_text('<svg/>', encoding='utf-8')
    subprocess.run(['git', 'add', 'README.md', 'light.svg'], cwd=tmp_path, check=True, capture_output=True)
    if tracked_dark:
        subprocess.run(['git', 'add', 'dark.svg'], cwd=tmp_path, check=True, capture_output=True)
    documents, links, errors = check(tmp_path)
    assert (documents, links) == (1, 2)
    assert errors == ([] if tracked_dark else ['README.md: missing or untracked: dark.svg'])


def test_data_uri_srcset_does_not_create_fake_local_paths():
    assert local_links('<source srcset="data:image/png;base64,AAAA">') == []

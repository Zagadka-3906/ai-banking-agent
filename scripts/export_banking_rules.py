"""Generate/check the human and machine rulebooks from the runtime rule registry."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from banking_rules import export_rules, render_rulebook


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    outputs = {ROOT / 'docs/银行业务规则说明.md': render_rulebook(),
               ROOT / 'docs/银行业务规则.json': json.dumps(export_rules(), ensure_ascii=False, indent=2) + '\n'}
    stale = []
    for path, content in outputs.items():
        if args.check:
            if not path.is_file() or path.read_text(encoding='utf-8') != content:
                stale.append(path.name)
        else:
            path.write_text(content, encoding='utf-8', newline='\n')
    if stale:
        print('Rulebook out of date: ' + ', '.join(stale))
        return 1
    print('Rulebook check passed' if args.check else 'Generated rulebook and JSON')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Read-only platform token probe. Never prints app secrets or returned tokens."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-network", action="store_true", help="Also probe without environment proxy settings")
    parser.add_argument("--output", type=Path, help="Optional private report under build/")
    args = parser.parse_args()
    output = args.output.resolve() if args.output else None
    if output and not output.is_relative_to(root / "build"):
        parser.error("Diagnostic reports must stay under ignored build/")
    load_dotenv(root / ".env")
    import sys
    sys.path.insert(0, str(root))
    from feishu_access import probe_access
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "stage": "tenant_token",
              "environment": probe_access()}
    if args.compare_network:
        report["direct"] = probe_access(direct=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["environment"]["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

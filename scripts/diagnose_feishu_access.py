"""Read-only platform token probe. Never prints app secrets or returned tokens."""
from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from feishu_access import probe_access
    print(json.dumps(probe_access()))


if __name__ == "__main__":
    main()

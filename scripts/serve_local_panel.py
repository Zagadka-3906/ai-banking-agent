"""Local panel CLI; service ownership lives in deploy.panel.local_runtime."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.panel.local_runtime import main

if __name__ == '__main__':
    raise SystemExit(main())

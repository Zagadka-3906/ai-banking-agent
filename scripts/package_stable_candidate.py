"""Package a committed candidate only after the full release gate accepts it."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def package(manifest: Path, output: Path) -> int:
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'build') or output.exists():
        raise ValueError('Use a new output directory under build/')
    changed = subprocess.check_output(['git', 'diff', 'HEAD', '--name-only'], cwd=ROOT)
    if changed.strip():
        raise ValueError('Commit tracked changes before packaging')
    output.mkdir(parents=True)
    gate = subprocess.run([sys.executable, str(ROOT / 'scripts/check_dialogue_release.py'),
        '--manifest', str(manifest.resolve()), '--output', str(output / 'gate.json')], cwd=ROOT, capture_output=True)
    (output / 'gate-command.log').write_bytes(gate.stdout + gate.stderr)
    if gate.returncode:
        print('BLOCKED: no source archive created; see gate evidence')
        return 1
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    archive = output / 'source.tar.gz'
    subprocess.run(['git', 'archive', '--format=tar.gz', '--output=' + str(archive), revision], cwd=ROOT, check=True)
    (output / 'revision.json').write_text(json.dumps({'revision': revision, 'release_gate': 'accepted'}), encoding='utf-8')
    print(json.dumps({'revision': revision, 'source_archive': str(archive), 'deployed': False}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    return package(args.manifest, args.output_dir)


if __name__ == '__main__':
    raise SystemExit(main())

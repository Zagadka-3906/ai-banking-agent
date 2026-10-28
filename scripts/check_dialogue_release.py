"""Check a frozen candidate; a BLOCKED report exits 1 and cannot release it."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.dialogue_eval.release_gate import assess_release
from scripts.evaluate_dialogue_tasks import source_fingerprints
from scripts.dialogue_eval.schema import load_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'build') or output.exists():
        parser.error('Use a new ignored build/ output file; retain previous results')
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if set(manifest) - {'groups', 'operational'} or not isinstance(manifest.get('groups'), list):
        parser.error('Manifest requires groups and optional operational evidence path')
    operational = json.loads(Path(manifest['operational']).read_text(encoding='utf-8')) if manifest.get('operational') else None
    result = assess_release(manifest['groups'], source_fingerprints(), operational)
    # The caller cannot manufacture a green regression suite by omitting
    # previously exposed failures. The repository owns this required inventory.
    required = set()
    for name in ['dialogue_tasks', 'dialogue_holdout', 'dialogue_generalization', 'dialogue_repair_regression_v2']:
        required.update(task.id for task in load_dataset(ROOT / 'tests/data' / (name + '.json'))[0].tasks)
    supplied = set()
    for group in manifest['groups']:
        if group.get('role') == 'regression':
            supplied.update(task.id for task in load_dataset(Path(group['cases']))[0].tasks)
    missing = sorted(required - supplied)
    if missing:
        result.update(accepted=False, offline_ready=False, missing_known_regressions=missing)
        result['blockers'].append('KNOWN_REGRESSION_INVENTORY_INCOMPLETE')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

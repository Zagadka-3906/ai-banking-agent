import json
from pathlib import Path

import pytest

from bank_agent import prompt_preferences
from scripts.optimize_dialogue_prompts import main, optimization_sets
from scripts.dialogue_eval.schema import load_dataset


def dataset(tmp_path,name,*,split='development',rename=False):
    source=load_dataset(Path(__file__).parent/'data/dialogue_tasks.json')[0]
    task=source.tasks[0].model_dump()
    if rename:
        task['id']='renamed_same_task'
    path=tmp_path/(name+'.json')
    path.write_text(json.dumps({'version':1,'split':split,'source':'unit fixture','tasks':[task]},ensure_ascii=False),encoding='utf-8')
    return path


def test_optimization_rejects_holdout_and_train_validation_leakage(tmp_path):
    train=dataset(tmp_path,'train')
    with pytest.raises(ValueError,match='holdout'):
        optimization_sets(train,dataset(tmp_path,'holdout',split='holdout'))
    with pytest.raises(ValueError,match='overlap'):
        optimization_sets(train,dataset(tmp_path,'same'))
    with pytest.raises(ValueError,match='renamed'):
        optimization_sets(train,dataset(tmp_path,'renamed',rename=True))


@pytest.mark.parametrize('candidate',[
    {'url':'https://example.invalid'}, {'answering':{'code':'execute()'}}, {'review':'a'*2501},
])
def test_candidates_are_bounded_text_components(candidate):
    with pytest.raises(ValueError):
        prompt_preferences.validate_instructions(candidate)


def test_production_cannot_install_offline_candidate(monkeypatch):
    monkeypatch.delenv('BANK_EVAL_ISOLATED_PROCESS',raising=False)
    with pytest.raises(RuntimeError,match='isolated'):
        prompt_preferences.install_for_evaluation({'answering':'candidate'})
    assert prompt_preferences.suffix('answering')==''


def test_optimization_requires_explicit_live_and_an_ignored_new_output(tmp_path):
    first=dataset(tmp_path,'train')
    data=json.loads(first.read_text(encoding='utf-8'))
    second_task=load_dataset(Path(__file__).parent/'data/dialogue_tasks.json')[0].tasks[1].model_dump()
    data['tasks']=[second_task]
    second=tmp_path/'validation.json'
    second.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    assert main(['--train',str(first),'--validation',str(second),'--dry-run'])==0
    with pytest.raises(SystemExit):
        main(['--train',str(first),'--validation',str(second)])

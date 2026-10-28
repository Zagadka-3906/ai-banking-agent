"""Private task criteria are deliberately separate from simulator instructions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Phase(StrictModel):
    goal: str = Field(min_length=1, max_length=1000)
    utterance: str = Field(min_length=1, max_length=500)
    action: Literal["inquiry", "clarify", "proposal", "confirm", "reject"] = "inquiry"
    criteria: str = Field(min_length=1, max_length=1500)
    goal_shift: bool = False
    required_reads: list[Literal['balance', 'cards', 'transactions', 'contacts', 'wealth_products', 'wealth_holdings', 'risk_profile', 'tasks', 'disputes']] = Field(default_factory=list, max_length=5)
    answered_goals: int | None = Field(default=None, ge=1, le=5)


class Task(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    goal: str = Field(min_length=1, max_length=1500)
    persona: str = Field(min_length=1, max_length=500)
    user_facts: str = Field(min_length=1, max_length=1000)
    topic: Literal["general", "dispute", "transfer", "card", "cross_scene", "security", "limits", "contacts", "wealth", "subscription", "tasks"]
    setup: Literal["default", "frozen_card", "recent_limit_change", "mixed_tasks", "tasks_read_outage"] = "default"
    outcome: Literal["unchanged", "transfer_300"] = "unchanged"
    proposal_intent: Literal["TRANSFER", "CARD_LIMIT"] = "TRANSFER"
    phases: list[Phase] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_actions(self):
        actions = [phase.action for phase in self.phases]
        if self.outcome == "transfer_300":
            if actions.count("confirm") != 1 or self.proposal_intent != "TRANSFER":
                raise ValueError("A transfer task needs exactly one confirmation")
        elif "confirm" in actions:
            raise ValueError("Read/proposal tasks cannot confirm execution")
        if any(action in {"reject", "confirm"} for action in actions) and "proposal" not in actions:
            raise ValueError("An approval decision needs a proposal phase")
        return self

    def user_view(self, phase: Phase, history: list[dict]) -> dict:
        # No setup, expected state, intent, criteria, tool trace or oracle verdict.
        return {"goal": phase.goal, "persona": self.persona, "known_facts": self.user_facts,
                "current_goal": phase.goal, "conversation": history}


class Dataset(StrictModel):
    version: Literal[1]
    rubric_version: Literal['v1', 'v2', 'v3'] = 'v1'
    split: Literal['development', 'holdout'] = 'development'
    source: str
    tasks: list[Task] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({task.id for task in self.tasks}) != len(self.tasks):
            raise ValueError("Duplicate task IDs")
        return self


def load_dataset(path: Path) -> tuple[Dataset, str]:
    raw = path.read_bytes()
    return Dataset.model_validate(json.loads(raw)), hashlib.sha256(raw).hexdigest()

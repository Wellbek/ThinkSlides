"""Every benchmark task loads, is unique, and can be judged."""

from pathlib import Path

import pytest

from app.research.task import Task

TASKS = sorted((Path(__file__).resolve().parents[3] / "benchmark" / "tasks").rglob("*.yaml"))


def test_there_are_tasks_to_run():
    assert len(TASKS) >= 10


@pytest.mark.parametrize("path", TASKS, ids=lambda p: p.stem)
def test_a_task_loads_and_carries_a_checklist(path):
    task = Task.load(path)
    assert task.category == path.parent.name
    assert task.mode in ("simple", "deep")
    assert task.detail in ("concise", "detailed", "comprehensive")
    assert len(task.checklist) >= 5


def test_task_ids_are_unique():
    ids = [Task.load(p).id for p in TASKS]
    assert len(ids) == len(set(ids))

"""评测集完整性测试：任务定义必须可解析、字段齐全、编号唯一。

这个仓库的产物是「基准」，基准本身如果写错了，所有分数都不成立，
所以把结构校验放进 CI。
"""
from pathlib import Path

import pytest
import yaml

TASKS_DIR = Path(__file__).resolve().parent.parent / "tasks"
TASK_FILES = sorted(TASKS_DIR.glob("*.yaml"))

REQUIRED = ("id", "name", "platform", "url", "steps", "success_criteria")
DIFFICULTIES = {"easy", "medium", "hard"}


def load(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_tasks_exist():
    assert len(TASK_FILES) >= 10, f"预期至少 10 个任务，实际 {len(TASK_FILES)}"


@pytest.mark.parametrize("path", TASK_FILES, ids=[p.stem for p in TASK_FILES])
def test_required_fields(path):
    task = load(path)
    for key in REQUIRED:
        assert task.get(key), f"{path.name} 缺少字段 {key}"
    assert isinstance(task["steps"], list) and len(task["steps"]) >= 2
    assert isinstance(task["success_criteria"], list) and task["success_criteria"]
    assert task["id"] in path.stem, f"{path.name} 的文件名与 id({task['id']}) 不一致"


@pytest.mark.parametrize("path", TASK_FILES, ids=[p.stem for p in TASK_FILES])
def test_values_are_sane(path):
    task = load(path)
    assert str(task["url"]).startswith("http")
    if "difficulty" in task:
        assert task["difficulty"] in DIFFICULTIES, f"{path.name} difficulty 非法"
    assert isinstance(task.get("requires_login"), bool)


def test_ids_unique():
    ids = [load(p)["id"] for p in TASK_FILES]
    dup = {i for i in ids if ids.count(i) > 1}
    assert not dup, f"重复的任务 id: {dup}"

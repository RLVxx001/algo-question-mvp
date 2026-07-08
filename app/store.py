from __future__ import annotations

import json
from pathlib import Path

from app.models import GeneratedProblem, ProblemWorkflow


class ProblemStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, problem: GeneratedProblem) -> None:
        path = self.path_for(problem.id)
        path.write_text(json.dumps(problem.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, problem_id: str) -> GeneratedProblem:
        path = self.path_for(problem_id)
        if not path.exists():
            raise KeyError(problem_id)
        return GeneratedProblem.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[GeneratedProblem]:
        problems: list[GeneratedProblem] = []
        for path in sorted(self.root.glob("*.json")):
            problems.append(GeneratedProblem.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        return problems

    def delete(self, problem_id: str) -> bool:
        path = self.path_for(problem_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def path_for(self, problem_id: str) -> Path:
        safe_id = "".join(ch for ch in problem_id if ch.isalnum() or ch in "-_")
        return self.root / f"{safe_id}.json"


class WorkflowStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, workflow: ProblemWorkflow) -> None:
        self.path_for(workflow.problem_id).write_text(
            json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, problem_id: str) -> ProblemWorkflow:
        path = self.path_for(problem_id)
        if not path.exists():
            raise KeyError(problem_id)
        return ProblemWorkflow.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def delete(self, problem_id: str) -> bool:
        path = self.path_for(problem_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def path_for(self, problem_id: str) -> Path:
        safe_id = "".join(ch for ch in problem_id if ch.isalnum() or ch in "-_")
        return self.root / f"{safe_id}.json"

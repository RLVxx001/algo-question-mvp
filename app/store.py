from __future__ import annotations

import json
import shutil
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
        try:
            path = self.path_for(problem_id)
        except ValueError as exc:
            raise KeyError(problem_id) from exc
        if not path.exists():
            raise KeyError(problem_id)
        return GeneratedProblem.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> list[GeneratedProblem]:
        problems: list[GeneratedProblem] = []
        for path in sorted(self.root.glob("*.json")):
            problem = _read_problem_for_list(path)
            if problem is not None:
                problems.append(problem)
        return problems

    def delete(self, problem_id: str) -> bool:
        try:
            path = self.path_for(problem_id)
        except ValueError:
            return False
        if not path.exists():
            return False
        path.unlink()
        return True

    def path_for(self, problem_id: str) -> Path:
        return self.root / f"{_safe_id(problem_id)}.json"


def _read_problem_for_list(path: Path) -> GeneratedProblem | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return GeneratedProblem.from_dict(data)
    except (TypeError, ValueError):
        return None


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
        try:
            path = self.path_for(problem_id)
        except ValueError as exc:
            raise KeyError(problem_id) from exc
        if not path.exists():
            raise KeyError(problem_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("workflow data must be an object")
            return ProblemWorkflow.from_dict(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise KeyError(problem_id) from exc

    def delete(self, problem_id: str) -> bool:
        try:
            path = self.path_for(problem_id)
        except ValueError:
            return False
        if not path.exists():
            return False
        path.unlink()
        return True

    def path_for(self, problem_id: str) -> Path:
        return self.root / f"{_safe_id(problem_id)}.json"


class ReportStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save_review(self, problem_id: str, report: dict) -> None:
        self._write(problem_id, "review_report.json", report)

    def save_validation(self, problem_id: str, report: dict) -> None:
        self._write(problem_id, "validation_report.json", report)

    def get_review(self, problem_id: str) -> dict | None:
        return self._read(problem_id, "review_report.json")

    def get_validation(self, problem_id: str) -> dict | None:
        return self._read(problem_id, "validation_report.json")

    def delete(self, problem_id: str) -> bool:
        try:
            report_dir = self.dir_for(problem_id)
        except ValueError:
            return False
        if not report_dir.exists():
            return False
        shutil.rmtree(report_dir)
        return True

    def dir_for(self, problem_id: str) -> Path:
        return self.root / _safe_id(problem_id)

    def _write(self, problem_id: str, name: str, report: dict) -> None:
        report_dir = self.dir_for(problem_id)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read(self, problem_id: str, name: str) -> dict | None:
        try:
            path = self.dir_for(problem_id) / name
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None


def _safe_id(problem_id: str) -> str:
    if not problem_id or any(not (ch.isalnum() or ch in "-_") for ch in problem_id):
        raise ValueError("problem_id contains unsupported characters")
    return problem_id

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
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("problem data must be an object")
            problem = GeneratedProblem.from_dict(data)
            if problem.id != problem_id:
                raise ValueError("problem id does not match storage key")
            return problem
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise KeyError(problem_id) from exc

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
        problem = GeneratedProblem.from_dict(data)
        _safe_id(problem.id)
        if problem.id != path.stem:
            return None
        return problem
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
            workflow = ProblemWorkflow.from_dict(data)
            if workflow.problem_id != problem_id:
                raise ValueError("workflow problem id does not match storage key")
            return workflow
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
        if report_dir.is_dir() and not report_dir.is_symlink():
            shutil.rmtree(report_dir)
        else:
            report_dir.unlink()
        return True

    def dir_for(self, problem_id: str) -> Path:
        return self.root / _safe_id(problem_id)

    def _write(self, problem_id: str, name: str, report: dict) -> None:
        report_dir = self.dir_for(problem_id)
        if report_dir.is_symlink() or (report_dir.exists() and not report_dir.is_dir()):
            report_dir.unlink()
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read(self, problem_id: str, name: str) -> dict | None:
        try:
            report_dir = self.dir_for(problem_id)
        except ValueError:
            return None
        if report_dir.is_symlink() or not report_dir.is_dir():
            return None
        path = report_dir / name
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("problem_id") not in {None, problem_id}:
            return None
        return data


def _safe_id(problem_id: str) -> str:
    if not problem_id or any(not (ch.isalnum() or ch in "-_") for ch in problem_id):
        raise ValueError("problem_id contains unsupported characters")
    return problem_id

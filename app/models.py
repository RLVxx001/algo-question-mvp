from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ProblemRequest:
    topic: str
    difficulty: str = "easy"
    language: str = "python"
    statement_language: str = "zh"
    count: int = 1
    use_llm: bool = True


@dataclass
class WorkflowStep:
    key: str
    name: str
    mode: str = "auto"
    status: str = "pending"
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowStep":
        return cls(**data)


@dataclass
class ProblemWorkflow:
    problem_id: str
    current_step: str
    status: str
    steps: list[WorkflowStep]
    created_at: str
    updated_at: str
    generation_config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProblemWorkflow":
        data["steps"] = [WorkflowStep.from_dict(step) for step in data.get("steps", [])]
        data.setdefault("generation_config", {})
        return cls(**data)


@dataclass
class GeneratedProblem:
    id: str
    title: str
    topic: str
    difficulty: str
    statement: str
    input_format: str
    output_format: str
    constraints: list[str]
    samples: list[dict[str, str]]
    tags: list[str]
    solution_explanation: str
    reference_solution: str
    brute_force_solution: str
    generator_code: str
    created_at: str
    source: str
    statement_language: str = "zh"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeneratedProblem":
        if not data.get("statement_language"):
            statement = str(data.get("statement", ""))
            data["statement_language"] = "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in statement) else "en"
        return cls(**data)


@dataclass
class ValidationCaseResult:
    index: int
    input: str
    expected: str
    actual: str
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    problem_id: str
    sample_passed: bool
    fuzz_passed: bool
    total_cases: int
    failed_cases: list[ValidationCaseResult]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failed_cases"] = [case.to_dict() for case in self.failed_cases]
        return data


@dataclass
class ReviewIssue:
    severity: str
    field: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewReport:
    problem_id: str
    passed: bool
    score: int
    issues: list[ReviewIssue]
    checks: list[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = [issue.to_dict() for issue in self.issues]
        return data

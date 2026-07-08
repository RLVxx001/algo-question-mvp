from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.exporter import export_problem_package
from app.generator import generate_workflow_stage
from app.models import GeneratedProblem, ProblemRequest, ProblemWorkflow, WorkflowStep
from app.paths import resolve_under
from app.reviewer import review_blocks_execution, review_problem
from app.validator import ValidationError, validate_problem


WORKFLOW_STEP_DEFS = [
    ("idea", "创意方向"),
    ("statement", "题面"),
    ("constraints", "约束与样例"),
    ("solutions", "标准解与暴力解"),
    ("generator", "测试数据生成器"),
    ("review", "审查"),
    ("validate", "验证"),
    ("package", "导出"),
]
MANUAL_WORKFLOW_STEP_KEYS = {"idea", "statement", "constraints", "solutions", "generator"}


def create_workflow(problem: GeneratedProblem, req: ProblemRequest, manual_steps: list[str] | None = None) -> ProblemWorkflow:
    manual = set(normalize_manual_steps(manual_steps))
    now = _now()
    steps = [
        WorkflowStep(
            key=key,
            name=name,
            mode="manual" if key in manual else "auto",
            status="pending",
        )
        for key, name in WORKFLOW_STEP_DEFS
    ]
    return ProblemWorkflow(
        problem_id=problem.id,
        current_step=steps[0].key,
        status="running",
        steps=steps,
        created_at=now,
        updated_at=now,
        generation_config=req.__dict__.copy(),
    )


def normalize_manual_steps(manual_steps: object | None) -> list[str]:
    if manual_steps is None:
        return ["statement"]
    if not isinstance(manual_steps, list):
        raise ValueError("manual_steps must be a list of step names")
    normalized = []
    for step in manual_steps:
        if not isinstance(step, str):
            raise ValueError("manual_steps must be a list of step names")
        step = step.strip()
        if step not in MANUAL_WORKFLOW_STEP_KEYS:
            raise ValueError(f"manual_steps contains unsupported step: {step}")
        normalized.append(step)
    return normalized


def advance_workflow(
    workflow: ProblemWorkflow,
    problem: GeneratedProblem,
    package_root,
    confirm_current: bool = False,
) -> tuple[ProblemWorkflow, dict]:
    events: list[dict] = []
    reports: dict = {}

    if confirm_current:
        current = _current_step(workflow)
        if current and current.status == "waiting_user":
            _complete_step(current, problem)
            events.append({"step": current.key, "status": "completed", "summary": current.summary})

    req = ProblemRequest(**workflow.generation_config)
    while True:
        step = _first_incomplete_step(workflow)
        if not step:
            workflow.status = "completed"
            workflow.current_step = "completed"
            workflow.updated_at = _now()
            return workflow, {"events": events, "reports": reports, "problem": problem}

        workflow.current_step = step.key
        step.status = "running"
        step.summary = "进行中"
        workflow.status = "running"
        workflow.updated_at = _now()

        if step.key in {"idea", "statement", "constraints", "solutions", "generator"}:
            problem = generate_workflow_stage(problem, req, step.key)

        if step.mode == "manual":
            step.status = "waiting_user"
            step.summary = _step_summary(step.key, problem)
            workflow.status = "waiting_user"
            workflow.updated_at = _now()
            events.append({"step": step.key, "status": "waiting_user", "summary": step.summary})
            return workflow, {"events": events, "reports": reports, "problem": problem}

        if step.key == "review":
            review = review_problem(problem)
            reports["review"] = review.to_dict()
            if not review.passed:
                step.status = "failed"
                step.summary = f"审查未通过，score={review.score}"
                workflow.status = "failed"
                workflow.updated_at = _now()
                events.append({"step": step.key, "status": "failed", "summary": step.summary})
                return workflow, {"events": events, "reports": reports, "problem": problem}
            step.status = "completed"
            step.summary = f"审查通过，score={review.score}"
            events.append({"step": step.key, "status": "completed", "summary": step.summary})
            continue

        if step.key == "validate":
            review = review_problem(problem)
            if review_blocks_execution(review):
                reports["review"] = review.to_dict()
                step.status = "failed"
                step.summary = "验证被阻止：生成代码包含危险本地执行"
                workflow.status = "failed"
                workflow.updated_at = _now()
                events.append({"step": step.key, "status": "failed", "summary": step.summary})
                return workflow, {"events": events, "reports": reports, "problem": problem}
            validation = validate_problem(problem, rounds=30, timeout_seconds=1.0)
            reports["validation"] = validation.to_dict()
            if not validation.fuzz_passed or not validation.sample_passed:
                step.status = "failed"
                reason = validation.failed_cases[0].reason if validation.failed_cases else "验证未通过"
                step.summary = reason
                workflow.status = "failed"
                workflow.updated_at = _now()
                events.append({"step": step.key, "status": "failed", "summary": step.summary})
                return workflow, {"events": events, "reports": reports, "problem": problem}
            step.status = "completed"
            step.summary = f"验证通过，cases={validation.total_cases}"
            events.append({"step": step.key, "status": "completed", "summary": step.summary})
            continue

        if step.key == "package":
            review = review_problem(problem)
            review_report = review.to_dict()
            reports["review"] = review_report
            if review_blocks_execution(review):
                _remove_package_artifacts(package_root, problem.id)
                reports["package"] = {
                    "package_blocked": True,
                    "error": "package blocked by dangerous generated code",
                }
                step.status = "failed"
                step.summary = "导出被阻止：生成代码包含危险本地执行"
                workflow.status = "failed"
                workflow.updated_at = _now()
                events.append({"step": step.key, "status": "failed", "summary": step.summary})
                return workflow, {"events": events, "reports": reports, "problem": problem}
            validation = validate_problem(problem, rounds=30, timeout_seconds=1.0)
            validation_report = validation.to_dict()
            reports["validation"] = validation_report
            if not review.passed or not validation.sample_passed or not validation.fuzz_passed:
                _remove_package_artifacts(package_root, problem.id)
                reports["package"] = {
                    "package_blocked": True,
                    "error": "package blocked by failed review or validation",
                }
                step.status = "failed"
                step.summary = "导出被阻止：审查或验证未通过"
                workflow.status = "failed"
                workflow.updated_at = _now()
                events.append({"step": step.key, "status": "failed", "summary": step.summary})
                return workflow, {"events": events, "reports": reports, "problem": problem}
            package_dir = export_problem_package(problem, package_root, validation, review)
            reports["package"] = {
                "package_blocked": False,
                "package_dir": str(package_dir),
                "download_url": f"/api/problems/{problem.id}/package/download",
            }
            step.status = "completed"
            step.summary = f"已导出到 {package_dir}"
            events.append({"step": step.key, "status": "completed", "summary": step.summary})
            continue

        _complete_step(step, problem)
        events.append({"step": step.key, "status": "completed", "summary": step.summary})


def apply_problem_patch(problem: GeneratedProblem, patch: dict) -> GeneratedProblem:
    allowed = {
        "title",
        "statement",
        "input_format",
        "output_format",
        "constraints",
        "samples",
        "tags",
        "solution_explanation",
        "reference_solution",
        "brute_force_solution",
        "generator_code",
    }
    string_fields = {
        "title",
        "statement",
        "input_format",
        "output_format",
        "solution_explanation",
        "reference_solution",
        "brute_force_solution",
        "generator_code",
    }
    list_fields = {"constraints", "tags"}
    updates = {}
    for key, value in patch.items():
        if key not in allowed:
            raise ValueError(f"patch contains unsupported field: {key}")
        if key in string_fields:
            updates[key] = _require_string(key, value)
        elif key in list_fields:
            updates[key] = _normalize_string_list(key, value)
        elif key == "samples":
            updates[key] = _normalize_samples(value)
    for key, value in updates.items():
        setattr(problem, key, value)
    return problem


def _require_string(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _normalize_string_list(field: str, value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    normalized = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field} items must be strings")
        item = item.strip()
        if item:
            normalized.append(item)
    return normalized


def _normalize_samples(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("samples must be a list")
    samples = []
    for index, sample in enumerate(value, 1):
        if not isinstance(sample, dict):
            raise ValueError(f"samples[{index}] must be an object")
        if "input" not in sample or "output" not in sample:
            raise ValueError(f"samples[{index}] must include input and output")
        samples.append(
            {
                "input": _require_string(f"samples[{index}].input", sample["input"]),
                "output": _require_string(f"samples[{index}].output", sample["output"]),
            }
        )
    return samples


def _first_incomplete_step(workflow: ProblemWorkflow) -> WorkflowStep | None:
    for step in workflow.steps:
        if step.status not in {"completed"}:
            return step
    return None


def _current_step(workflow: ProblemWorkflow) -> WorkflowStep | None:
    for step in workflow.steps:
        if step.key == workflow.current_step:
            return step
    return None


def _complete_step(step: WorkflowStep, problem: GeneratedProblem) -> None:
    step.status = "completed"
    step.summary = _step_summary(step.key, problem)


def _step_summary(key: str, problem: GeneratedProblem) -> str:
    if key == "idea":
        return f"方向：{problem.topic} / {problem.difficulty}"
    if key == "statement":
        return f"题面：{problem.title}"
    if key == "constraints":
        return f"约束 {len(problem.constraints)} 条，样例 {len(problem.samples)} 组"
    if key == "solutions":
        return "标准解和暴力解已生成"
    if key == "generator":
        return "测试数据生成器已生成"
    return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remove_package_artifacts(package_root, problem_id: str) -> None:
    root = Path(package_root)
    raw_package_dir = _package_root_child(root, problem_id)
    raw_archive_path = _package_root_child(root, f"{problem_id}.zip")
    package_dir = resolve_under(root, problem_id)
    archive_path = resolve_under(root, f"{problem_id}.zip")
    if raw_package_dir is not None and raw_package_dir.is_symlink():
        raw_package_dir.unlink()
    elif package_dir is not None and package_dir.is_dir():
        shutil.rmtree(package_dir)
    if raw_archive_path is not None and raw_archive_path.is_symlink():
        raw_archive_path.unlink()
    elif archive_path is not None and archive_path.exists():
        archive_path.unlink()


def _package_root_child(root: Path, name: str) -> Path | None:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return None
    return root / name

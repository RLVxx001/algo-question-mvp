from __future__ import annotations

from datetime import datetime, timezone

from app.exporter import export_problem_package
from app.generator import generate_workflow_stage
from app.models import GeneratedProblem, ProblemRequest, ProblemWorkflow, WorkflowStep
from app.reviewer import review_problem
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


def create_workflow(problem: GeneratedProblem, req: ProblemRequest, manual_steps: list[str] | None = None) -> ProblemWorkflow:
    manual = set(manual_steps or ["statement"])
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
            validation = validate_problem(problem, rounds=30, timeout_seconds=1.0)
            review = review_problem(problem)
            package_dir = export_problem_package(problem, package_root, validation, review)
            reports["review"] = review.to_dict()
            reports["validation"] = validation.to_dict()
            reports["package"] = {"package_dir": str(package_dir)}
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
    for key, value in patch.items():
        if key in allowed:
            setattr(problem, key, value)
    return problem


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

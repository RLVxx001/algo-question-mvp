from __future__ import annotations

from app.models import GeneratedProblem, ReviewIssue, ReviewReport


def review_problem(problem: GeneratedProblem) -> ReviewReport:
    issues: list[ReviewIssue] = []
    checks: list[str] = []

    _require_text(problem.title, "title", issues)
    _require_text(problem.statement, "statement", issues)
    _require_text(problem.input_format, "input_format", issues)
    _require_text(problem.output_format, "output_format", issues)
    _require_text(problem.solution_explanation, "solution_explanation", issues)
    checks.append("required text fields checked")

    if len(problem.samples) < 2:
        issues.append(ReviewIssue("error", "samples", "at least two samples are required"))
    for idx, sample in enumerate(problem.samples, 1):
        if not isinstance(sample, dict) or "input" not in sample or "output" not in sample:
            issues.append(ReviewIssue("error", f"samples[{idx}]", "sample must include input and output"))
        elif not str(sample["input"]).strip() or not str(sample["output"]).strip():
            issues.append(ReviewIssue("error", f"samples[{idx}]", "sample input and output cannot be empty"))
    checks.append("sample shape checked")

    if not problem.constraints:
        issues.append(ReviewIssue("error", "constraints", "constraints cannot be empty"))
    if not problem.tags:
        issues.append(ReviewIssue("warn", "tags", "tags are empty; difficulty routing will be weak"))
    checks.append("metadata checked")

    _compile_python(problem.reference_solution, "reference_solution", issues)
    _compile_python(problem.brute_force_solution, "brute_force_solution", issues)
    _compile_python(problem.generator_code, "generator_code", issues)
    checks.append("python code syntax checked")

    searchable = " ".join([problem.title, problem.statement, " ".join(problem.tags)]).lower()
    topic_tokens = [token for token in problem.topic.lower().replace("_", " ").split() if len(token) >= 3]
    if topic_tokens and not any(token in searchable for token in topic_tokens):
        issues.append(ReviewIssue("warn", "topic", "topic is not clearly reflected in title, statement, or tags"))
    checks.append("topic alignment checked")

    if "float" in searchable or "precision" in searchable:
        issues.append(ReviewIssue("warn", "statement", "floating point problems are outside the current MVP comfort zone"))
    if "interactive" in searchable:
        issues.append(ReviewIssue("error", "statement", "interactive problems are not supported by this MVP"))
    checks.append("unsupported problem types checked")

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warn_count = sum(1 for issue in issues if issue.severity == "warn")
    score = max(0, 100 - error_count * 35 - warn_count * 10)
    return ReviewReport(
        problem_id=problem.id,
        passed=error_count == 0,
        score=score,
        issues=issues,
        checks=checks,
    )


def _require_text(value: str, field: str, issues: list[ReviewIssue]) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(ReviewIssue("error", field, "field is required"))


def _compile_python(source: str, field: str, issues: list[ReviewIssue]) -> None:
    if not isinstance(source, str) or not source.strip():
        issues.append(ReviewIssue("error", field, "python source is required"))
        return
    try:
        compile(source, f"<{field}>", "exec")
    except SyntaxError as exc:
        issues.append(ReviewIssue("error", field, f"syntax error: {exc.msg} at line {exc.lineno}"))

from __future__ import annotations

import ast
import operator

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

    _check_dangerous_python(problem.reference_solution, "reference_solution", issues)
    _check_dangerous_python(problem.brute_force_solution, "brute_force_solution", issues)
    _check_dangerous_python(problem.generator_code, "generator_code", issues)
    checks.append("dangerous local-execution patterns checked")

    _check_text_depth(problem, issues)
    _check_constraint_shape(problem.constraints, issues)
    _check_generator_seed(problem.generator_code, issues)
    checks.append("quality heuristics checked")

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


def review_blocks_execution(review: ReviewReport) -> bool:
    return any(
        issue.severity == "error" and "dangerous local-execution" in issue.message
        for issue in review.issues
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


def _check_dangerous_python(source: str, field: str, issues: list[ReviewIssue]) -> None:
    if not isinstance(source, str):
        return
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    dangerous_modules = {"builtins", "os", "socket", "subprocess", "shutil", "requests", "urllib", "importlib", "pathlib"}
    dangerous_calls = {"open", "eval", "exec", "__import__"}
    dangerous_calls = dangerous_calls | _dangerous_callable_aliases(tree, dangerous_calls)
    if any(_has_dangerous_node(node, dangerous_modules, dangerous_calls) for node in ast.walk(tree)):
        issues.append(ReviewIssue("error", field, "dangerous local-execution code is not allowed in this MVP"))


def _has_dangerous_node(node: ast.AST, modules: set[str], calls: set[str]) -> bool:
    if isinstance(node, ast.Import):
        return any(_module_root(alias.name) in modules for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return _module_root(node.module or "") in modules
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id == "getattr" and len(node.args) >= 2:
                return _string_literal(node.args[1]) in calls
            return node.func.id in calls
        if isinstance(node.func, ast.Attribute):
            return node.func.attr in calls
        if isinstance(node.func, ast.Subscript):
            return _subscript_key(node.func) in calls
    return False


def _module_root(name: str) -> str:
    return name.split(".", 1)[0]


def _string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_literal(node.left)
        right = _string_literal(node.right)
        if left is not None and right is not None:
            return operator.add(left, right)
    return None


def _subscript_key(node: ast.Subscript) -> str | None:
    return _string_literal(node.slice)


def _dangerous_callable_aliases(tree: ast.AST, calls: set[str]) -> set[str]:
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        known = calls | aliases
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names = [
                    name
                    for target in node.targets
                    for name in _dangerous_alias_target_names(target, node.value, known)
                ]
            elif isinstance(node, ast.AnnAssign) and node.value:
                names = _dangerous_alias_target_names(node.target, node.value, known)
            else:
                names = []
            for name in names:
                if name not in known:
                    aliases.add(name)
                    changed = True
    return aliases


def _dangerous_alias_target_names(target: ast.AST, value: ast.AST, calls: set[str]) -> list[str]:
    if _is_dangerous_callable_ref(value, calls):
        return _assignment_target_names(target)
    if isinstance(target, ast.Tuple | ast.List) and isinstance(value, ast.Tuple | ast.List):
        return [
            name
            for target_item, value_item in zip(target.elts, value.elts)
            for name in _dangerous_alias_target_names(target_item, value_item, calls)
        ]
    return []


def _assignment_target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple | ast.List):
        return [name for item in node.elts for name in _assignment_target_names(item)]
    return []


def _is_dangerous_callable_ref(node: ast.AST, calls: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in calls
    if isinstance(node, ast.Attribute):
        return node.attr in calls
    if isinstance(node, ast.Subscript):
        return _subscript_key(node) in calls
    return False


def _check_text_depth(problem: GeneratedProblem, issues: list[ReviewIssue]) -> None:
    if len(problem.statement.strip()) < 30:
        issues.append(ReviewIssue("warn", "statement", "statement is very short and may be ambiguous"))
    if len(problem.input_format.strip()) < 12:
        issues.append(ReviewIssue("warn", "input_format", "input format is too short"))
    if len(problem.output_format.strip()) < 12:
        issues.append(ReviewIssue("warn", "output_format", "output format is too short"))
    if len(problem.solution_explanation.strip()) < 30:
        issues.append(ReviewIssue("warn", "solution_explanation", "solution explanation is very short"))


def _check_constraint_shape(constraints: list[str], issues: list[ReviewIssue]) -> None:
    joined = " ".join(constraints)
    if constraints and not any(ch.isdigit() for ch in joined):
        issues.append(ReviewIssue("warn", "constraints", "constraints do not include numeric bounds"))


def _check_generator_seed(source: str, issues: list[ReviewIssue]) -> None:
    if "sys.argv" not in source and "seed" not in source:
        issues.append(ReviewIssue("warn", "generator_code", "generator does not appear to consume a seed argument"))

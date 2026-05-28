from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from app.models import GeneratedProblem, ValidationCaseResult, ValidationReport


class ValidationError(Exception):
    pass


def validate_problem(problem: GeneratedProblem, rounds: int = 100, timeout_seconds: float = 2.0) -> ValidationReport:
    failed: list[ValidationCaseResult] = []
    notes: list[str] = []

    with tempfile.TemporaryDirectory(prefix="algo-problem-") as tmp:
        root = Path(tmp)
        ref_path = root / "reference.py"
        brute_path = root / "brute.py"
        gen_path = root / "generator.py"
        ref_path.write_text(problem.reference_solution, encoding="utf-8")
        brute_path.write_text(problem.brute_force_solution, encoding="utf-8")
        gen_path.write_text(problem.generator_code, encoding="utf-8")

        for idx, sample in enumerate(problem.samples, 1):
            actual = _run_python(ref_path, sample["input"], timeout_seconds)
            expected = sample["output"].strip()
            passed = actual.strip() == expected
            if not passed:
                failed.append(
                    ValidationCaseResult(
                        index=idx,
                        input=sample["input"],
                        expected=expected,
                        actual=actual.strip(),
                        passed=False,
                        reason="sample output mismatch",
                    )
                )

        sample_passed = not failed
        fuzz_passed = True
        for seed in range(rounds):
            try:
                case_input = _run_generator(gen_path, seed, timeout_seconds)
            except ValidationError as exc:
                fuzz_passed = False
                failed.append(
                    ValidationCaseResult(
                        index=seed,
                        input="",
                        expected="",
                        actual="",
                        passed=False,
                        reason=f"生成器失败，seed={seed}: {exc}",
                    )
                )
                break
            try:
                expected = _run_python(brute_path, case_input, timeout_seconds)
            except ValidationError as exc:
                fuzz_passed = False
                failed.append(
                    ValidationCaseResult(
                        index=seed,
                        input=_truncate_case_input(case_input),
                        expected="",
                        actual="",
                        passed=False,
                        reason=f"暴力解失败，seed={seed}: {exc}",
                    )
                )
                break
            try:
                actual = _run_python(ref_path, case_input, timeout_seconds)
            except ValidationError as exc:
                fuzz_passed = False
                failed.append(
                    ValidationCaseResult(
                        index=seed,
                        input=_truncate_case_input(case_input),
                        expected="",
                        actual="",
                        passed=False,
                        reason=f"标准解失败，seed={seed}: {exc}",
                    )
                )
                break
            if actual.strip() != expected.strip():
                fuzz_passed = False
                failed.append(
                    ValidationCaseResult(
                        index=seed,
                        input=case_input,
                        expected=expected.strip(),
                        actual=actual.strip(),
                        passed=False,
                        reason="reference and brute force mismatch",
                    )
                )
                break

    if sample_passed:
        notes.append("all samples matched reference solution")
    if fuzz_passed:
        notes.append(f"reference and brute force matched for {rounds} generated cases")
    if problem.source.startswith("mock"):
        notes.append("problem was generated from local templates")

    return ValidationReport(
        problem_id=problem.id,
        sample_passed=sample_passed,
        fuzz_passed=fuzz_passed,
        total_cases=len(problem.samples) + rounds,
        failed_cases=failed,
        notes=notes,
    )


def _run_generator(path: Path, seed: int, timeout_seconds: float) -> str:
    return _run([sys.executable, str(path), str(seed)], "", timeout_seconds, f"generator.py(seed={seed})")


def _run_python(path: Path, stdin: str, timeout_seconds: float) -> str:
    labels = {
        "reference.py": "reference_solution.py",
        "brute.py": "brute_force_solution.py",
    }
    return _run([sys.executable, str(path)], stdin, timeout_seconds, labels.get(path.name, path.name))


def _run(cmd: list[str], stdin: str, timeout_seconds: float, label: str) -> str:
    try:
        completed = subprocess.run(
            cmd,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError(f"{label} timed out after {timeout_seconds:g}s") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise ValidationError(f"{label} failed: {stderr}" if stderr else f"{label} failed")
    return completed.stdout


def _truncate_case_input(case_input: str, limit: int = 4000) -> str:
    if len(case_input) <= limit:
        return case_input
    return case_input[:limit] + "\n... truncated ..."

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.models import GeneratedProblem, RerunReport, ValidationCaseResult, ValidationReport


class ValidationError(Exception):
    pass


def validate_problem(problem: GeneratedProblem, rounds: int = 100, timeout_seconds: float = 2.0) -> ValidationReport:
    started = time.perf_counter()
    failed: list[ValidationCaseResult] = []
    notes: list[str] = []
    first_failed_seed: int | None = None
    failure_stage: str | None = None

    with tempfile.TemporaryDirectory(prefix="algo-problem-") as tmp:
        root = Path(tmp)
        ref_path = root / "reference.py"
        brute_path = root / "brute.py"
        gen_path = root / "generator.py"
        ref_path.write_text(problem.reference_solution, encoding="utf-8")
        brute_path.write_text(problem.brute_force_solution, encoding="utf-8")
        gen_path.write_text(problem.generator_code, encoding="utf-8")

        for idx, sample in enumerate(problem.samples, 1):
            expected = sample["output"].strip()
            try:
                actual = _run_python(ref_path, sample["input"], timeout_seconds)
            except ValidationError as exc:
                failure_stage = failure_stage or "sample"
                failed.append(
                    ValidationCaseResult(
                        index=idx,
                        input=_truncate_case_input(sample["input"]),
                        expected=_truncate_case_output(expected),
                        actual="",
                        passed=False,
                        reason=f"sample reference failed: {exc}",
                    )
                )
                continue
            passed = actual.strip() == expected
            if not passed:
                failure_stage = failure_stage or "sample"
                failed.append(
                    ValidationCaseResult(
                        index=idx,
                        input=_truncate_case_input(sample["input"]),
                        expected=_truncate_case_output(expected),
                        actual=_truncate_case_output(actual.strip()),
                        passed=False,
                        reason="sample output mismatch",
                    )
                )

        sample_passed = not failed
        fuzz_passed = True
        fuzz_cases_run = 0
        for seed in range(rounds):
            fuzz_cases_run += 1
            try:
                case_input = _run_generator(gen_path, seed, timeout_seconds)
            except ValidationError as exc:
                fuzz_passed = False
                first_failed_seed = seed
                failure_stage = failure_stage or "generator"
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
                first_failed_seed = seed
                failure_stage = failure_stage or "brute_force"
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
                first_failed_seed = seed
                failure_stage = failure_stage or "reference"
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
                first_failed_seed = seed
                failure_stage = failure_stage or "compare"
                failed.append(
                    ValidationCaseResult(
                        index=seed,
                        input=_truncate_case_input(case_input),
                        expected=_truncate_case_output(expected.strip()),
                        actual=_truncate_case_output(actual.strip()),
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
        total_cases=len(problem.samples) + fuzz_cases_run,
        failed_cases=failed,
        notes=notes,
        rounds=rounds,
        timeout_seconds=timeout_seconds,
        sample_count=len(problem.samples),
        duration_ms=int((time.perf_counter() - started) * 1000),
        first_failed_seed=first_failed_seed,
        failure_stage=failure_stage,
    )


def rerun_case(problem: GeneratedProblem, case_input: str, timeout_seconds: float = 2.0) -> RerunReport:
    with tempfile.TemporaryDirectory(prefix="algo-rerun-") as tmp:
        root = Path(tmp)
        ref_path = root / "reference.py"
        brute_path = root / "brute.py"
        ref_path.write_text(problem.reference_solution, encoding="utf-8")
        brute_path.write_text(problem.brute_force_solution, encoding="utf-8")
        try:
            expected = _run_python(brute_path, case_input, timeout_seconds).strip()
        except ValidationError as exc:
            return RerunReport(problem.id, case_input, "", "", False, str(exc), "brute_force")
        try:
            actual = _run_python(ref_path, case_input, timeout_seconds).strip()
        except ValidationError as exc:
            return RerunReport(
                problem.id,
                case_input,
                _truncate_case_output(expected),
                "",
                False,
                str(exc),
                "reference",
            )

    passed = expected == actual
    return RerunReport(
        problem_id=problem.id,
        input=case_input,
        expected=_truncate_case_output(expected),
        actual=_truncate_case_output(actual),
        passed=passed,
        failure_stage=None if passed else "compare",
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
        stderr = _truncate_text(completed.stderr.strip())
        raise ValidationError(f"{label} failed: {stderr}" if stderr else f"{label} failed")
    return completed.stdout


def _truncate_text(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n... truncated ..."


def _truncate_case_input(case_input: str, limit: int = 4000) -> str:
    return _truncate_text(case_input, limit)


def _truncate_case_output(case_output: str, limit: int = 4000) -> str:
    return _truncate_text(case_output, limit)

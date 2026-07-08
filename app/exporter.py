from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.models import GeneratedProblem, ReviewReport, ValidationReport
from app.paths import resolve_under


def export_problem_package(
    problem: GeneratedProblem,
    root: Path,
    validation: ValidationReport,
    review: ReviewReport,
) -> Path:
    raw_package_dir = _direct_package_child(root, problem.id)
    if raw_package_dir is not None and raw_package_dir.is_symlink():
        raw_package_dir.unlink()
    package_dir = resolve_under(root, problem.id)
    if package_dir is None:
        raise ValueError("package path is outside package root")
    package_dir.mkdir(parents=True, exist_ok=True)

    _write_package_file(
        package_dir / "problem.json",
        json.dumps(problem.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_package_file(package_dir / "problem.md", _problem_markdown(problem), encoding="utf-8")
    _write_package_file(package_dir / "reference_solution.py", problem.reference_solution, encoding="utf-8")
    _write_package_file(package_dir / "brute_force_solution.py", problem.brute_force_solution, encoding="utf-8")
    _write_package_file(package_dir / "generator.py", problem.generator_code, encoding="utf-8")
    _write_package_file(
        package_dir / "validation_report.json",
        json.dumps(validation.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_package_file(
        package_dir / "review_report.json",
        json.dumps(review.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_package_file(package_dir / "README.md", _package_readme(problem, validation, review), encoding="utf-8")
    return package_dir


def create_problem_package_archive(problem_id: str, root: Path) -> Path:
    raw_package_dir = _direct_package_child(root, problem_id)
    if raw_package_dir is not None and raw_package_dir.is_symlink():
        raise FileNotFoundError(f"package not found: {problem_id}")
    package_dir = resolve_under(root, problem_id)
    archive_path = resolve_under(root, f"{problem_id}.zip")
    if package_dir is None or not package_dir.is_dir():
        raise FileNotFoundError(f"package not found: {problem_id}")
    if archive_path is None:
        raise ValueError("archive path is outside package root")

    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file() and not path.is_symlink():
                archive.write(path, path.relative_to(package_dir))
    return archive_path


def _write_package_file(path: Path, text: str, encoding: str) -> None:
    if path.is_symlink():
        path.unlink()
    path.write_text(text, encoding=encoding)


def _direct_package_child(root: Path, name: str) -> Path | None:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return None
    return root / name


def _problem_markdown(problem: GeneratedProblem) -> str:
    samples = []
    for idx, sample in enumerate(problem.samples, 1):
        samples.append(
            f"### Sample {idx}\n\nInput:\n\n```text\n{sample['input'].rstrip()}\n```\n\n"
            f"Output:\n\n```text\n{sample['output'].rstrip()}\n```\n"
        )
    constraints = "\n".join(f"- {item}" for item in problem.constraints)
    tags = ", ".join(problem.tags)
    return f"""# {problem.title}

Topic: {problem.topic}
Difficulty: {problem.difficulty}
Tags: {tags}

## Statement

{problem.statement}

## Input Format

{problem.input_format}

## Output Format

{problem.output_format}

## Constraints

{constraints}

## Samples

{chr(10).join(samples)}

## Solution

{problem.solution_explanation}
"""


def _package_readme(problem: GeneratedProblem, validation: ValidationReport, review: ReviewReport) -> str:
    return f"""# Package: {problem.title}

Problem ID: `{problem.id}`

## Files

- `problem.md`: publishable statement and explanation
- `problem.json`: raw generated problem package
- `reference_solution.py`: target solution
- `brute_force_solution.py`: small-data oracle
- `generator.py`: deterministic random input generator
- `validation_report.json`: sample and fuzz verification result
- `review_report.json`: static quality review result

## Verification Summary

- Samples passed: `{validation.sample_passed}`
- Fuzz passed: `{validation.fuzz_passed}`
- Total cases: `{validation.total_cases}`
- Review passed: `{review.passed}`
- Review score: `{review.score}`
"""

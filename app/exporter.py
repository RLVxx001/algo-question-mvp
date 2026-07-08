from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.models import GeneratedProblem, ReviewReport, ValidationReport


def export_problem_package(
    problem: GeneratedProblem,
    root: Path,
    validation: ValidationReport,
    review: ReviewReport,
) -> Path:
    package_dir = root / problem.id
    package_dir.mkdir(parents=True, exist_ok=True)

    (package_dir / "problem.json").write_text(
        json.dumps(problem.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (package_dir / "problem.md").write_text(_problem_markdown(problem), encoding="utf-8")
    (package_dir / "reference_solution.py").write_text(problem.reference_solution, encoding="utf-8")
    (package_dir / "brute_force_solution.py").write_text(problem.brute_force_solution, encoding="utf-8")
    (package_dir / "generator.py").write_text(problem.generator_code, encoding="utf-8")
    (package_dir / "validation_report.json").write_text(
        json.dumps(validation.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (package_dir / "review_report.json").write_text(
        json.dumps(review.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (package_dir / "README.md").write_text(_package_readme(problem, validation, review), encoding="utf-8")
    return package_dir


def create_problem_package_archive(problem_id: str, root: Path) -> Path:
    package_dir = (root / problem_id).resolve()
    archive_path = (root / f"{problem_id}.zip").resolve()
    root_path = root.resolve()
    if not str(package_dir).startswith(str(root_path)) or not package_dir.is_dir():
        raise FileNotFoundError(f"package not found: {problem_id}")
    if not str(archive_path).startswith(str(root_path)):
        raise ValueError("archive path is outside package root")

    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_dir))
    return archive_path


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

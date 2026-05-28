from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.exporter import export_problem_package
from app.generator import generate_problem
from app.models import GeneratedProblem, ProblemRequest
from app.reviewer import review_problem
from app.validator import validate_problem


class AlgorithmQuestionMVPTest(unittest.TestCase):
    def test_mock_problem_review_validate_and_export(self) -> None:
        problem = generate_problem(
            ProblemRequest(
                topic="prefix sum",
                difficulty="easy",
                count=1,
                use_llm=False,
            )
        )

        review = review_problem(problem)
        self.assertTrue(review.passed, review.to_dict())
        self.assertGreaterEqual(review.score, 80)

        validation = validate_problem(problem, rounds=20)
        self.assertTrue(validation.sample_passed, validation.to_dict())
        self.assertTrue(validation.fuzz_passed, validation.to_dict())
        self.assertEqual(validation.failed_cases, [])

        with tempfile.TemporaryDirectory() as tmp:
            package_dir = export_problem_package(problem, Path(tmp), validation, review)
            self.assertTrue((package_dir / "problem.md").exists())
            self.assertTrue((package_dir / "reference_solution.py").exists())
            self.assertTrue((package_dir / "brute_force_solution.py").exists())
            self.assertTrue((package_dir / "generator.py").exists())
            self.assertTrue((package_dir / "validation_report.json").exists())
            self.assertTrue((package_dir / "review_report.json").exists())

    def test_statement_language_defaults_to_chinese_and_can_use_english(self) -> None:
        zh_problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        self.assertEqual(zh_problem.statement_language, "zh")
        self.assertEqual(zh_problem.title, "目标和配对计数")
        self.assertIn("给定", zh_problem.statement)

        en_problem = generate_problem(ProblemRequest(topic="array", statement_language="en", use_llm=False))
        self.assertEqual(en_problem.statement_language, "en")
        self.assertEqual(en_problem.title, "Count Pairs With Target Sum")
        self.assertIn("Given", en_problem.statement)

    def test_legacy_null_statement_language_is_inferred(self) -> None:
        data = generate_problem(ProblemRequest(topic="array", statement_language="en", use_llm=False)).to_dict()
        data["statement_language"] = None
        problem = GeneratedProblem.from_dict(data)
        self.assertEqual(problem.statement_language, "en")


if __name__ == "__main__":
    unittest.main()

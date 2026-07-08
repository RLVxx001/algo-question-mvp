from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from app.exporter import create_problem_package_archive, export_problem_package
from app.generator import generate_problem
from app.models import GeneratedProblem, ProblemRequest
from app.reviewer import review_problem
from app.validator import rerun_case, validate_problem


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

    def test_export_archive_contains_publishable_package_files(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        review = review_problem(problem)
        validation = validate_problem(problem, rounds=3)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_problem_package(problem, root, validation, review)

            archive_path = create_problem_package_archive(problem.id, root)

            self.assertEqual(archive_path.name, f"{problem.id}.zip")
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
            self.assertIn("problem.md", names)
            self.assertIn("problem.json", names)
            self.assertIn("reference_solution.py", names)
            self.assertIn("brute_force_solution.py", names)
            self.assertIn("generator.py", names)
            self.assertIn("validation_report.json", names)
            self.assertIn("review_report.json", names)
            self.assertIn("README.md", names)

    def test_server_package_download_returns_zip_response(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        review = review_problem(problem)
        validation = validate_problem(problem, rounds=3)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_problem_package(problem, root, validation, review)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            store = Mock()
            store.get.return_value = problem
            with patch("app.server.PACKAGE_ROOT", root), patch("app.server.STORE", store):
                handler._download_package(problem.id)

            handler.send_response.assert_called_once_with(200)
            sent_headers = handler.send_header.call_args_list
            self.assertIn(("Content-Type", "application/zip"), [call.args for call in sent_headers])
            self.assertIn(
                ("Content-Disposition", f'attachment; filename="{problem.id}.zip"'),
                [call.args for call in sent_headers],
            )
            body = handler.wfile.write.call_args.args[0]
            self.assertGreater(len(body), 0)
            with zipfile.ZipFile(Path(tmp) / f"{problem.id}.zip") as archive:
                self.assertIn("problem.md", archive.namelist())

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

    def test_validation_report_includes_operational_metadata(self) -> None:
        problem = generate_problem(ProblemRequest(topic="prefix sum", use_llm=False))

        report = validate_problem(problem, rounds=7, timeout_seconds=1.5)

        self.assertEqual(report.rounds, 7)
        self.assertEqual(report.timeout_seconds, 1.5)
        self.assertEqual(report.sample_count, len(problem.samples))
        self.assertGreaterEqual(report.duration_ms, 0)
        self.assertIsNone(report.first_failed_seed)
        self.assertIsNone(report.failure_stage)
        data = report.to_dict()
        self.assertEqual(data["rounds"], 7)
        self.assertIn("duration_ms", data)

    def test_rerun_case_compares_reference_and_bruteforce(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        result = rerun_case(problem, "5 6\n1 5 3 3 2\n", timeout_seconds=1.0)

        self.assertTrue(result.passed, result.to_dict())
        self.assertEqual(result.expected.strip(), result.actual.strip())
        self.assertEqual(result.error, "")

    def test_validation_report_records_first_failed_seed(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.samples = []
        problem.generator_code = "import sys\nprint('2 3')\nprint('1 2')\n"
        problem.reference_solution = "print(0)\n"
        problem.brute_force_solution = "print(1)\n"

        report = validate_problem(problem, rounds=3)

        self.assertFalse(report.fuzz_passed)
        self.assertEqual(report.first_failed_seed, 0)
        self.assertEqual(report.failure_stage, "compare")

    def test_rerun_case_reports_compare_failure(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "print(0)\n"
        problem.brute_force_solution = "print(1)\n"

        result = rerun_case(problem, "2 3\n1 2\n", timeout_seconds=1.0)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_stage, "compare")
        self.assertEqual(result.expected, "1")
        self.assertEqual(result.actual, "0")

    def test_review_flags_dangerous_generated_code(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "import subprocess\nprint('x')\n"

        review = review_problem(problem)

        self.assertFalse(review.passed)
        self.assertTrue(
            any(
                issue.field == "reference_solution" and "dangerous" in issue.message
                for issue in review.issues
            ),
            review.to_dict(),
        )

    def test_review_warns_about_weak_constraints_and_missing_seed(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.constraints = ["values are valid"]
        problem.generator_code = "print('1 1')\nprint('1')\n"

        review = review_problem(problem)

        fields = [issue.field for issue in review.issues]
        self.assertIn("constraints", fields)
        self.assertIn("generator_code", fields)

    def test_new_local_templates_review_and_validate(self) -> None:
        expected_titles = {
            "two pointers": "Count Pairs With Sum At Most K",
            "binary search": "First Position At Least X",
            "stack": "Next Greater Element",
        }
        for topic, title in expected_titles.items():
            with self.subTest(topic=topic):
                problem = generate_problem(ProblemRequest(topic=topic, statement_language="en", use_llm=False))
                self.assertEqual(problem.title, title)
                review = review_problem(problem)
                self.assertTrue(review.passed, review.to_dict())
                validation = validate_problem(problem, rounds=15)
                self.assertTrue(validation.sample_passed, validation.to_dict())
                self.assertTrue(validation.fuzz_passed, validation.to_dict())

    def test_server_clamps_validation_options(self) -> None:
        from app.server import _clamp_rounds, _clamp_timeout

        self.assertEqual(_clamp_rounds(0), 1)
        self.assertEqual(_clamp_rounds(1500), 1000)
        self.assertEqual(_clamp_rounds("12"), 12)
        self.assertEqual(_clamp_timeout(0.01), 0.2)
        self.assertEqual(_clamp_timeout(99), 10.0)
        self.assertEqual(_clamp_timeout("1.5"), 1.5)


if __name__ == "__main__":
    unittest.main()

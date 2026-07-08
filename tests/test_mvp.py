from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from app.exporter import create_problem_package_archive, export_problem_package
from app.generator import generate_problem
from app.models import GeneratedProblem, ProblemRequest
from app.reviewer import review_problem
from app.similarity import find_similar_problems
from app.store import ProblemStore, ReportStore, WorkflowStore
from app.validator import rerun_case, validate_problem
from app.workflow import advance_workflow, apply_problem_patch, create_workflow


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

    def test_runtime_endpoint_reports_llm_configuration_without_secret(self) -> None:
        from app.server import Handler

        handler = object.__new__(Handler)
        handler.wfile = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()

        with patch.dict(
            "os.environ",
            {
                "ALGO_LLM_API_KEY": "secret-value",
                "ALGO_LLM_BASE_URL": "http://llm.local:8318",
                "ALGO_LLM_MODEL": "model-x",
            },
            clear=False,
        ):
            handler._runtime()

        handler.send_response.assert_called_once_with(200)
        payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
        self.assertTrue(payload["llm"]["configured"])
        self.assertEqual(payload["llm"]["base_url"], "http://llm.local:8318")
        self.assertEqual(payload["llm"]["model"], "model-x")
        self.assertNotIn("secret-value", json.dumps(payload))
        self.assertEqual(payload["generation"]["max_count"], 5)
        self.assertEqual(payload["validation"]["max_rounds"], 1000)

    def test_runtime_endpoint_marks_missing_llm_as_template_fallback(self) -> None:
        from app.server import Handler

        handler = object.__new__(Handler)
        handler.wfile = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()

        with patch.dict("os.environ", {"ALGO_LLM_API_KEY": ""}, clear=False):
            handler._runtime()

        payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
        self.assertFalse(payload["llm"]["configured"])
        self.assertEqual(payload["llm"]["active_mode"], "template")
        self.assertEqual(payload["llm"]["fallback_source"], "mock")

    def test_similarity_finds_duplicate_problem_candidates(self) -> None:
        base = generate_problem(ProblemRequest(topic="array", use_llm=False))
        duplicate = generate_problem(ProblemRequest(topic="array", use_llm=False))
        different = generate_problem(ProblemRequest(topic="prefix sum", use_llm=False))

        report = find_similar_problems(base, [duplicate, different], threshold=0.35)

        self.assertEqual(report.problem_id, base.id)
        self.assertTrue(report.has_risk)
        self.assertEqual(len(report.candidates), 1)
        self.assertEqual(report.candidates[0].problem_id, duplicate.id)
        self.assertGreaterEqual(report.candidates[0].score, 0.35)
        self.assertIn("title", report.candidates[0].matched_fields)

    def test_server_similarity_endpoint_excludes_self_and_sorts_candidates(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        duplicate = generate_problem(ProblemRequest(topic="array", use_llm=False))
        unrelated = generate_problem(ProblemRequest(topic="stack", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            store = ProblemStore(Path(tmp) / "problems")
            store.save(problem)
            store.save(duplicate)
            store.save(unrelated)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STORE", store):
                handler._similar(problem.id)

            handler.send_response.assert_called_once_with(200)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["problem_id"], problem.id)
            self.assertTrue(payload["has_risk"])
            self.assertEqual([item["problem_id"] for item in payload["candidates"]], [duplicate.id])

    def test_stores_can_delete_problem_and_workflow_files(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            workflow_store = WorkflowStore(root / "workflows")
            problem_store.save(problem)
            workflow_store.save(workflow)

            self.assertTrue(problem_store.delete(problem.id))
            self.assertTrue(workflow_store.delete(problem.id))
            self.assertFalse(problem_store.path_for(problem.id).exists())
            self.assertFalse(workflow_store.path_for(problem.id).exists())
            self.assertFalse(problem_store.delete(problem.id))
            self.assertFalse(workflow_store.delete(problem.id))

    def test_server_delete_problem_removes_problem_workflow_and_package_artifacts(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False))
        review = review_problem(problem)
        validation = validate_problem(problem, rounds=3)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            workflow_store = WorkflowStore(root / "workflows")
            report_store = ReportStore(root / "reports")
            package_root = root / "packages"
            problem_store.save(problem)
            workflow_store.save(workflow)
            report_store.save_review(problem.id, review.to_dict())
            report_store.save_validation(problem.id, validation.to_dict())
            package_dir = export_problem_package(problem, package_root, validation, review)
            archive_path = create_problem_package_archive(problem.id, package_root)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.WORKFLOW_STORE", workflow_store),
                patch("app.server.REPORT_STORE", report_store),
                patch("app.server.PACKAGE_ROOT", package_root),
            ):
                handler._delete_problem(problem.id)

            handler.send_response.assert_called_once_with(200)
            body = handler.wfile.write.call_args.args[0]
            self.assertIn(b'"deleted": true', body)
            self.assertFalse(problem_store.path_for(problem.id).exists())
            self.assertFalse(workflow_store.path_for(problem.id).exists())
            self.assertFalse(report_store.dir_for(problem.id).exists())
            self.assertFalse(package_dir.exists())
            self.assertFalse(archive_path.exists())

    def test_report_store_persists_review_and_validation_without_package(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        review = review_problem(problem)
        validation = validate_problem(problem, rounds=3)

        with tempfile.TemporaryDirectory() as tmp:
            store = ReportStore(Path(tmp))

            store.save_review(problem.id, review.to_dict())
            store.save_validation(problem.id, validation.to_dict())

            self.assertEqual(store.get_review(problem.id)["problem_id"], problem.id)
            self.assertEqual(store.get_validation(problem.id)["rounds"], 3)
            self.assertTrue(store.delete(problem.id))
            self.assertIsNone(store.get_review(problem.id))
            self.assertIsNone(store.get_validation(problem.id))
            self.assertFalse(store.delete(problem.id))

    def test_server_persists_review_and_validation_reports_before_package_export(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            package_root = root / "packages"
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"rounds": 3, "timeout_seconds": 1.0}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.REPORT_STORE", report_store),
                patch("app.server.PACKAGE_ROOT", package_root),
            ):
                handler._review(problem.id)
                self.assertIsNotNone(report_store.get_review(problem.id))

                handler._validate(problem.id)
                self.assertIsNotNone(report_store.get_validation(problem.id))

                handler._reports(problem.id)

            body = handler.wfile.write.call_args.args[0]
            reports = json.loads(body.decode("utf-8"))
            self.assertTrue(reports["review"]["passed"])
            self.assertTrue(reports["validation"]["fuzz_passed"])
            self.assertIsNone(reports["package"])

    def test_reports_endpoint_marks_blocked_package_from_failed_reports(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "print(0)\n"
        review = review_problem(problem)
        validation = validate_problem(problem, rounds=3)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            package_root = root / "packages"
            problem_store.save(problem)
            report_store.save_review(problem.id, review.to_dict())
            report_store.save_validation(problem.id, validation.to_dict())

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.REPORT_STORE", report_store),
                patch("app.server.PACKAGE_ROOT", package_root),
            ):
                handler._reports(problem.id)

            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertIsNotNone(payload["package"])
            self.assertTrue(payload["package"]["package_blocked"])
            self.assertEqual(payload["package"]["error"], "package blocked by failed review or validation")
            self.assertTrue(payload["review"]["passed"])
            self.assertFalse(payload["validation"]["sample_passed"])

    def test_server_package_rejects_failed_validation_without_exporting(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "print(0)\n"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            package_root = root / "packages"
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"rounds": 3, "timeout_seconds": 1.0}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.REPORT_STORE", report_store),
                patch("app.server.PACKAGE_ROOT", package_root),
            ):
                handler._package(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertTrue(payload["package_blocked"])
            self.assertFalse(payload["validation"]["sample_passed"])
            self.assertTrue(payload["review"]["passed"])
            self.assertIsNotNone(report_store.get_review(problem.id))
            self.assertIsNotNone(report_store.get_validation(problem.id))
            self.assertFalse((package_root / problem.id).exists())

    def test_server_package_rejects_failed_review_without_exporting(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.samples = []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            package_root = root / "packages"
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"rounds": 3, "timeout_seconds": 1.0}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.REPORT_STORE", report_store),
                patch("app.server.PACKAGE_ROOT", package_root),
            ):
                handler._package(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertTrue(payload["package_blocked"])
            self.assertTrue(payload["validation"]["fuzz_passed"])
            self.assertFalse(payload["review"]["passed"])
            self.assertFalse((package_root / problem.id).exists())

    def test_workflow_package_step_rejects_failed_validation_without_exporting(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "print(0)\n"
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=[])
        for step in workflow.steps:
            if step.key != "package":
                step.status = "completed"
        workflow.current_step = "package"

        with tempfile.TemporaryDirectory() as tmp:
            package_root = Path(tmp) / "packages"

            workflow, result = advance_workflow(workflow, problem, package_root)

            package_step = next(step for step in workflow.steps if step.key == "package")
            self.assertEqual(workflow.status, "failed")
            self.assertEqual(package_step.status, "failed")
            self.assertTrue(result["reports"]["package"]["package_blocked"])
            self.assertFalse(result["reports"]["validation"]["sample_passed"])
            self.assertTrue(result["reports"]["review"]["passed"])
            self.assertFalse((package_root / problem.id).exists())

    def test_workflow_package_step_rejects_failed_review_without_exporting(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.samples = []
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=[])
        for step in workflow.steps:
            if step.key != "package":
                step.status = "completed"
        workflow.current_step = "package"

        with tempfile.TemporaryDirectory() as tmp:
            package_root = Path(tmp) / "packages"

            workflow, result = advance_workflow(workflow, problem, package_root)

            package_step = next(step for step in workflow.steps if step.key == "package")
            self.assertEqual(workflow.status, "failed")
            self.assertEqual(package_step.status, "failed")
            self.assertTrue(result["reports"]["package"]["package_blocked"])
            self.assertTrue(result["reports"]["validation"]["fuzz_passed"])
            self.assertFalse(result["reports"]["review"]["passed"])
            self.assertFalse((package_root / problem.id).exists())

    def test_server_edit_invalidates_reports_and_package_artifacts(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        review = review_problem(problem)
        validation = validate_problem(problem, rounds=3)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            package_root = root / "packages"
            problem_store.save(problem)
            report_store.save_review(problem.id, review.to_dict())
            report_store.save_validation(problem.id, validation.to_dict())
            package_dir = export_problem_package(problem, package_root, validation, review)
            archive_path = create_problem_package_archive(problem.id, package_root)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"patch": {"title": "Edited Title"}}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.REPORT_STORE", report_store),
                patch("app.server.PACKAGE_ROOT", package_root),
            ):
                handler._edit_problem(problem.id)

            edited = problem_store.get(problem.id)
            self.assertEqual(edited.title, "Edited Title")
            self.assertIsNone(report_store.get_review(problem.id))
            self.assertIsNone(report_store.get_validation(problem.id))
            self.assertFalse(package_dir.exists())
            self.assertFalse(archive_path.exists())
            body = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertTrue(body["reports_invalidated"])
            self.assertTrue(body["package_invalidated"])

    def test_problem_patch_normalizes_samples_and_solution_code(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        edited = apply_problem_patch(
            problem,
            {
                "samples": [
                    {"input": 123, "output": 456, "note": "ignored"},
                    {"input": "7 8\n", "output": "15\n"},
                ],
                "reference_solution": 100,
            },
        )

        self.assertEqual(
            edited.samples,
            [
                {"input": "123", "output": "456"},
                {"input": "7 8\n", "output": "15\n"},
            ],
        )
        self.assertEqual(edited.reference_solution, "100")

    def test_server_edit_rejects_invalid_samples_without_saving(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        original_samples = list(problem.samples)

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"patch": {"samples": [{"input": "1 2\n"}]}}

            with patch("app.server.STORE", problem_store):
                handler._edit_problem(problem.id)

            handler.send_response.assert_called_once_with(400)
            self.assertEqual(problem_store.get(problem.id).samples, original_samples)
            body = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertIn("samples", body["error"])

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

from __future__ import annotations

import io
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

    def test_export_archive_rejects_sibling_directory_with_same_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_root = root / "packages"
            package_root.mkdir()
            sibling_package = root / "packages_evil" / "prob_x"
            sibling_package.mkdir(parents=True)
            (sibling_package / "problem.md").write_text("secret", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                create_problem_package_archive("../packages_evil/prob_x", package_root)

            self.assertFalse((root / "packages_evil" / "prob_x.zip").exists())

    def test_export_package_rejects_sibling_directory_with_same_prefix(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.id = "../packages_evil/prob_x"
        review = review_problem(problem)
        validation = validate_problem(problem, rounds=3)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_root = root / "packages"
            package_root.mkdir()

            with self.assertRaises(ValueError):
                export_problem_package(problem, package_root, validation, review)

            self.assertFalse((root / "packages_evil" / "prob_x").exists())

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

    def test_server_static_rejects_sibling_directory_with_same_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static_root = root / "static"
            static_root.mkdir()
            sibling_root = root / "static_evil"
            sibling_root.mkdir()
            (sibling_root / "secret.txt").write_text("secret", encoding="utf-8")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STATIC_ROOT", static_root):
                handler._static("../static_evil/secret.txt")

            handler.send_response.assert_called_once_with(404)
            self.assertIn(b"static file not found", handler.wfile.write.call_args.args[0])

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

    def test_server_port_parser_accepts_valid_integer_ports(self) -> None:
        from app.server import _parse_server_port

        self.assertEqual(_parse_server_port("18081"), 18081)
        self.assertEqual(_parse_server_port(" 8080 "), 8080)
        self.assertEqual(_parse_server_port("65535"), 65535)

    def test_server_port_parser_rejects_invalid_ports(self) -> None:
        from app.server import _parse_server_port

        cases = [
            ("many", "ALGO_PORT must be an integer"),
            ("1.5", "ALGO_PORT must be an integer"),
            ("0", "ALGO_PORT must be between 1 and 65535"),
            ("65536", "ALGO_PORT must be between 1 and 65535"),
        ]
        for value, message in cases:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, message):
                    _parse_server_port(value)

    def test_main_reports_invalid_port_configuration_without_traceback(self) -> None:
        from app.server import main

        with patch.dict("os.environ", {"ALGO_PORT": "many"}, clear=False):
            with self.assertRaisesRegex(SystemExit, "ALGO_PORT must be an integer"):
                main()

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

    def test_problem_store_list_ignores_invalid_problem_files(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            store = ProblemStore(Path(tmp) / "problems")
            store.save(problem)
            (store.root / "broken.json").write_text("{bad", encoding="utf-8")
            (store.root / "not_object.json").write_text("[]", encoding="utf-8")
            (store.root / "missing_fields.json").write_text(json.dumps({"id": "prob_bad"}), encoding="utf-8")

            self.assertEqual([item.id for item in store.list()], [problem.id])

    def test_server_problem_list_ignores_invalid_problem_files(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            store = ProblemStore(Path(tmp) / "problems")
            store.save(problem)
            (store.root / "broken.json").write_text("{bad", encoding="utf-8")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.path = "/api/problems"
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STORE", store):
                handler.do_GET()

            handler.send_response.assert_called_once_with(200)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual([item["id"] for item in payload["list"]], [problem.id])

    def test_problem_store_get_treats_invalid_problem_file_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProblemStore(Path(tmp) / "problems")
            store.path_for("prob_bad_json").write_text("{bad", encoding="utf-8")
            store.path_for("prob_not_object").write_text("[]", encoding="utf-8")
            store.path_for("prob_missing_fields").write_text(json.dumps({"id": "prob_missing_fields"}), encoding="utf-8")

            for problem_id in ["prob_bad_json", "prob_not_object", "prob_missing_fields"]:
                with self.subTest(problem_id=problem_id):
                    with self.assertRaises(KeyError):
                        store.get(problem_id)

    def test_server_problem_detail_returns_not_found_for_invalid_problem_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProblemStore(Path(tmp) / "problems")
            store.path_for("prob_bad").write_text("{bad", encoding="utf-8")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.path = "/api/problems/prob_bad"
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STORE", store):
                handler.do_GET()

            handler.send_response.assert_called_once_with(404)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "problem not found")

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

    def test_workflow_store_get_treats_invalid_json_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp) / "workflows")
            store.path_for("prob_bad").write_text("{bad", encoding="utf-8")

            with self.assertRaises(KeyError):
                store.get("prob_bad")

    def test_server_workflow_endpoint_returns_not_found_for_invalid_workflow_file(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflow_store = WorkflowStore(root / "workflows")
            workflow_store.path_for(problem.id).write_text("{bad", encoding="utf-8")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.WORKFLOW_STORE", workflow_store):
                handler._workflow(problem.id)

            handler.send_response.assert_called_once_with(404)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "workflow not found")

    def test_stores_reject_problem_ids_that_would_sanitize_to_existing_files(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.id = "probx"
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False))
        review = review_problem(problem)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            workflow_store = WorkflowStore(root / "workflows")
            report_store = ReportStore(root / "reports")
            problem_store.save(problem)
            workflow_store.save(workflow)
            report_store.save_review(problem.id, review.to_dict())

            with self.assertRaises(KeyError):
                problem_store.get("prob!x")
            with self.assertRaises(KeyError):
                workflow_store.get("prob!x")
            self.assertFalse(problem_store.delete("prob!x"))
            self.assertFalse(workflow_store.delete("prob!x"))
            self.assertIsNone(report_store.get_review("prob!x"))
            self.assertFalse(report_store.delete("prob!x"))
            self.assertTrue(problem_store.path_for(problem.id).exists())
            self.assertTrue(workflow_store.path_for(problem.id).exists())
            self.assertTrue(report_store.dir_for(problem.id).exists())

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

    def test_server_remove_package_artifacts_rejects_sibling_directory_with_same_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_root = root / "packages"
            package_root.mkdir()
            sibling_dir = root / "packages_evil" / "prob_x"
            sibling_dir.mkdir(parents=True)
            sibling_archive = root / "packages_evil" / "prob_x.zip"
            sibling_archive.write_text("zip", encoding="utf-8")

            from app.server import _remove_package_artifacts

            with patch("app.server.PACKAGE_ROOT", package_root):
                removed = _remove_package_artifacts("../packages_evil/prob_x")

            self.assertFalse(removed)
            self.assertTrue(sibling_dir.exists())
            self.assertTrue(sibling_archive.exists())

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

    def test_report_store_ignores_invalid_json_report_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ReportStore(Path(tmp))
            report_dir = store.dir_for("prob_bad")
            report_dir.mkdir(parents=True)
            (report_dir / "review_report.json").write_text("{bad", encoding="utf-8")
            (report_dir / "validation_report.json").write_text("[]", encoding="utf-8")

            self.assertIsNone(store.get_review("prob_bad"))
            self.assertIsNone(store.get_validation("prob_bad"))

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

    def test_reports_endpoint_ignores_invalid_package_report_files(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            package_root = root / "packages"
            package_dir = package_root / problem.id
            package_dir.mkdir(parents=True)
            (package_dir / "review_report.json").write_text("{bad", encoding="utf-8")
            (package_dir / "validation_report.json").write_text("[]", encoding="utf-8")
            problem_store.save(problem)

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

            handler.send_response.assert_called_once_with(200)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertIsNone(payload["review"])
            self.assertIsNone(payload["validation"])
            self.assertFalse(payload["package"]["package_blocked"])
            self.assertEqual(payload["package"]["download_url"], f"/api/problems/{problem.id}/package/download")

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

    def test_server_package_blocks_dangerous_code_before_validation(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "executed.txt"
            problem.samples = [{"input": "", "output": "ok\n"}]
            problem.reference_solution = f"open({str(marker)!r}, 'w').write('ran')\nprint('ok')\n"
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
            self.assertEqual(payload["error"], "package blocked by dangerous generated code")
            self.assertIsNone(payload["validation"])
            self.assertFalse(payload["review"]["passed"])
            self.assertFalse(marker.exists())
            self.assertIsNotNone(report_store.get_review(problem.id))
            self.assertIsNone(report_store.get_validation(problem.id))
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

    def test_workflow_validate_step_blocks_dangerous_code_before_execution(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=[])
        for step in workflow.steps:
            if step.key in {"idea", "statement", "constraints", "solutions", "generator", "review"}:
                step.status = "completed"
            if step.key == "package":
                step.status = "completed"
        workflow.current_step = "validate"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "executed.txt"
            problem.samples = [{"input": "", "output": "ok\n"}]
            problem.reference_solution = f"open({str(marker)!r}, 'w').write('ran')\nprint('ok')\n"

            workflow, result = advance_workflow(workflow, problem, root / "packages")

            validate_step = next(step for step in workflow.steps if step.key == "validate")
            self.assertEqual(workflow.status, "failed")
            self.assertEqual(validate_step.status, "failed")
            self.assertIn("review", result["reports"])
            self.assertNotIn("validation", result["reports"])
            self.assertFalse(result["reports"]["review"]["passed"])
            self.assertFalse(marker.exists())

    def test_workflow_package_step_blocks_dangerous_code_before_validation(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=[])
        for step in workflow.steps:
            if step.key != "package":
                step.status = "completed"
        workflow.current_step = "package"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "executed.txt"
            package_root = root / "packages"
            problem.samples = [{"input": "", "output": "ok\n"}]
            problem.reference_solution = f"open({str(marker)!r}, 'w').write('ran')\nprint('ok')\n"

            workflow, result = advance_workflow(workflow, problem, package_root)

            package_step = next(step for step in workflow.steps if step.key == "package")
            self.assertEqual(workflow.status, "failed")
            self.assertEqual(package_step.status, "failed")
            self.assertTrue(result["reports"]["package"]["package_blocked"])
            self.assertEqual(result["reports"]["package"]["error"], "package blocked by dangerous generated code")
            self.assertIn("review", result["reports"])
            self.assertNotIn("validation", result["reports"])
            self.assertFalse(result["reports"]["review"]["passed"])
            self.assertFalse(marker.exists())
            self.assertFalse((package_root / problem.id).exists())

    def test_create_workflow_rejects_unknown_manual_steps(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with self.assertRaisesRegex(ValueError, "manual_steps contains unsupported step: unknown"):
            create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=["statement", "unknown"])

    def test_create_workflow_rejects_non_interactive_manual_steps(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with self.assertRaisesRegex(ValueError, "manual_steps contains unsupported step: package"):
            create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=["package"])

    def test_create_workflow_respects_empty_manual_steps(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=[])

        self.assertTrue(all(step.mode == "auto" for step in workflow.steps))

    def test_server_workflow_rejects_unknown_manual_steps_without_saving_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {
                "topic": "array",
                "use_llm": False,
                "manual_steps": ["unknown"],
            }

            with patch("app.server.STORE", problem_store):
                handler._start_workflow()

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "manual_steps contains unsupported step: unknown")
            self.assertEqual(problem_store.list(), [])

    def test_server_workflow_rejects_non_list_manual_steps_without_saving_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {
                "topic": "array",
                "use_llm": False,
                "manual_steps": "statement",
            }

            with patch("app.server.STORE", problem_store):
                handler._start_workflow()

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "manual_steps must be a list of step names")
            self.assertEqual(problem_store.list(), [])

    def test_server_workflow_validates_manual_steps_before_creating_draft(self) -> None:
        from app.server import Handler

        handler = object.__new__(Handler)
        handler.wfile = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._read_json = lambda default=None: {
            "topic": "array",
            "use_llm": True,
            "manual_steps": "statement",
        }

        with patch("app.server.create_problem_draft") as create_draft:
            handler._start_workflow()

        create_draft.assert_not_called()
        handler.send_response.assert_called_once_with(400)
        payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
        self.assertEqual(payload["error"], "manual_steps must be a list of step names")

    def test_server_generate_rejects_fractional_count_without_saving_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {
                "topic": "array",
                "count": 1.5,
                "use_llm": False,
            }

            with patch("app.server.STORE", problem_store):
                handler._generate()

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "count must be an integer")
            self.assertEqual(problem_store.list(), [])

    def test_server_generate_rejects_non_string_topic_without_saving_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {
                "topic": ["array"],
                "count": 1,
                "use_llm": False,
            }

            with patch("app.server.STORE", problem_store):
                handler._generate()

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "topic must be a string")
            self.assertEqual(problem_store.list(), [])

    def test_server_generate_rejects_missing_topic_without_saving_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {
                "count": 1,
                "use_llm": False,
            }

            with patch("app.server.STORE", problem_store):
                handler._generate()

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "topic is required")
            self.assertEqual(problem_store.list(), [])

    def test_server_validate_rejects_fractional_rounds_without_saving_report(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"rounds": 2.5, "timeout_seconds": 1.0}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.REPORT_STORE", report_store),
            ):
                handler._validate(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "rounds must be an integer")
            self.assertIsNone(report_store.get_validation(problem.id))

    def test_server_workflow_continue_parses_string_false_confirmation(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=["statement"])
        workflow, result = advance_workflow(workflow, problem, Path(tempfile.gettempdir()) / "packages")
        problem = result["problem"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            workflow_store = WorkflowStore(root / "workflows")
            problem_store.save(problem)
            workflow_store.save(workflow)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"confirm_current": "false"}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.WORKFLOW_STORE", workflow_store),
                patch("app.server.PACKAGE_ROOT", root / "packages"),
            ):
                handler._continue_workflow(problem.id)

            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["workflow"]["status"], "waiting_user")
            self.assertEqual(payload["workflow"]["current_step"], "statement")
            statement_step = next(step for step in payload["workflow"]["steps"] if step["key"] == "statement")
            self.assertEqual(statement_step["status"], "waiting_user")

    def test_server_workflow_continue_rejects_invalid_confirmation_flag(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=["statement"])
        workflow, result = advance_workflow(workflow, problem, Path(tempfile.gettempdir()) / "packages")
        problem = result["problem"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            workflow_store = WorkflowStore(root / "workflows")
            problem_store.save(problem)
            workflow_store.save(workflow)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"confirm_current": "later"}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.WORKFLOW_STORE", workflow_store),
            ):
                handler._continue_workflow(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "confirm_current must be a boolean")

    def test_server_workflow_continue_rejects_non_object_patch_without_advancing(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=["statement"])
        workflow, result = advance_workflow(workflow, problem, Path(tempfile.gettempdir()) / "packages")
        problem = result["problem"]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            workflow_store = WorkflowStore(root / "workflows")
            problem_store.save(problem)
            workflow_store.save(workflow)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"confirm_current": True, "patch": "bad"}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.WORKFLOW_STORE", workflow_store),
                patch("app.server.PACKAGE_ROOT", root / "packages"),
            ):
                handler._continue_workflow(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "patch must be an object")
            stored_workflow = workflow_store.get(problem.id)
            self.assertEqual(stored_workflow.status, "waiting_user")
            self.assertEqual(stored_workflow.current_step, "statement")

    def test_server_workflow_continue_patch_invalidates_reports_and_package_artifacts(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        workflow = create_workflow(problem, ProblemRequest(topic="array", use_llm=False), manual_steps=["statement"])
        workflow, result = advance_workflow(workflow, problem, Path(tempfile.gettempdir()) / "packages")
        problem = result["problem"]
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
            handler._read_json = lambda default=None: {
                "confirm_current": "false",
                "patch": {"title": f"{problem.title} edited"},
            }

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.WORKFLOW_STORE", workflow_store),
                patch("app.server.REPORT_STORE", report_store),
                patch("app.server.PACKAGE_ROOT", package_root),
            ):
                handler._continue_workflow(problem.id)

            self.assertEqual(problem_store.get(problem.id).title, f"{problem.title} edited")
            self.assertIsNone(report_store.get_review(problem.id))
            self.assertIsNone(report_store.get_validation(problem.id))
            self.assertFalse(package_dir.exists())
            self.assertFalse(archive_path.exists())
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertTrue(payload["changed"])
            self.assertTrue(payload["reports_invalidated"])
            self.assertTrue(payload["package_invalidated"])

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

    def test_server_edit_noop_keeps_reports_and_package_artifacts(self) -> None:
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
            handler._read_json = lambda default=None: {"patch": {"title": problem.title}}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.REPORT_STORE", report_store),
                patch("app.server.PACKAGE_ROOT", package_root),
            ):
                handler._edit_problem(problem.id)

            self.assertIsNotNone(report_store.get_review(problem.id))
            self.assertIsNotNone(report_store.get_validation(problem.id))
            self.assertTrue(package_dir.exists())
            self.assertTrue(archive_path.exists())
            body = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertFalse(body["changed"])
            self.assertFalse(body["reports_invalidated"])
            self.assertFalse(body["package_invalidated"])

    def test_problem_patch_normalizes_samples_and_solution_code(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        edited = apply_problem_patch(
            problem,
            {
                "samples": [
                    {"input": "123", "output": "456", "note": "ignored"},
                    {"input": "7 8\n", "output": "15\n"},
                ],
                "reference_solution": "print(100)\n",
            },
        )

        self.assertEqual(
            edited.samples,
            [
                {"input": "123", "output": "456"},
                {"input": "7 8\n", "output": "15\n"},
            ],
        )
        self.assertEqual(edited.reference_solution, "print(100)\n")

    def test_problem_patch_rejects_non_string_edit_values(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        cases = [
            ({"title": 123}, "title must be a string"),
            ({"constraints": ["1 <= n <= 10", 100]}, "constraints items must be strings"),
            ({"tags": ["array", {"bad": True}]}, "tags items must be strings"),
            ({"samples": [{"input": "1 2\n", "output": 3}]}, r"samples\[1\]\.output must be a string"),
        ]
        for patch, message in cases:
            with self.subTest(patch=patch):
                with self.assertRaisesRegex(ValueError, message):
                    apply_problem_patch(problem, patch)

    def test_problem_patch_rejects_unknown_fields(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with self.assertRaisesRegex(ValueError, "patch contains unsupported field: unknown"):
            apply_problem_patch(problem, {"unknown": "value"})

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

    def test_server_edit_rejects_non_string_patch_values_without_saving(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        original_title = problem.title

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"patch": {"title": 123}}

            with patch("app.server.STORE", problem_store):
                handler._edit_problem(problem.id)

            handler.send_response.assert_called_once_with(400)
            self.assertEqual(problem_store.get(problem.id).title, original_title)
            body = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(body["error"], "title must be a string")

    def test_server_edit_rejects_non_object_body_without_saving(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        original_title = problem.title

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            body = b'["bad"]'
            handler = object.__new__(Handler)
            handler.headers = {"Content-Length": str(len(body))}
            handler.rfile = io.BytesIO(body)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STORE", problem_store):
                handler._edit_problem(problem.id)

            handler.send_response.assert_called_once_with(400)
            self.assertEqual(problem_store.get(problem.id).title, original_title)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "JSON body must be an object")

    def test_server_edit_rejects_unknown_patch_fields_without_saving(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        original_title = problem.title

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"patch": {"unknown": "value"}}

            with patch("app.server.STORE", problem_store):
                handler._edit_problem(problem.id)

            handler.send_response.assert_called_once_with(400)
            self.assertEqual(problem_store.get(problem.id).title, original_title)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "patch contains unsupported field: unknown")

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

    def test_llm_problem_payload_rejects_non_list_structured_fields(self) -> None:
        from app.generator import _problem_from_payload

        payload = generate_problem(ProblemRequest(topic="array", statement_language="en", use_llm=False)).to_dict()
        payload["constraints"] = "1 <= n <= 10"

        with self.assertRaisesRegex(ValueError, "constraints must be a list of strings"):
            _problem_from_payload(ProblemRequest(topic="array", statement_language="en"), payload, "llm")

    def test_llm_stage_patch_rejects_non_list_structured_fields(self) -> None:
        from app.generator import _apply_stage_patch

        problem = generate_problem(ProblemRequest(topic="array", statement_language="en", use_llm=False))

        with self.assertRaisesRegex(ValueError, "samples must be a list of objects"):
            _apply_stage_patch(problem, {"samples": "not a sample list"}, "llm")

    def test_llm_stage_patch_does_not_partially_apply_invalid_patch(self) -> None:
        from app.generator import _apply_stage_patch

        problem = generate_problem(ProblemRequest(topic="array", statement_language="en", use_llm=False))
        original_title = problem.title
        original_source = problem.source

        with self.assertRaisesRegex(ValueError, "samples must be a list of objects"):
            _apply_stage_patch(problem, {"title": "Partially Applied Title", "samples": "bad"}, "llm")

        self.assertEqual(problem.title, original_title)
        self.assertEqual(problem.source, original_source)

    def test_llm_json_parser_extracts_object_from_surrounding_text(self) -> None:
        from app.generator import _parse_json_object

        parsed = _parse_json_object('Here is the problem JSON:\n{"title": "A", "tags": ["array"]}\nDone.')

        self.assertEqual(parsed, {"title": "A", "tags": ["array"]})

    def test_llm_json_parser_accepts_fenced_object_with_trailing_text(self) -> None:
        from app.generator import _parse_json_object

        parsed = _parse_json_object('```json\n{"title": "A", "tags": ["array"]}\n```\nHope this helps.')

        self.assertEqual(parsed, {"title": "A", "tags": ["array"]})

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
        self.assertEqual(report.total_cases, 1)

    def test_validation_report_records_sample_reference_runtime_failure(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "raise RuntimeError('boom')\n"

        report = validate_problem(problem, rounds=0, timeout_seconds=1.0)

        self.assertFalse(report.sample_passed)
        self.assertEqual(report.failure_stage, "sample")
        self.assertEqual(report.failed_cases[0].index, 1)
        self.assertIn("sample reference failed", report.failed_cases[0].reason)
        self.assertIn("RuntimeError", report.failed_cases[0].reason)

    def test_validation_report_truncates_sample_runtime_failure_payloads(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.samples = [{"input": "i" * 6000, "output": "o" * 6000}]
        problem.reference_solution = "raise RuntimeError('boom')\n"

        report = validate_problem(problem, rounds=0, timeout_seconds=1.0)

        failed = report.failed_cases[0]
        self.assertIn("sample reference failed", failed.reason)
        self.assertLessEqual(len(failed.input), 4300)
        self.assertLessEqual(len(failed.expected), 4300)
        self.assertIn("... truncated ...", failed.input)
        self.assertIn("... truncated ...", failed.expected)

    def test_validation_report_truncates_long_runtime_stderr(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "import sys\nsys.stderr.write('x' * 6000)\nraise SystemExit(1)\n"

        report = validate_problem(problem, rounds=0, timeout_seconds=1.0)

        reason = report.failed_cases[0].reason
        self.assertLessEqual(len(reason), 4300)
        self.assertIn("... truncated ...", reason)

    def test_validation_report_truncates_long_mismatch_outputs(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "print('x' * 6000)\n"

        report = validate_problem(problem, rounds=0, timeout_seconds=1.0)

        failed = report.failed_cases[0]
        self.assertEqual(failed.reason, "sample output mismatch")
        self.assertLessEqual(len(failed.actual), 4300)
        self.assertIn("... truncated ...", failed.actual)

    def test_rerun_case_reports_compare_failure(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "print(0)\n"
        problem.brute_force_solution = "print(1)\n"

        result = rerun_case(problem, "2 3\n1 2\n", timeout_seconds=1.0)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_stage, "compare")
        self.assertEqual(result.expected, "1")
        self.assertEqual(result.actual, "0")

    def test_rerun_case_truncates_long_mismatch_outputs(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.brute_force_solution = "print('e' * 6000)\n"
        problem.reference_solution = "print('a' * 6000)\n"

        result = rerun_case(problem, "1\n", timeout_seconds=1.0)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_stage, "compare")
        self.assertLessEqual(len(result.expected), 4300)
        self.assertLessEqual(len(result.actual), 4300)
        self.assertIn("... truncated ...", result.expected)
        self.assertIn("... truncated ...", result.actual)

    def test_rerun_case_truncates_long_response_input(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.brute_force_solution = "print('expected')\n"
        problem.reference_solution = "print('actual')\n"

        result = rerun_case(problem, "i" * 6000, timeout_seconds=1.0)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_stage, "compare")
        self.assertLessEqual(len(result.input), 4300)
        self.assertIn("... truncated ...", result.input)

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

    def test_review_flags_obfuscated_dangerous_generated_code(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "import   os\nprint('x')\n"
        problem.generator_code = "__import__('subprocess')\nprint('1')\n"

        review = review_problem(problem)

        dangerous_fields = {
            issue.field
            for issue in review.issues
            if issue.severity == "error" and "dangerous" in issue.message
        }
        self.assertIn("reference_solution", dangerous_fields)
        self.assertIn("generator_code", dangerous_fields)
        self.assertFalse(review.passed)

    def test_review_flags_dynamic_dangerous_generated_code(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "import importlib\nimportlib.import_module('subprocess')\nprint('x')\n"
        problem.brute_force_solution = "getattr(__builtins__, 'open')('out.txt', 'w')\nprint('x')\n"

        review = review_problem(problem)

        dangerous_fields = {
            issue.field
            for issue in review.issues
            if issue.severity == "error" and "dangerous" in issue.message
        }
        self.assertIn("reference_solution", dangerous_fields)
        self.assertIn("brute_force_solution", dangerous_fields)
        self.assertFalse(review.passed)

    def test_review_flags_subscripted_dangerous_generated_code(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "__builtins__['open']('out.txt', 'w')\nprint('x')\n"

        review = review_problem(problem)

        self.assertTrue(
            any(
                issue.field == "reference_solution" and issue.severity == "error" and "dangerous" in issue.message
                for issue in review.issues
            ),
            review.to_dict(),
        )
        self.assertFalse(review.passed)

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

    def test_server_rejects_boolean_validation_options(self) -> None:
        from app.server import _clamp_rounds, _clamp_timeout

        with self.assertRaisesRegex(ValueError, "rounds must be an integer"):
            _clamp_rounds(False)
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be a number"):
            _clamp_timeout(True)
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be a number"):
            _clamp_timeout(float("nan"))
        with self.assertRaisesRegex(ValueError, "timeout_seconds must be a number"):
            _clamp_timeout(float("inf"))

    def test_problem_request_parser_handles_string_boolean_flags(self) -> None:
        from app.server import _problem_request_from_body

        disabled = _problem_request_from_body({"topic": "array", "use_llm": "false"})
        enabled = _problem_request_from_body({"topic": "array", "use_llm": "true"})

        self.assertFalse(disabled.use_llm)
        self.assertTrue(enabled.use_llm)

    def test_problem_request_parser_rejects_invalid_boolean_flags(self) -> None:
        from app.server import _problem_request_from_body

        with self.assertRaisesRegex(ValueError, "use_llm must be a boolean"):
            _problem_request_from_body({"topic": "array", "use_llm": "maybe"})

    def test_problem_request_parser_normalizes_statement_language_aliases(self) -> None:
        from app.server import _problem_request_from_body

        english = _problem_request_from_body({"topic": "array", "statement_language": "english"})
        chinese = _problem_request_from_body({"topic": "array", "statement_language": "中文"})

        self.assertEqual(english.statement_language, "en")
        self.assertEqual(chinese.statement_language, "zh")

    def test_problem_request_parser_rejects_unsupported_statement_language(self) -> None:
        from app.server import _problem_request_from_body

        with self.assertRaisesRegex(ValueError, "statement_language must be zh or en"):
            _problem_request_from_body({"topic": "array", "statement_language": "fr"})

    def test_problem_request_parser_rejects_non_string_enum_fields(self) -> None:
        from app.server import _problem_request_from_body

        cases = [
            ("language", [], "language must be a string"),
            ("difficulty", [], "difficulty must be a string"),
            ("statement_language", [], "statement_language must be a string"),
        ]
        for field, value, message in cases:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, message):
                    _problem_request_from_body({"topic": "array", field: value})

    def test_problem_request_parser_clamps_generation_count(self) -> None:
        from app.server import _problem_request_from_body

        self.assertEqual(_problem_request_from_body({"topic": "array", "count": "3"}).count, 3)
        self.assertEqual(_problem_request_from_body({"topic": "array", "count": 0}).count, 1)
        self.assertEqual(_problem_request_from_body({"topic": "array", "count": 99}).count, 5)

    def test_problem_request_parser_rejects_invalid_generation_count(self) -> None:
        from app.server import _problem_request_from_body

        with self.assertRaisesRegex(ValueError, "count must be an integer"):
            _problem_request_from_body({"topic": "array", "count": "many"})

    def test_problem_request_parser_rejects_blank_topic(self) -> None:
        from app.server import _problem_request_from_body

        with self.assertRaisesRegex(ValueError, "topic is required"):
            _problem_request_from_body({"topic": "   ", "use_llm": False})

    def test_problem_request_parser_normalizes_python_language_alias(self) -> None:
        from app.server import _problem_request_from_body

        self.assertEqual(_problem_request_from_body({"topic": "array", "language": "py"}).language, "python")

    def test_problem_request_parser_rejects_unsupported_code_language(self) -> None:
        from app.server import _problem_request_from_body

        with self.assertRaisesRegex(ValueError, "language must be python"):
            _problem_request_from_body({"topic": "array", "language": "java"})

    def test_problem_request_parser_normalizes_difficulty(self) -> None:
        from app.server import _problem_request_from_body

        self.assertEqual(_problem_request_from_body({"topic": "array", "difficulty": "Medium"}).difficulty, "medium")

    def test_problem_request_parser_rejects_unsupported_difficulty(self) -> None:
        from app.server import _problem_request_from_body

        with self.assertRaisesRegex(ValueError, "difficulty must be easy, medium, or hard"):
            _problem_request_from_body({"topic": "array", "difficulty": "expert"})

    def test_server_validate_returns_report_for_sample_runtime_failure(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "raise RuntimeError('boom')\n"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"rounds": 1, "timeout_seconds": 1}

            with (
                patch("app.server.STORE", problem_store),
                patch("app.server.REPORT_STORE", report_store),
            ):
                handler._validate(problem.id)

            handler.send_response.assert_called_once_with(200)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertFalse(payload["sample_passed"])
            self.assertEqual(payload["failure_stage"], "sample")
            self.assertIn("sample reference failed", payload["failed_cases"][0]["reason"])
            self.assertIsNotNone(report_store.get_validation(problem.id))

    def test_server_validate_blocks_dangerous_code_before_execution(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "executed.txt"
            problem.samples = [{"input": "", "output": "ok\n"}]
            problem.reference_solution = f"open({str(marker)!r}, 'w').write('ran')\nprint('ok')\n"
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"rounds": 1, "timeout_seconds": 1}

            with patch("app.server.STORE", problem_store), patch("app.server.REPORT_STORE", report_store):
                handler._validate(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "validation blocked by dangerous generated code")
            self.assertFalse(payload["review"]["passed"])
            self.assertFalse(marker.exists())
            self.assertIsNotNone(report_store.get_review(problem.id))
            self.assertIsNone(report_store.get_validation(problem.id))

    def test_server_validate_rejects_invalid_rounds_as_bad_request(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"rounds": "not-a-number"}

            with patch("app.server.STORE", problem_store):
                handler._validate(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "rounds must be an integer")

    def test_server_validate_rejects_non_object_json_body(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            body = b"[]"
            handler = object.__new__(Handler)
            handler.headers = {"Content-Length": str(len(body))}
            handler.rfile = io.BytesIO(body)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STORE", problem_store):
                handler._validate(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "JSON body must be an object")

    def test_server_validate_rejects_invalid_content_length(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.headers = {"Content-Length": "many"}
            handler.rfile = io.BytesIO(b"{}")
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STORE", problem_store):
                handler._validate(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "invalid Content-Length")

    def test_server_validate_rejects_invalid_utf8_json_body(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            body = b"\xff"
            handler = object.__new__(Handler)
            handler.headers = {"Content-Length": str(len(body))}
            handler.rfile = io.BytesIO(body)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STORE", problem_store):
                handler._validate(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "invalid JSON body")

    def test_server_validate_rejects_negative_content_length(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.headers = {"Content-Length": "-1"}
            handler.rfile = io.BytesIO(b"{}")
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()

            with patch("app.server.STORE", problem_store):
                handler._validate(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "invalid Content-Length")

    def test_server_rerun_rejects_invalid_timeout_as_bad_request(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {
                "input": problem.samples[0]["input"],
                "timeout_seconds": "slow",
            }

            with patch("app.server.STORE", problem_store):
                handler._rerun(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "timeout_seconds must be a number")

    def test_server_rerun_accepts_empty_string_input(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "print('ok')\n"
        problem.brute_force_solution = "print('ok')\n"

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {
                "input": "",
                "timeout_seconds": 1,
            }

            with patch("app.server.STORE", problem_store):
                handler._rerun(problem.id)

            handler.send_response.assert_called_once_with(200)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["input"], "")
            self.assertTrue(payload["passed"])

    def test_server_rerun_blocks_dangerous_code_before_execution(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "executed.txt"
            problem.brute_force_solution = f"open({str(marker)!r}, 'w').write('ran')\nprint('ok')\n"
            problem.reference_solution = "print('ok')\n"
            problem_store = ProblemStore(root / "problems")
            report_store = ReportStore(root / "reports")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {
                "input": "",
                "timeout_seconds": 1,
            }

            with patch("app.server.STORE", problem_store), patch("app.server.REPORT_STORE", report_store):
                handler._rerun(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "rerun blocked by dangerous generated code")
            self.assertFalse(payload["review"]["passed"])
            self.assertFalse(marker.exists())
            self.assertIsNotNone(report_store.get_review(problem.id))

    def test_server_package_rejects_invalid_rounds_as_bad_request(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))

        with tempfile.TemporaryDirectory() as tmp:
            problem_store = ProblemStore(Path(tmp) / "problems")
            problem_store.save(problem)

            from app.server import Handler

            handler = object.__new__(Handler)
            handler.wfile = Mock()
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler._read_json = lambda default=None: {"rounds": "many"}

            with patch("app.server.STORE", problem_store):
                handler._package(problem.id)

            handler.send_response.assert_called_once_with(400)
            payload = json.loads(handler.wfile.write.call_args.args[0].decode("utf-8"))
            self.assertEqual(payload["error"], "rounds must be an integer")


if __name__ == "__main__":
    unittest.main()

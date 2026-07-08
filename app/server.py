from __future__ import annotations

import json
import mimetypes
import os
import shutil
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.exporter import create_problem_package_archive, export_problem_package
from app.generator import create_problem_draft, generate_problem
from app.models import ProblemRequest
from app.reviewer import review_problem
from app.store import ProblemStore, ReportStore, WorkflowStore
from app.validator import ValidationError, rerun_case, validate_problem
from app.workflow import advance_workflow, apply_problem_patch, create_workflow


ROOT = Path(__file__).resolve().parents[1]
STORE = ProblemStore(ROOT / "data" / "problems")
WORKFLOW_STORE = WorkflowStore(ROOT / "data" / "workflows")
REPORT_STORE = ReportStore(ROOT / "data" / "reports")
PACKAGE_ROOT = ROOT / "data" / "packages"
STATIC_ROOT = ROOT / "static"


class Handler(BaseHTTPRequestHandler):
    server_version = "AlgoQuestionMVP/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/api/problems":
            self._json(HTTPStatus.OK, {"list": [_summary(p) for p in STORE.list()]})
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/reports"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/reports")
            self._reports(problem_id)
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/workflow"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/workflow")
            self._workflow(problem_id)
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/package/download"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/package/download")
            self._download_package(problem_id)
            return
        if parsed.path.startswith("/api/problems/"):
            problem_id = parsed.path.removeprefix("/api/problems/")
            try:
                self._json(HTTPStatus.OK, STORE.get(problem_id).to_dict())
            except KeyError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/problems/generate":
            self._generate()
            return
        if parsed.path == "/api/workflows/start":
            self._start_workflow()
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/workflow/continue"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/workflow/continue")
            self._continue_workflow(problem_id)
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/edit"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/edit")
            self._edit_problem(problem_id)
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/validate"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/validate")
            self._validate(problem_id)
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/rerun"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/rerun")
            self._rerun(problem_id)
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/review"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/review")
            self._review(problem_id)
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/package"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/package")
            self._package(problem_id)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/problems/"):
            problem_id = parsed.path.removeprefix("/api/problems/")
            self._delete_problem(problem_id)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _generate(self) -> None:
        try:
            body = self._read_json()
            req = _problem_request_from_body(body)
            count = max(1, min(req.count, 5))
            problems = []
            for _ in range(count):
                problem = generate_problem(req)
                STORE.save(problem)
                problems.append(problem.to_dict())
            self._json(HTTPStatus.OK, {"list": problems})
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _start_workflow(self) -> None:
        try:
            body = self._read_json()
            req = _problem_request_from_body(body)
            manual_steps = body.get("manual_steps")
            if not isinstance(manual_steps, list):
                manual_steps = ["statement"]
            problem = create_problem_draft(req)
            STORE.save(problem)
            workflow = create_workflow(problem, req, [str(step) for step in manual_steps])
            workflow, result = advance_workflow(workflow, problem, PACKAGE_ROOT)
            problem = result.get("problem", problem)
            _persist_reports(problem.id, result.get("reports", {}))
            result = _public_workflow_result(result)
            STORE.save(problem)
            WORKFLOW_STORE.save(workflow)
            self._json(
                HTTPStatus.OK,
                {
                    "problem": problem.to_dict(),
                    "workflow": workflow.to_dict(),
                    "result": result,
                },
            )
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _workflow(self, problem_id: str) -> None:
        try:
            self._json(HTTPStatus.OK, WORKFLOW_STORE.get(problem_id).to_dict())
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "workflow not found"})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _continue_workflow(self, problem_id: str) -> None:
        try:
            problem = STORE.get(problem_id)
            workflow = WORKFLOW_STORE.get(problem_id)
            body = self._read_json(default={})
            if isinstance(body.get("patch"), dict):
                problem = apply_problem_patch(problem, body["patch"])
                STORE.save(problem)
            workflow, result = advance_workflow(
                workflow,
                problem,
                PACKAGE_ROOT,
                confirm_current=bool(body.get("confirm_current", True)),
            )
            problem = result.get("problem", problem)
            _persist_reports(problem.id, result.get("reports", {}))
            result = _public_workflow_result(result)
            STORE.save(problem)
            WORKFLOW_STORE.save(workflow)
            self._json(
                HTTPStatus.OK,
                {
                    "problem": problem.to_dict(),
                    "workflow": workflow.to_dict(),
                    "result": result,
                },
            )
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem or workflow not found"})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except ValidationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _edit_problem(self, problem_id: str) -> None:
        try:
            problem = STORE.get(problem_id)
            body = self._read_json(default={})
            patch = body.get("patch", body)
            if not isinstance(patch, dict):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "patch must be an object"})
                return
            problem = apply_problem_patch(problem, patch)
            STORE.save(problem)
            invalidated = _invalidate_problem_outputs(problem_id)
            payload = problem.to_dict()
            payload.update(invalidated)
            self._json(HTTPStatus.OK, payload)
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _validate(self, problem_id: str) -> None:
        try:
            problem = STORE.get(problem_id)
            body = self._read_json(default={})
            rounds = _clamp_rounds(body.get("rounds", 100))
            timeout_seconds = _clamp_timeout(body.get("timeout_seconds", 2.0))
            report = validate_problem(problem, rounds=rounds, timeout_seconds=timeout_seconds)
            payload = report.to_dict()
            REPORT_STORE.save_validation(problem_id, payload)
            self._json(HTTPStatus.OK, payload)
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except ValidationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _rerun(self, problem_id: str) -> None:
        try:
            problem = STORE.get(problem_id)
            body = self._read_json(default={})
            case_input = body.get("input")
            if not isinstance(case_input, str) or not case_input:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "input is required"})
                return
            timeout_seconds = _clamp_timeout(body.get("timeout_seconds", 2.0))
            self._json(HTTPStatus.OK, rerun_case(problem, case_input, timeout_seconds).to_dict())
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _reports(self, problem_id: str) -> None:
        try:
            STORE.get(problem_id)
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
            return

        package_dir = PACKAGE_ROOT / problem_id
        review = REPORT_STORE.get_review(problem_id) or _read_json_file(package_dir / "review_report.json")
        validation = REPORT_STORE.get_validation(problem_id) or _read_json_file(package_dir / "validation_report.json")
        package = None
        if package_dir.exists():
            package = _package_info(problem_id, package_dir)

        self._json(
            HTTPStatus.OK,
            {
                "problem_id": problem_id,
                "review": review,
                "validation": validation,
                "package": package,
            },
        )

    def _review(self, problem_id: str) -> None:
        try:
            problem = STORE.get(problem_id)
            report = review_problem(problem).to_dict()
            REPORT_STORE.save_review(problem_id, report)
            self._json(HTTPStatus.OK, report)
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _package(self, problem_id: str) -> None:
        try:
            problem = STORE.get(problem_id)
            body = self._read_json(default={})
            rounds = _clamp_rounds(body.get("rounds", 100))
            timeout_seconds = _clamp_timeout(body.get("timeout_seconds", 2.0))
            validation = validate_problem(problem, rounds=rounds, timeout_seconds=timeout_seconds)
            review = review_problem(problem)
            package_dir = export_problem_package(problem, PACKAGE_ROOT, validation, review)
            REPORT_STORE.save_review(problem.id, review.to_dict())
            REPORT_STORE.save_validation(problem.id, validation.to_dict())
            self._json(
                HTTPStatus.OK,
                {
                    "problem_id": problem.id,
                    "package_dir": str(package_dir),
                    "download_url": _package_download_url(problem.id),
                    "validation": validation.to_dict(),
                    "review": review.to_dict(),
                },
            )
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except ValidationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _download_package(self, problem_id: str) -> None:
        try:
            STORE.get(problem_id)
            archive_path = create_problem_package_archive(problem_id, PACKAGE_ROOT)
            body = archive_path.read_bytes()
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{archive_path.name}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except FileNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "package not found"})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _delete_problem(self, problem_id: str) -> None:
        try:
            STORE.get(problem_id)
            STORE.delete(problem_id)
            WORKFLOW_STORE.delete(problem_id)
            REPORT_STORE.delete(problem_id)
            removed_package = _remove_package_artifacts(problem_id)
            self._json(
                HTTPStatus.OK,
                {
                    "problem_id": problem_id,
                    "deleted": True,
                    "removed_package": removed_package,
                },
            )
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _read_json(self, default: dict | None = None) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return default if default is not None else {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str) -> None:
        safe_name = name.strip("/")
        path = (STATIC_ROOT / safe_name).resolve()
        if not str(path).startswith(str(STATIC_ROOT.resolve())) or not path.exists() or path.is_dir():
            self._json(HTTPStatus.NOT_FOUND, {"error": "static file not found"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _summary(problem) -> dict:
    return {
        "id": problem.id,
        "title": problem.title,
        "topic": problem.topic,
        "difficulty": problem.difficulty,
        "tags": problem.tags,
        "created_at": problem.created_at,
        "source": problem.source,
        "statement_language": problem.statement_language,
    }


def _read_json_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _package_info(problem_id: str, package_dir: Path) -> dict:
    return {
        "package_dir": str(package_dir),
        "download_url": _package_download_url(problem_id),
    }


def _package_download_url(problem_id: str) -> str:
    return f"/api/problems/{problem_id}/package/download"


def _persist_reports(problem_id: str, reports: dict) -> None:
    review = reports.get("review")
    validation = reports.get("validation")
    if isinstance(review, dict):
        REPORT_STORE.save_review(problem_id, review)
    if isinstance(validation, dict):
        REPORT_STORE.save_validation(problem_id, validation)


def _invalidate_problem_outputs(problem_id: str) -> dict:
    return {
        "reports_invalidated": REPORT_STORE.delete(problem_id),
        "package_invalidated": _remove_package_artifacts(problem_id),
    }


def _remove_package_artifacts(problem_id: str) -> bool:
    package_dir = (PACKAGE_ROOT / problem_id).resolve()
    archive_path = (PACKAGE_ROOT / f"{problem_id}.zip").resolve()
    root_path = PACKAGE_ROOT.resolve()
    removed = False
    if str(package_dir).startswith(str(root_path)) and package_dir.is_dir():
        shutil.rmtree(package_dir)
        removed = True
    if str(archive_path).startswith(str(root_path)) and archive_path.exists():
        archive_path.unlink()
        removed = True
    return removed


def _problem_request_from_body(body: dict) -> ProblemRequest:
    return ProblemRequest(
        topic=str(body.get("topic", "array")),
        difficulty=str(body.get("difficulty", "easy")),
        language=str(body.get("language", "python")),
        statement_language=str(body.get("statement_language", body.get("natural_language", "zh"))),
        count=int(body.get("count", 1)),
        use_llm=bool(body.get("use_llm", True)),
    )


def _clamp_rounds(value: object) -> int:
    return max(1, min(int(value), 1000))


def _clamp_timeout(value: object) -> float:
    return max(0.2, min(float(value), 10.0))


def _public_workflow_result(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "problem"}


def main() -> None:
    host = os.getenv("ALGO_HOST", "127.0.0.1")
    port = int(os.getenv("ALGO_PORT", "18081"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"algo-question-mvp listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()

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
from app.generator import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, create_problem_draft, generate_problem
from app.models import ProblemRequest
from app.paths import resolve_under
from app.reviewer import review_problem
from app.similarity import find_similar_problems
from app.store import ProblemStore, ReportStore, WorkflowStore
from app.validator import ValidationError, rerun_case, validate_problem
from app.workflow import advance_workflow, apply_problem_patch, create_workflow, normalize_manual_steps


ROOT = Path(__file__).resolve().parents[1]
STORE = ProblemStore(ROOT / "data" / "problems")
WORKFLOW_STORE = WorkflowStore(ROOT / "data" / "workflows")
REPORT_STORE = ReportStore(ROOT / "data" / "reports")
PACKAGE_ROOT = ROOT / "data" / "packages"
STATIC_ROOT = ROOT / "static"
DEFAULT_GENERATION_COUNT = 1
MAX_GENERATION_COUNT = 5
DEFAULT_VALIDATION_ROUNDS = 100
MAX_VALIDATION_ROUNDS = 1000
DEFAULT_TIMEOUT_SECONDS = 2.0
MIN_TIMEOUT_SECONDS = 0.2
MAX_TIMEOUT_SECONDS = 10.0


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
        if parsed.path == "/api/runtime":
            self._runtime()
            return
        if parsed.path == "/api/problems":
            self._json(HTTPStatus.OK, {"list": [_summary(p) for p in STORE.list()]})
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/reports"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/reports")
            self._reports(problem_id)
            return
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/similar"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/similar")
            self._similar(problem_id)
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
            count = max(1, min(req.count, MAX_GENERATION_COUNT))
            problems = []
            for _ in range(count):
                problem = generate_problem(req)
                STORE.save(problem)
                problems.append(problem.to_dict())
            self._json(HTTPStatus.OK, {"list": problems})
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _runtime(self) -> None:
        self._json(HTTPStatus.OK, _runtime_info())

    def _start_workflow(self) -> None:
        try:
            body = self._read_json()
            req = _problem_request_from_body(body)
            manual_steps = normalize_manual_steps(body.get("manual_steps") if "manual_steps" in body else None)
            problem = create_problem_draft(req)
            workflow = create_workflow(problem, req, manual_steps)
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
            invalidated = {"reports_invalidated": False, "package_invalidated": False}
            changed = False
            if "patch" in body:
                if not isinstance(body["patch"], dict):
                    raise ValueError("patch must be an object")
                original = problem.to_dict()
                problem = apply_problem_patch(problem, body["patch"])
                changed = problem.to_dict() != original
                if changed:
                    STORE.save(problem)
                    invalidated = _invalidate_problem_outputs(problem_id)
            workflow, result = advance_workflow(
                workflow,
                problem,
                PACKAGE_ROOT,
                confirm_current=_parse_bool(body.get("confirm_current", True), "confirm_current"),
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
                    "changed": changed,
                    **invalidated,
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
            if not isinstance(body, dict):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "patch must be an object"})
                return
            patch = body.get("patch", body)
            if not isinstance(patch, dict):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "patch must be an object"})
                return
            original = problem.to_dict()
            problem = apply_problem_patch(problem, patch)
            changed = problem.to_dict() != original
            if changed:
                STORE.save(problem)
                invalidated = _invalidate_problem_outputs(problem_id)
            else:
                invalidated = {"reports_invalidated": False, "package_invalidated": False}
            payload = problem.to_dict()
            payload["changed"] = changed
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
            rounds = _clamp_rounds(body.get("rounds", DEFAULT_VALIDATION_ROUNDS))
            timeout_seconds = _clamp_timeout(body.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
            report = validate_problem(problem, rounds=rounds, timeout_seconds=timeout_seconds)
            payload = report.to_dict()
            REPORT_STORE.save_validation(problem_id, payload)
            self._json(HTTPStatus.OK, payload)
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
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
            timeout_seconds = _clamp_timeout(body.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
            self._json(HTTPStatus.OK, rerun_case(problem, case_input, timeout_seconds).to_dict())
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
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
        package = _package_report_status(problem_id, package_dir, review, validation)

        self._json(
            HTTPStatus.OK,
            {
                "problem_id": problem_id,
                "review": review,
                "validation": validation,
                "package": package,
            },
        )

    def _similar(self, problem_id: str) -> None:
        try:
            problem = STORE.get(problem_id)
            report = find_similar_problems(problem, STORE.list())
            self._json(HTTPStatus.OK, report.to_dict())
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

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
            rounds = _clamp_rounds(body.get("rounds", DEFAULT_VALIDATION_ROUNDS))
            timeout_seconds = _clamp_timeout(body.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
            validation = validate_problem(problem, rounds=rounds, timeout_seconds=timeout_seconds)
            review = review_problem(problem)
            validation_report = validation.to_dict()
            review_report = review.to_dict()
            REPORT_STORE.save_review(problem.id, review_report)
            REPORT_STORE.save_validation(problem.id, validation_report)
            if not review.passed or not validation.sample_passed or not validation.fuzz_passed:
                _remove_package_artifacts(problem.id)
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "problem_id": problem.id,
                        "package_blocked": True,
                        "error": "package blocked by failed review or validation",
                        "validation": validation_report,
                        "review": review_report,
                    },
                )
                return
            package_dir = export_problem_package(problem, PACKAGE_ROOT, validation, review)
            self._json(
                HTTPStatus.OK,
                {
                    "problem_id": problem.id,
                    "package_blocked": False,
                    "package_dir": str(package_dir),
                    "download_url": _package_download_url(problem.id),
                    "validation": validation_report,
                    "review": review_report,
                },
            )
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "problem not found"})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
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
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0:
            raise ValueError("invalid Content-Length")
        if length == 0:
            return default if default is not None else {}
        try:
            raw = self.rfile.read(length).decode("utf-8")
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str) -> None:
        safe_name = name.strip("/")
        path = _safe_static_path(safe_name)
        if path is None or not path.exists() or path.is_dir():
            self._json(HTTPStatus.NOT_FOUND, {"error": "static file not found"})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _safe_static_path(name: str) -> Path | None:
    return resolve_under(STATIC_ROOT, name)


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


def _runtime_info() -> dict:
    llm_configured = bool(os.getenv("ALGO_LLM_API_KEY"))
    return {
        "ok": True,
        "server": {
            "host": os.getenv("ALGO_HOST", "127.0.0.1"),
            "port": int(os.getenv("ALGO_PORT", "18081")),
        },
        "llm": {
            "configured": llm_configured,
            "active_mode": "llm" if llm_configured else "template",
            "base_url": os.getenv("ALGO_LLM_BASE_URL", DEFAULT_LLM_BASE_URL).rstrip("/"),
            "model": os.getenv("ALGO_LLM_MODEL", DEFAULT_LLM_MODEL),
            "fallback_source": "mock",
            "failure_fallback_source": "mock-after-llm-failure",
            "language_fallback_source": "mock-after-language-mismatch",
        },
        "generation": {
            "default_count": DEFAULT_GENERATION_COUNT,
            "max_count": MAX_GENERATION_COUNT,
            "supported_statement_languages": ["zh", "en"],
            "default_statement_language": "zh",
        },
        "validation": {
            "default_rounds": DEFAULT_VALIDATION_ROUNDS,
            "max_rounds": MAX_VALIDATION_ROUNDS,
            "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "min_timeout_seconds": MIN_TIMEOUT_SECONDS,
            "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
        },
    }


def _read_json_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _package_info(problem_id: str, package_dir: Path) -> dict:
    return {
        "package_blocked": False,
        "package_dir": str(package_dir),
        "download_url": _package_download_url(problem_id),
    }


def _package_report_status(problem_id: str, package_dir: Path, review: dict | None, validation: dict | None) -> dict | None:
    if package_dir.exists():
        return _package_info(problem_id, package_dir)
    if _reports_block_package(review, validation):
        return {
            "package_blocked": True,
            "error": "package blocked by failed review or validation",
            "review_passed": review.get("passed") if isinstance(review, dict) else None,
            "sample_passed": validation.get("sample_passed") if isinstance(validation, dict) else None,
            "fuzz_passed": validation.get("fuzz_passed") if isinstance(validation, dict) else None,
        }
    return None


def _reports_block_package(review: dict | None, validation: dict | None) -> bool:
    review_failed = isinstance(review, dict) and review.get("passed") is False
    validation_failed = isinstance(validation, dict) and (
        validation.get("sample_passed") is False or validation.get("fuzz_passed") is False
    )
    return review_failed or validation_failed


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
    package_dir = resolve_under(PACKAGE_ROOT, problem_id)
    archive_path = resolve_under(PACKAGE_ROOT, f"{problem_id}.zip")
    removed = False
    if package_dir is not None and package_dir.is_dir():
        shutil.rmtree(package_dir)
        removed = True
    if archive_path is not None and archive_path.exists():
        archive_path.unlink()
        removed = True
    return removed


def _problem_request_from_body(body: dict) -> ProblemRequest:
    return ProblemRequest(
        topic=_parse_topic(body.get("topic", "array")),
        difficulty=_parse_difficulty(body.get("difficulty", "easy")),
        language=_parse_code_language(body.get("language", "python")),
        statement_language=_parse_statement_language(body.get("statement_language", body.get("natural_language", "zh"))),
        count=_parse_count(body.get("count", DEFAULT_GENERATION_COUNT)),
        use_llm=_parse_bool(body.get("use_llm", True), "use_llm"),
    )


def _parse_topic(value: object) -> str:
    if value is None:
        raise ValueError("topic is required")
    topic = str(value).strip()
    if not topic:
        raise ValueError("topic is required")
    return topic


def _parse_code_language(value: object) -> str:
    normalized = str(value or "python").strip().lower()
    if normalized in {"python", "py", "python3", "py3"}:
        return "python"
    raise ValueError("language must be python")


def _parse_difficulty(value: object) -> str:
    normalized = str(value or "easy").strip().lower()
    if normalized in {"easy", "medium", "hard"}:
        return normalized
    raise ValueError("difficulty must be easy, medium, or hard")


def _parse_count(value: object) -> int:
    count = _parse_integer(value, "count")
    return max(1, min(count, MAX_GENERATION_COUNT))


def _parse_statement_language(value: object) -> str:
    if value is None:
        return "zh"
    normalized = str(value).strip().lower()
    if normalized in {"zh", "cn", "chinese", "中文", "汉语"}:
        return "zh"
    if normalized in {"en", "english"}:
        return "en"
    raise ValueError("statement_language must be zh or en")


def _parse_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"{field} must be a boolean")


def _clamp_rounds(value: object) -> int:
    rounds = _parse_integer(value, "rounds")
    return max(1, min(rounds, MAX_VALIDATION_ROUNDS))


def _parse_integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        signless = text[1:] if text[:1] in {"+", "-"} else text
        if signless.isdigit():
            return int(text)
    raise ValueError(f"{field} must be an integer")


def _clamp_timeout(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("timeout_seconds must be a number")
    try:
        timeout_seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be a number") from exc
    return max(MIN_TIMEOUT_SECONDS, min(timeout_seconds, MAX_TIMEOUT_SECONDS))


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

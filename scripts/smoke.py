from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.error
import urllib.request
import zipfile
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end smoke checks against the MVP server.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--include-llm", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    results: list[str] = []

    health = _get_json(f"{base_url}/healthz")
    _assert(health.get("ok") is True, "healthz returned ok")
    results.append("healthz ok")

    runtime = _get_json(f"{base_url}/api/runtime")
    _assert(runtime.get("ok") is True, "runtime endpoint returned ok")
    _assert(isinstance(runtime.get("llm"), dict), "runtime endpoint includes llm block")
    _assert("configured" in runtime["llm"], "runtime llm block includes configured flag")
    _assert("api_key" not in json.dumps(runtime).lower(), "runtime endpoint does not expose api key")
    _assert(runtime["generation"]["max_count"] == 5, "runtime generation max count is exposed")
    _assert(runtime["validation"]["max_rounds"] == 1000, "runtime validation max rounds is exposed")
    results.append("runtime ok")

    index_html = _get_text(f"{base_url}/")
    _assert("算法出题工作台" in index_html, "frontend index contains title")
    _assert("运行状态" in index_html, "frontend index contains runtime status panel")
    results.append("frontend index ok")

    mock_problem_id = _run_problem_flow(base_url, use_llm=False, topic="string", rounds=30)
    results.append(f"mock flow ok: {mock_problem_id}")

    duplicate_ids = _run_similarity_flow(base_url)
    results.append(f"similarity flow ok: {', '.join(duplicate_ids)}")

    zh_problem = _post_json(
        f"{base_url}/api/problems/generate",
        {
            "topic": "array",
            "difficulty": "easy",
            "count": 1,
            "use_llm": False,
        },
        timeout=30,
    )["list"][0]
    _assert(zh_problem["statement_language"] == "zh", "default statement language is Chinese")
    _assert("给定" in zh_problem["statement"], "default mock statement is Chinese")
    _assert_deleted(base_url, zh_problem["id"])
    results.append(f"default chinese ok: {zh_problem['id']}")

    counted = _post_json(
        f"{base_url}/api/problems/generate",
        {"topic": "array", "difficulty": "easy", "count": "2", "use_llm": False},
        timeout=30,
    )["list"]
    _assert(len(counted) == 2, "string count generates requested number of problems")
    for problem in counted:
        _assert_deleted(base_url, problem["id"])
    results.append("string count ok")

    try:
        _post_json(
            f"{base_url}/api/problems/generate",
            {"topic": "array", "difficulty": "easy", "count": "many", "use_llm": False},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "invalid generation count returns 400")
        _assert(body["error"] == "count must be an integer", "invalid generation count has clear error")
    else:
        raise AssertionError("invalid generation count unexpectedly succeeded")

    try:
        _post_json(
            f"{base_url}/api/problems/generate",
            {"topic": "   ", "difficulty": "easy", "count": 1, "use_llm": False},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "blank topic returns 400")
        _assert(body["error"] == "topic is required", "blank topic has clear error")
        results.append("blank topic rejected ok")
    else:
        raise AssertionError("blank topic unexpectedly succeeded")

    medium_problem = _post_json(
        f"{base_url}/api/problems/generate",
        {"topic": "array", "difficulty": "Medium", "count": 1, "use_llm": False},
        timeout=30,
    )["list"][0]
    _assert(medium_problem["difficulty"] == "medium", "difficulty normalizes to lowercase")
    _assert_deleted(base_url, medium_problem["id"])
    results.append(f"difficulty normalization ok: {medium_problem['id']}")

    try:
        _post_json(
            f"{base_url}/api/problems/generate",
            {"topic": "array", "difficulty": "expert", "count": 1, "use_llm": False},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "unsupported difficulty returns 400")
        _assert(
            body["error"] == "difficulty must be easy, medium, or hard",
            "unsupported difficulty has clear error",
        )
    else:
        raise AssertionError("unsupported difficulty unexpectedly succeeded")

    python_alias_problem = _post_json(
        f"{base_url}/api/problems/generate",
        {"topic": "array", "difficulty": "easy", "language": "py", "count": 1, "use_llm": False},
        timeout=30,
    )["list"][0]
    _assert(python_alias_problem["reference_solution"], "python language alias generates Python solution")
    _assert_deleted(base_url, python_alias_problem["id"])
    results.append(f"code language alias ok: {python_alias_problem['id']}")

    try:
        _post_json(
            f"{base_url}/api/problems/generate",
            {"topic": "array", "difficulty": "easy", "language": "java", "count": 1, "use_llm": False},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "unsupported code language returns 400")
        _assert(body["error"] == "language must be python", "unsupported code language has clear error")
    else:
        raise AssertionError("unsupported code language unexpectedly succeeded")

    string_false_problem = _post_json(
        f"{base_url}/api/problems/generate",
        {"topic": "array", "difficulty": "easy", "count": 1, "use_llm": "false"},
        timeout=30,
    )["list"][0]
    _assert(string_false_problem["source"] == "mock", "string false use_llm disables llm")
    _assert_deleted(base_url, string_false_problem["id"])
    results.append(f"string boolean ok: {string_false_problem['id']}")

    english_alias_problem = _post_json(
        f"{base_url}/api/problems/generate",
        {
            "topic": "array",
            "difficulty": "easy",
            "count": 1,
            "statement_language": "english",
            "use_llm": False,
        },
        timeout=30,
    )["list"][0]
    _assert(english_alias_problem["statement_language"] == "en", "english language alias normalizes to en")
    _assert("Given" in english_alias_problem["statement"], "english alias returns English statement")
    _assert_deleted(base_url, english_alias_problem["id"])
    results.append(f"language alias ok: {english_alias_problem['id']}")

    try:
        _post_json(
            f"{base_url}/api/problems/generate",
            {"topic": "array", "statement_language": "fr", "use_llm": False},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "unsupported statement language returns 400")
        _assert(
            body["error"] == "statement_language must be zh or en",
            "unsupported statement language has clear error",
        )
    else:
        raise AssertionError("unsupported statement language unexpectedly succeeded")

    problem_count_before_invalid_workflow = len(_get_json(f"{base_url}/api/problems").get("list") or [])
    try:
        _post_json(
            f"{base_url}/api/workflows/start",
            {
                "topic": "array",
                "difficulty": "easy",
                "count": 1,
                "use_llm": False,
                "manual_steps": ["unknown"],
            },
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "unsupported manual workflow step returns 400")
        _assert(
            body["error"] == "manual_steps contains unsupported step: unknown",
            "unsupported manual workflow step has clear error",
        )
    else:
        raise AssertionError("unsupported manual workflow step unexpectedly succeeded")
    problem_count_after_invalid_workflow = len(_get_json(f"{base_url}/api/problems").get("list") or [])
    _assert(
        problem_count_after_invalid_workflow == problem_count_before_invalid_workflow,
        "invalid workflow manual steps do not save a draft problem",
    )
    results.append("manual step validation ok")

    workflow = _post_json(
        f"{base_url}/api/workflows/start",
        {
            "topic": "循环",
            "difficulty": "easy",
            "statement_language": "zh",
            "count": 1,
            "use_llm": False,
            "manual_steps": ["statement"],
        },
        timeout=30,
    )
    problem_id = workflow["problem"]["id"]
    _assert(workflow["workflow"]["status"] == "waiting_user", "workflow stops for manual statement step")
    _assert(workflow["workflow"]["current_step"] == "statement", "workflow is waiting at statement step")
    _assert(workflow["problem"]["constraints"] == [], "constraints are not generated before statement confirmation")
    _assert(workflow["problem"]["reference_solution"] == "", "solutions are not generated before statement confirmation")
    not_confirmed = _post_json(
        f"{base_url}/api/problems/{problem_id}/workflow/continue",
        {"confirm_current": "false"},
        timeout=30,
    )
    _assert(not_confirmed["workflow"]["status"] == "waiting_user", "string false confirmation keeps workflow waiting")
    _assert(
        not_confirmed["workflow"]["current_step"] == "statement",
        "string false confirmation keeps current workflow step",
    )
    try:
        _post_json(
            f"{base_url}/api/problems/{problem_id}/workflow/continue",
            {"confirm_current": "later"},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "invalid workflow confirmation flag returns 400")
        _assert(
            body["error"] == "confirm_current must be a boolean",
            "invalid workflow confirmation flag has clear error",
        )
    else:
        raise AssertionError("invalid workflow confirmation flag unexpectedly succeeded")
    continued = _post_json(
        f"{base_url}/api/problems/{problem_id}/workflow/continue",
        {
            "confirm_current": True,
            "patch": {
                "title": workflow["problem"]["title"] + "（改）",
            },
        },
        timeout=60,
    )
    _assert(continued["workflow"]["status"] == "completed", "workflow completes after confirmation")
    _assert(continued["changed"] is True, "workflow patch reports changed")
    _assert(continued["problem"]["title"].endswith("（改）"), "workflow patch was saved")
    _assert(continued["problem"]["constraints"], "constraints generated after confirmation")
    _assert(continued["problem"]["reference_solution"], "reference solution generated after confirmation")
    _assert_deleted(base_url, problem_id)
    results.append(f"workflow flow ok: {problem_id}")

    if args.include_llm:
        llm_problem_id = _run_problem_flow(base_url, use_llm=True, topic="two pointers", rounds=50)
        results.append(f"llm flow ok: {llm_problem_id}")

    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


def _run_similarity_flow(base_url: str) -> list[str]:
    first = _post_json(
        f"{base_url}/api/problems/generate",
        {"topic": "array", "difficulty": "easy", "count": 1, "use_llm": False},
        timeout=30,
    )["list"][0]
    second = _post_json(
        f"{base_url}/api/problems/generate",
        {"topic": "array", "difficulty": "easy", "count": 1, "use_llm": False},
        timeout=30,
    )["list"][0]
    ids = [first["id"], second["id"]]
    try:
        report = _get_json(f"{base_url}/api/problems/{first['id']}/similar")
        _assert(report["problem_id"] == first["id"], "similar endpoint returns selected problem id")
        _assert(report["has_risk"] is True, "similar endpoint reports duplicate risk")
        matching_candidates = [
            candidate for candidate in report["candidates"] if candidate["problem_id"] == second["id"]
        ]
        _assert(bool(matching_candidates), "similar endpoint returns duplicate candidate")
        _assert(matching_candidates[0]["score"] >= report["threshold"], "similar candidate passes threshold")
    finally:
        for problem_id in ids:
            _assert_deleted(base_url, problem_id)
    return ids


def _run_problem_flow(base_url: str, use_llm: bool, topic: str, rounds: int) -> str:
    generated = _post_json(
        f"{base_url}/api/problems/generate",
        {
            "topic": topic,
            "difficulty": "easy",
            "count": 1,
            "use_llm": use_llm,
        },
        timeout=90,
    )
    problems = generated.get("list") or []
    _assert(len(problems) == 1, "generate returned exactly one problem")
    problem = problems[0]
    problem_id = problem["id"]
    if use_llm:
        _assert(problem["source"] == "llm", f"expected llm source, got {problem['source']}")

    detail = _get_json(f"{base_url}/api/problems/{problem_id}")
    _assert(detail["id"] == problem_id, "detail endpoint returned selected problem")
    similarity = _get_json(f"{base_url}/api/problems/{problem_id}/similar")
    _assert(similarity["problem_id"] == problem_id, "similar endpoint returns selected problem")
    _assert("candidates" in similarity, "similar endpoint includes candidates")

    review = _post_json(f"{base_url}/api/problems/{problem_id}/review", {}, timeout=30)
    _assert(review["passed"] is True, f"review passed for {problem_id}")
    _assert(review["score"] >= 80, f"review score >= 80 for {problem_id}")

    validation = _post_json(
        f"{base_url}/api/problems/{problem_id}/validate",
        {"rounds": rounds, "timeout_seconds": 1.5},
        timeout=60,
    )
    _assert(validation["sample_passed"] is True, f"samples passed for {problem_id}")
    _assert(validation["fuzz_passed"] is True, f"fuzz passed for {problem_id}")
    _assert(validation["failed_cases"] == [], f"no failed cases for {problem_id}")
    _assert(validation["rounds"] == rounds, f"validation rounds recorded for {problem_id}")
    _assert(validation["timeout_seconds"] == 1.5, f"validation timeout recorded for {problem_id}")

    pre_package_reports = _get_json(f"{base_url}/api/problems/{problem_id}/reports")
    _assert(pre_package_reports["review"]["passed"] is True, "review report persists before package export")
    _assert(
        pre_package_reports["validation"]["rounds"] == rounds,
        "validation report persists before package export",
    )
    _assert(pre_package_reports["package"] is None, "package info is absent before package export")

    try:
        _post_json(
            f"{base_url}/api/problems/{problem_id}/validate",
            {"rounds": "invalid", "timeout_seconds": 1.5},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "invalid validation rounds returns 400")
        _assert(body["error"] == "rounds must be an integer", "invalid validation rounds has clear error")
    else:
        raise AssertionError("invalid validation rounds unexpectedly succeeded")

    try:
        _post_json(
            f"{base_url}/api/problems/{problem_id}/validate",
            {"rounds": False, "timeout_seconds": 1.5},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "boolean validation rounds returns 400")
        _assert(body["error"] == "rounds must be an integer", "boolean validation rounds has clear error")
    else:
        raise AssertionError("boolean validation rounds unexpectedly succeeded")

    try:
        _post_json(
            f"{base_url}/api/problems/{problem_id}/validate",
            {"rounds": rounds, "timeout_seconds": True},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "boolean validation timeout returns 400")
        _assert(body["error"] == "timeout_seconds must be a number", "boolean validation timeout has clear error")
    else:
        raise AssertionError("boolean validation timeout unexpectedly succeeded")

    try:
        _post_raw_json(f"{base_url}/api/problems/{problem_id}/validate", b"[]", timeout=30)
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "non-object JSON body returns 400")
        _assert(body["error"] == "JSON body must be an object", "non-object JSON body has clear error")
    else:
        raise AssertionError("non-object JSON body unexpectedly succeeded")

    rerun = _post_json(
        f"{base_url}/api/problems/{problem_id}/rerun",
        {"input": problem["samples"][0]["input"], "timeout_seconds": 1.5},
        timeout=30,
    )
    _assert(rerun["passed"] is True, f"rerun passed for {problem_id}")
    _assert(rerun["expected"] == rerun["actual"], f"rerun outputs match for {problem_id}")

    package = _post_json(
        f"{base_url}/api/problems/{problem_id}/package",
        {"rounds": rounds, "timeout_seconds": 1.5},
        timeout=60,
    )
    _assert(package["problem_id"] == problem_id, "package endpoint returned selected problem")
    _assert(bool(package["package_dir"]), "package_dir is present")
    _assert(package["validation"]["fuzz_passed"] is True, "package validation passed")
    _assert(package["review"]["passed"] is True, "package review passed")

    reports = _get_json(f"{base_url}/api/problems/{problem_id}/reports")
    _assert(reports["review"]["passed"] is True, "stored review report is readable")
    _assert(reports["validation"]["fuzz_passed"] is True, "stored validation report is readable")
    _assert(bool(reports["package"]["package_dir"]), "stored package info is readable")

    archive_body, archive_headers = _get_bytes(f"{base_url}/api/problems/{problem_id}/package/download")
    _assert(len(archive_body) > 0, "package zip download is nonempty")
    _assert(archive_headers.get("Content-Type") == "application/zip", "package zip content type is correct")
    with zipfile.ZipFile(io.BytesIO(archive_body)) as archive:
        names = set(archive.namelist())
    _assert("problem.md" in names, "package zip contains problem.md")
    _assert("validation_report.json" in names, "package zip contains validation report")

    noop_edit = _post_json(
        f"{base_url}/api/problems/{problem_id}/edit",
        {"patch": {"title": problem["title"]}},
        timeout=30,
    )
    _assert(noop_edit["changed"] is False, "noop edit reports unchanged")
    _assert(noop_edit["reports_invalidated"] is False, "noop edit keeps stored reports")
    _assert(noop_edit["package_invalidated"] is False, "noop edit keeps package artifacts")
    noop_reports = _get_json(f"{base_url}/api/problems/{problem_id}/reports")
    _assert(noop_reports["review"]["passed"] is True, "noop edit keeps review report readable")
    _assert(noop_reports["validation"]["fuzz_passed"] is True, "noop edit keeps validation report readable")
    noop_archive_body, _ = _get_bytes(f"{base_url}/api/problems/{problem_id}/package/download")
    _assert(len(noop_archive_body) > 0, "noop edit keeps package zip downloadable")

    edited_samples = [
        {"input": "1 2\n1\n", "output": "0\n"},
        {"input": "2 3\n1 2\n", "output": "1\n"},
    ]
    edited_reference = f"{problem['reference_solution'].rstrip()}\n# edited in smoke\n"
    edited = _post_json(
        f"{base_url}/api/problems/{problem_id}/edit",
        {
            "patch": {
                "title": f"{problem['title']} edited",
                "samples": edited_samples,
                "reference_solution": edited_reference,
            }
        },
        timeout=30,
    )
    _assert(edited["samples"] == edited_samples, "edit endpoint saved sample changes")
    _assert(edited["reference_solution"] == edited_reference, "edit endpoint saved reference solution changes")
    _assert(edited["changed"] is True, "changed edit reports changed")
    _assert(edited["reports_invalidated"] is True, "edit invalidated stored reports")
    _assert(edited["package_invalidated"] is True, "edit invalidated package artifacts")
    invalidated_reports = _get_json(f"{base_url}/api/problems/{problem_id}/reports")
    _assert(invalidated_reports["review"] is None, "review report is cleared after edit")
    _assert(invalidated_reports["validation"] is None, "validation report is cleared after edit")
    _assert(invalidated_reports["package"] is None, "package info is cleared after edit")
    try:
        _get_bytes(f"{base_url}/api/problems/{problem_id}/package/download")
    except urllib.error.HTTPError as exc:
        _assert(exc.code == 404, "package download returns 404 after edit")
    else:
        raise AssertionError("package download still succeeds after edit")

    try:
        _post_json(
            f"{base_url}/api/problems/{problem_id}/package",
            {"rounds": rounds, "timeout_seconds": 1.5},
            timeout=60,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "package returns 400 when validation fails")
        _assert(body["package_blocked"] is True, "package response marks export as blocked")
        _assert(body["validation"]["sample_passed"] is False, "blocked package includes failed validation report")
        _assert("review" in body, "blocked package includes review report")
    else:
        raise AssertionError("package unexpectedly succeeded after failing validation")

    blocked_reports = _get_json(f"{base_url}/api/problems/{problem_id}/reports")
    _assert(blocked_reports["validation"]["sample_passed"] is False, "blocked validation report persists")
    _assert(
        blocked_reports["package"]["package_blocked"] is True,
        "blocked package status persists after report reload",
    )
    try:
        _get_bytes(f"{base_url}/api/problems/{problem_id}/package/download")
    except urllib.error.HTTPError as exc:
        _assert(exc.code == 404, "package download remains 404 after blocked export")
    else:
        raise AssertionError("package download succeeds after blocked export")

    try:
        _post_json(
            f"{base_url}/api/problems/{problem_id}/edit",
            {"patch": {"samples": [{"input": "missing output\n"}]}},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        _assert(exc.code == 400, "invalid sample patch returns 400")
    else:
        raise AssertionError("invalid sample patch unexpectedly succeeded")

    after_invalid_edit = _get_json(f"{base_url}/api/problems/{problem_id}")
    _assert(after_invalid_edit["samples"] == edited_samples, "invalid sample patch did not overwrite samples")

    try:
        _post_json(
            f"{base_url}/api/problems/{problem_id}/edit",
            {"patch": {"unknown": "value"}},
            timeout=30,
        )
    except urllib.error.HTTPError as exc:
        body = _http_error_json(exc)
        _assert(exc.code == 400, "unknown edit patch field returns 400")
        _assert(
            body["error"] == "patch contains unsupported field: unknown",
            "unknown edit patch field has clear error",
        )
    else:
        raise AssertionError("unknown edit patch field unexpectedly succeeded")

    after_unknown_edit = _get_json(f"{base_url}/api/problems/{problem_id}")
    _assert(after_unknown_edit["samples"] == edited_samples, "unknown patch field did not overwrite samples")

    _assert_deleted(base_url, problem_id)
    return problem_id


def _get_json(url: str) -> dict[str, Any]:
    return json.loads(_get_text(url))


def _get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read().decode("utf-8")


def _get_bytes(url: str) -> tuple[bytes, Any]:
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read(), response.headers


def _delete_json(url: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _assert_deleted(base_url: str, problem_id: str) -> None:
    deleted = _delete_json(f"{base_url}/api/problems/{problem_id}")
    _assert(deleted["deleted"] is True, f"delete endpoint removed {problem_id}")
    try:
        _get_json(f"{base_url}/api/problems/{problem_id}")
    except urllib.error.HTTPError as exc:
        _assert(exc.code == 404, f"deleted problem returns 404 for {problem_id}")
    else:
        raise AssertionError(f"deleted problem is still readable: {problem_id}")


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    return _post_raw_json(url, json.dumps(payload).encode("utf-8"), timeout)


def _post_raw_json(url: str, body: bytes, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_error_json(exc: urllib.error.HTTPError) -> dict[str, Any]:
    return json.loads(exc.read().decode("utf-8"))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise

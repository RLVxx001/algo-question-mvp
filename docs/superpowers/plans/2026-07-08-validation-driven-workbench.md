# Validation-Driven Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the local algorithm-question workbench with richer validation metadata, single-case reruns, stronger review checks, more local templates, and a more useful static UI.

**Architecture:** Keep the current Python standard-library HTTP service and static frontend. Extend existing dataclasses and modules in place: `app/models.py` owns response shapes, `app/validator.py` owns validation/rerun execution, `app/reviewer.py` owns static checks, `app/generator.py` owns local templates, and `app/server.py` exposes API routes. The frontend remains `static/index.html`, `static/styles.css`, and `static/app.js`, with client-side filtering and structured report rendering.

**Tech Stack:** Python 3 standard library, `unittest`, static HTML/CSS/JavaScript, existing `scripts/smoke.py`.

---

## File Structure

- Modify `app/models.py`: add validation metadata fields and `RerunReport`.
- Modify `app/validator.py`: clamp validation options, track duration/failure metadata, add `rerun_case`.
- Modify `app/reviewer.py`: add dangerous-code and quality heuristics.
- Modify `app/generator.py`: add two-pointers, binary-search, and stack templates with localization.
- Modify `app/server.py`: accept validation timeout, add `/rerun`, preserve old response compatibility.
- Modify `tests/test_mvp.py`: add behavior tests before implementation.
- Modify `scripts/smoke.py`: exercise custom validation options and rerun API.
- Modify `static/index.html`: add validation controls and list filters.
- Modify `static/styles.css`: style controls, report summaries, failed case blocks, and filter rows.
- Modify `static/app.js`: client-side filtering, structured reports, rerun interaction, tag editing.
- Modify docs only if behavior docs need to reflect final API.

---

### Task 1: Validation Metadata and Rerun Core

**Files:**
- Modify: `app/models.py`
- Modify: `app/validator.py`
- Test: `tests/test_mvp.py`

- [ ] **Step 1: Write failing tests for validation metadata and rerun**

Add these tests to `tests/test_mvp.py`:

```python
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
```

Update imports:

```python
from app.validator import rerun_case, validate_problem
```

- [ ] **Step 2: Run tests and verify they fail for missing metadata/rerun**

Run:

```bash
python3 -m unittest tests.test_mvp.AlgorithmQuestionMVPTest.test_validation_report_includes_operational_metadata tests.test_mvp.AlgorithmQuestionMVPTest.test_rerun_case_compares_reference_and_bruteforce
```

Expected: fail because `rerun_case` or metadata fields do not exist.

- [ ] **Step 3: Add models**

In `app/models.py`, extend `ValidationReport`:

```python
    rounds: int = 0
    timeout_seconds: float = 0.0
    sample_count: int = 0
    duration_ms: int = 0
    first_failed_seed: int | None = None
    failure_stage: str | None = None
```

Add:

```python
@dataclass
class RerunReport:
    problem_id: str
    input: str
    expected: str
    actual: str
    passed: bool
    error: str = ""
    failure_stage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

- [ ] **Step 4: Implement validator metadata and `rerun_case`**

In `app/validator.py`:

```python
import time
from app.models import GeneratedProblem, RerunReport, ValidationCaseResult, ValidationReport
```

Track `started = time.perf_counter()`, set `failure_stage` and `first_failed_seed` when failures happen, and return:

```python
duration_ms=int((time.perf_counter() - started) * 1000),
rounds=rounds,
timeout_seconds=timeout_seconds,
sample_count=len(problem.samples),
first_failed_seed=first_failed_seed,
failure_stage=failure_stage,
```

Add:

```python
def rerun_case(problem: GeneratedProblem, case_input: str, timeout_seconds: float = 2.0) -> RerunReport:
    with tempfile.TemporaryDirectory(prefix="algo-rerun-") as tmp:
        root = Path(tmp)
        ref_path = root / "reference.py"
        brute_path = root / "brute.py"
        ref_path.write_text(problem.reference_solution, encoding="utf-8")
        brute_path.write_text(problem.brute_force_solution, encoding="utf-8")
        try:
            expected = _run_python(brute_path, case_input, timeout_seconds)
        except ValidationError as exc:
            return RerunReport(problem.id, case_input, "", "", False, str(exc), "brute_force")
        try:
            actual = _run_python(ref_path, case_input, timeout_seconds)
        except ValidationError as exc:
            return RerunReport(problem.id, case_input, expected.strip(), "", False, str(exc), "reference")
    return RerunReport(problem.id, case_input, expected.strip(), actual.strip(), expected.strip() == actual.strip())
```

- [ ] **Step 5: Run tests and verify they pass**

Run:

```bash
python3 -m unittest tests.test_mvp.AlgorithmQuestionMVPTest.test_validation_report_includes_operational_metadata tests.test_mvp.AlgorithmQuestionMVPTest.test_rerun_case_compares_reference_and_bruteforce
```

Expected: both tests pass.

---

### Task 2: Review Heuristics

**Files:**
- Modify: `app/reviewer.py`
- Test: `tests/test_mvp.py`

- [ ] **Step 1: Write failing review tests**

Add:

```python
    def test_review_flags_dangerous_generated_code(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.reference_solution = "import subprocess\nprint('x')\n"

        review = review_problem(problem)

        self.assertFalse(review.passed)
        self.assertTrue(any(issue.field == "reference_solution" and "dangerous" in issue.message for issue in review.issues))

    def test_review_warns_about_weak_constraints_and_missing_seed(self) -> None:
        problem = generate_problem(ProblemRequest(topic="array", use_llm=False))
        problem.constraints = ["values are valid"]
        problem.generator_code = "print('1 1')\nprint('1')\n"

        review = review_problem(problem)

        fields = [issue.field for issue in review.issues]
        self.assertIn("constraints", fields)
        self.assertIn("generator_code", fields)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
python3 -m unittest tests.test_mvp.AlgorithmQuestionMVPTest.test_review_flags_dangerous_generated_code tests.test_mvp.AlgorithmQuestionMVPTest.test_review_warns_about_weak_constraints_and_missing_seed
```

Expected: fail because the new checks do not exist.

- [ ] **Step 3: Implement review checks**

Add helper calls after Python syntax checks:

```python
    _check_dangerous_python(problem.reference_solution, "reference_solution", issues)
    _check_dangerous_python(problem.brute_force_solution, "brute_force_solution", issues)
    _check_dangerous_python(problem.generator_code, "generator_code", issues)
    _check_text_depth(problem, issues)
    _check_constraint_shape(problem.constraints, issues)
    _check_generator_seed(problem.generator_code, issues)
```

Add helpers:

```python
def _check_dangerous_python(source: str, field: str, issues: list[ReviewIssue]) -> None:
    patterns = ["import os", "from os", "import socket", "from socket", "import subprocess", "from subprocess", "import shutil", "from shutil", "import requests", "import urllib", "from urllib", "open(", "eval(", "exec("]
    if any(pattern in source for pattern in patterns):
        issues.append(ReviewIssue("error", field, "dangerous local-execution code is not allowed in this MVP"))


def _check_text_depth(problem: GeneratedProblem, issues: list[ReviewIssue]) -> None:
    if len(problem.statement.strip()) < 30:
        issues.append(ReviewIssue("warn", "statement", "statement is very short and may be ambiguous"))
    if len(problem.input_format.strip()) < 12:
        issues.append(ReviewIssue("warn", "input_format", "input format is too short"))
    if len(problem.output_format.strip()) < 12:
        issues.append(ReviewIssue("warn", "output_format", "output format is too short"))
    if len(problem.solution_explanation.strip()) < 30:
        issues.append(ReviewIssue("warn", "solution_explanation", "solution explanation is very short"))


def _check_constraint_shape(constraints: list[str], issues: list[ReviewIssue]) -> None:
    joined = " ".join(constraints)
    if constraints and not any(ch.isdigit() for ch in joined):
        issues.append(ReviewIssue("warn", "constraints", "constraints do not include numeric bounds"))


def _check_generator_seed(source: str, issues: list[ReviewIssue]) -> None:
    if "sys.argv" not in source and "seed" not in source:
        issues.append(ReviewIssue("warn", "generator_code", "generator does not appear to consume a seed argument"))
```

- [ ] **Step 4: Run review tests**

Run:

```bash
python3 -m unittest tests.test_mvp.AlgorithmQuestionMVPTest.test_review_flags_dangerous_generated_code tests.test_mvp.AlgorithmQuestionMVPTest.test_review_warns_about_weak_constraints_and_missing_seed
```

Expected: both tests pass.

---

### Task 3: Local Template Expansion

**Files:**
- Modify: `app/generator.py`
- Test: `tests/test_mvp.py`

- [ ] **Step 1: Write failing template coverage test**

Add:

```python
    def test_new_local_templates_review_and_validate(self) -> None:
        for topic in ["two pointers", "binary search", "stack"]:
            with self.subTest(topic=topic):
                problem = generate_problem(ProblemRequest(topic=topic, use_llm=False))
                review = review_problem(problem)
                self.assertTrue(review.passed, review.to_dict())
                validation = validate_problem(problem, rounds=15)
                self.assertTrue(validation.sample_passed, validation.to_dict())
                self.assertTrue(validation.fuzz_passed, validation.to_dict())
```

- [ ] **Step 2: Run test and verify it fails or routes to old fallback**

Run:

```bash
python3 -m unittest tests.test_mvp.AlgorithmQuestionMVPTest.test_new_local_templates_review_and_validate
```

Expected: fails until all new templates exist and pass stronger review.

- [ ] **Step 3: Add routing**

In `_mock_problem`:

```python
    if "two pointer" in topic or "two pointers" in topic or "双指针" in topic:
        return _localize_mock_problem(_two_pointers_problem(req, source), req.statement_language)
    if "binary" in topic or "二分" in topic or "search" in topic:
        return _localize_mock_problem(_binary_search_problem(req, source), req.statement_language)
    if "stack" in topic or "栈" in topic:
        return _localize_mock_problem(_stack_problem(req, source), req.statement_language)
```

- [ ] **Step 4: Implement the two-pointers template**

Add `_two_pointers_problem(req, source)` following the existing `_prefix_sum_problem` style.

Problem definition: sorted array, count pairs `(i, j)` with `i < j` and `a[i] + a[j] <= k`.

Required implementation details:

- title: `Count Pairs With Sum At Most K`;
- samples: `5 6\n1 2 3 4 5\n -> 4`, and `4 3\n1 1 1 1\n -> 6`;
- reference solution: sort input if needed, then use two pointers and add `right - left`;
- brute force: nested loops over all pairs;
- generator: seed-driven sorted arrays with `n <= 70`.

- [ ] **Step 5: Implement the binary-search template**

Add `_binary_search_problem(req, source)`.

Problem definition: sorted array, answer q queries asking for the first 1-based position whose value is at least `x`, or `-1`.

Required implementation details:

- title: `First Position At Least X`;
- samples: `5 3\n1 3 5 7 9\n4\n1\n10\n -> 3\n1\n-1`, and `3 2\n2 2 8\n2\n9\n -> 1\n-1`;
- reference solution: use `bisect_left`;
- brute force: scan from left to right;
- generator: seed-driven sorted arrays with `n <= 80` and `q <= 60`.

- [ ] **Step 6: Implement the stack template**

Add `_stack_problem(req, source)`.

Problem definition: for each array element, output the next greater element to its right, or `-1`.

Required implementation details:

- title: `Next Greater Element`;
- samples: `5\n2 1 3 2 4\n -> 3 3 4 4 -1`, and `4\n4 3 2 1\n -> -1 -1 -1 -1`;
- reference solution: monotonic decreasing stack of indices;
- brute force: scan right side for each index;
- generator: seed-driven arrays with `n <= 90`.

- [ ] **Step 7: Extend localization**

In `_localize_mock_problem`, add title-based Chinese replacements for the three new English titles:

```python
    if problem.title == "Count Pairs With Sum At Most K":
        problem.title = "和不超过 K 的配对数"
        problem.statement = "给定一个整数数组和整数 k，统计有多少个下标对 (i, j) 满足 1 <= i < j <= n 且 a[i] + a[j] <= k。"
        problem.input_format = "第一行包含两个整数 n 和 k。第二行包含 n 个整数。"
        problem.output_format = "输出一个整数，表示满足条件的下标对数量。"
        problem.tags = ["双指针", "排序", "数组"]
    if problem.title == "First Position At Least X":
        problem.title = "第一个不小于 X 的位置"
        problem.statement = "给定一个非降序整数数组，需要回答 q 次查询。每次给出 x，输出数组中第一个大于等于 x 的位置，下标从 1 开始；如果不存在，输出 -1。"
        problem.input_format = "第一行包含两个整数 n 和 q。第二行包含 n 个非降序整数。接下来 q 行，每行一个整数 x。"
        problem.output_format = "对每次查询单独输出一行答案。"
        problem.tags = ["二分", "数组", "查询"]
    if problem.title == "Next Greater Element":
        problem.title = "右侧第一个更大元素"
        problem.statement = "给定一个整数数组，对每个位置 i，求它右侧第一个严格大于 a[i] 的元素值；如果不存在则输出 -1。"
        problem.input_format = "第一行包含一个整数 n。第二行包含 n 个整数。"
        problem.output_format = "输出 n 个整数，第 i 个数表示位置 i 的答案。"
        problem.tags = ["单调栈", "数组"]
```

- [ ] **Step 8: Run template test**

Run:

```bash
python3 -m unittest tests.test_mvp.AlgorithmQuestionMVPTest.test_new_local_templates_review_and_validate
```

Expected: pass.

---

### Task 4: API and Smoke Coverage

**Files:**
- Modify: `app/server.py`
- Modify: `scripts/smoke.py`
- Test: `tests/test_mvp.py`

- [ ] **Step 1: Write server-free request parsing tests**

Add clamping helpers to `app/server.py` and test them directly from `tests/test_mvp.py`.

Add tests:

```python
    def test_server_clamps_validation_options(self) -> None:
        from app.server import _clamp_rounds, _clamp_timeout

        self.assertEqual(_clamp_rounds(0), 1)
        self.assertEqual(_clamp_rounds(1500), 1000)
        self.assertEqual(_clamp_rounds("12"), 12)
        self.assertEqual(_clamp_timeout(0.01), 0.2)
        self.assertEqual(_clamp_timeout(99), 10.0)
        self.assertEqual(_clamp_timeout("1.5"), 1.5)
```

Add helpers:

```python
def _clamp_rounds(value: object) -> int:
    return max(1, min(int(value), 1000))

def _clamp_timeout(value: object) -> float:
    return max(0.2, min(float(value), 10.0))
```

- [ ] **Step 2: Modify validation/package handlers**

In `_validate` and `_package`, read:

```python
rounds = _clamp_rounds(body.get("rounds", 100))
timeout_seconds = _clamp_timeout(body.get("timeout_seconds", 2.0))
report = validate_problem(problem, rounds=rounds, timeout_seconds=timeout_seconds)
```

- [ ] **Step 3: Add rerun route**

In `do_POST` before `/review`:

```python
        if parsed.path.startswith("/api/problems/") and parsed.path.endswith("/rerun"):
            problem_id = parsed.path.removeprefix("/api/problems/").removesuffix("/rerun")
            self._rerun(problem_id)
            return
```

Add handler:

```python
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
```

Update imports:

```python
from app.validator import ValidationError, rerun_case, validate_problem
```

- [ ] **Step 4: Update smoke**

In `_run_problem_flow`, after validation:

```python
    rerun = _post_json(
        f"{base_url}/api/problems/{problem_id}/rerun",
        {"input": problem["samples"][0]["input"], "timeout_seconds": 1.5},
        timeout=30,
    )
    _assert(rerun["passed"] is True, f"rerun passed for {problem_id}")
```

When validating, send:

```python
{"rounds": rounds, "timeout_seconds": 1.5}
```

- [ ] **Step 5: Run unit tests and smoke later with server**

Run unit tests:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: pass. Smoke is run after frontend work so the full loop reflects final behavior.

---

### Task 5: Frontend Workbench Controls and Reports

**Files:**
- Modify: `static/index.html`
- Modify: `static/styles.css`
- Modify: `static/app.js`

- [ ] **Step 1: Add controls and filters to HTML**

Add near toolbar:

```html
<label class="compact-field">
  <span>轮数</span>
  <input id="roundsInput" type="number" min="1" max="1000" value="100">
</label>
<label class="compact-field">
  <span>超时</span>
  <input id="timeoutInput" type="number" min="0.2" max="10" step="0.1" value="2">
</label>
```

Add above problem list:

```html
<div class="filter-row">
  <input id="problemSearchInput" class="filter-input" aria-label="搜索题目、知识点、标签">
  <select id="sourceFilter"><option value="all">全部来源</option><option value="llm">LLM</option><option value="mock">模板</option></select>
  <select id="languageFilter"><option value="all">全部语言</option><option value="zh">中文</option><option value="en">English</option></select>
</div>
```

- [ ] **Step 2: Wire DOM elements**

In `els`, add:

```javascript
  roundsInput: document.getElementById("roundsInput"),
  timeoutInput: document.getElementById("timeoutInput"),
  problemSearchInput: document.getElementById("problemSearchInput"),
  sourceFilter: document.getElementById("sourceFilter"),
  languageFilter: document.getElementById("languageFilter"),
```

Add state:

```javascript
  reruns: {},
```

- [ ] **Step 3: Implement filtering**

Update `renderProblemList()` to use:

```javascript
function filteredProblems() {
  const keyword = String(els.problemSearchInput?.value || "").trim().toLowerCase();
  const source = els.sourceFilter?.value || "all";
  const language = els.languageFilter?.value || "all";
  return state.problems.filter((problem) => {
    const haystack = [problem.id, problem.title, problem.topic, (problem.tags || []).join(" ")].join(" ").toLowerCase();
    return (!keyword || haystack.includes(keyword))
      && (source === "all" || problem.source === source || (source === "mock" && problem.source.startsWith("mock")))
      && (language === "all" || problem.statement_language === language);
  });
}
```

- [ ] **Step 4: Use validation controls**

Add:

```javascript
function validationOptions() {
  return {
    rounds: Number(els.roundsInput?.value || 100),
    timeout_seconds: Number(els.timeoutInput?.value || 2),
  };
}
```

Use it in `runValidate()` and `runPackage()`.

- [ ] **Step 5: Render structured reports**

Replace `renderReports()` with functions:

```javascript
function renderReports(problem) {
  const reports = state.reports[problem.id] || {};
  els.detailContent.innerHTML = `
    <h3>报告</h3>
    ${renderReviewSummary(reports.review)}
    ${renderValidationSummary(problem, reports.validation)}
    ${renderPackageSummary(reports.package)}
  `;
  bindReportActions(problem);
}
```

Add `renderReviewSummary`, `renderValidationSummary`, `renderFailedCase`, `renderPackageSummary`, and keep `renderReportBlock` only for raw JSON sections.

- [ ] **Step 6: Add rerun interaction**

Add:

```javascript
async function rerunFailedCase(index) {
  const problem = state.selected;
  const report = state.reports[problem.id]?.validation;
  const failed = report?.failed_cases?.[index];
  if (!problem || !failed) return;
  try {
    const data = await api(`/api/problems/${problem.id}/rerun`, {
      method: "POST",
      body: JSON.stringify({ input: failed.input, timeout_seconds: Number(els.timeoutInput?.value || 2) }),
    });
    state.reruns[`${problem.id}:${index}`] = data;
    renderAll();
    log("复跑完成", `passed=${data.passed}`, data.passed ? "ok" : "bad");
  } catch (err) {
    log("复跑失败", err.message, "bad");
  }
}
```

- [ ] **Step 7: Add tag editing**

In `renderEdit`, include:

```html
<label><span>标签，每行一个</span><textarea name="tags">${escapeHtml((problem.tags || []).join("\n"))}</textarea></label>
```

In `problemPatchFromEditForm`, include:

```javascript
tags: String(form.get("tags") || "").split("\n").map((item) => item.trim()).filter(Boolean),
```

- [ ] **Step 8: Style the UI additions**

Add CSS for `.compact-field`, `.filter-row`, `.filter-input`, `.report-card`, `.issue-list`, `.failed-case`, `.mini-toolbar`, `.rerun-result`.

- [ ] **Step 9: Manual browser check**

After backend and frontend are implemented, start the server and inspect:

```bash
python3 -m app.server
```

Open `http://127.0.0.1:18081/` and verify: controls fit, filters work, reports are readable, failed case actions do not overlap on desktop/mobile widths.

---

### Task 6: Final Verification and Docs

**Files:**
- Modify: `docs/API.md`
- Modify: `docs/VALIDATION.md`
- Modify: `docs/FRONTEND.md`

- [ ] **Step 1: Update docs**

Document:

- validation `timeout_seconds`;
- `/api/problems/{id}/rerun`;
- validation metadata fields;
- frontend filters, validation controls, and structured reports.

- [ ] **Step 2: Compile**

Run:

```bash
python3 -m py_compile app/*.py tests/*.py scripts/*.py
```

Expected: exit code 0.

- [ ] **Step 3: Unit tests**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: all tests pass.

- [ ] **Step 4: Smoke**

Start server:

```bash
python3 -m app.server
```

Run:

```bash
python3 -m scripts.smoke
```

Expected: JSON output with `"ok": true`.

- [ ] **Step 5: Git review**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: no whitespace errors; changed files are only the implementation, docs, and tests for this feature.

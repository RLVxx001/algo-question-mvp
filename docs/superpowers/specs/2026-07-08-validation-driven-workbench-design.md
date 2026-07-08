# Validation-Driven Workbench Upgrade Design

## Goal

Upgrade `algo-question-mvp` from a runnable prototype into a more useful local algorithm-question workbench. The upgrade keeps the current no-dependency Python service and static frontend, while making the generation, review, validation, failure triage, and export loop easier to trust and operate locally.

## Scope

This iteration focuses on a balanced improvement across backend logic and page interaction:

- Improve validation reports so a user can see what ran, how long it took, and where a failure happened.
- Add a single-case rerun path so failed generated inputs can be reproduced without rerunning the whole fuzz suite.
- Strengthen static review checks for obvious local-execution and problem-quality risks.
- Add a small set of local templates for common topics so the workbench remains useful without an LLM key.
- Improve the static page so reports are readable, validation parameters are adjustable, and problem lists are easier to scan.

This iteration does not introduce a database, job queue, user accounts, Docker sandboxing, or a frontend framework.

## Backend Design

### Validation Reports

`app.validator.validate_problem()` will continue to run:

1. sample output checks against `reference_solution`;
2. generator-driven random cases;
3. `brute_force_solution` vs `reference_solution` differential testing.

The returned `ValidationReport` should gain operational metadata:

- `rounds`: requested fuzz rounds after clamping;
- `timeout_seconds`: per-process timeout;
- `sample_count`: number of checked samples;
- `duration_ms`: total validation duration;
- `first_failed_seed`: seed for the first fuzz failure, or `null`;
- `failure_stage`: one of `sample`, `generator`, `brute_force`, `reference`, `compare`, or `null`.

Existing response fields stay compatible: `sample_passed`, `fuzz_passed`, `total_cases`, `failed_cases`, and `notes`.

### Single-Case Rerun

Add a backend function that runs only one supplied input against both solutions:

```text
rerun_case(problem, case_input, timeout_seconds)
  -> expected output from brute force
  -> actual output from reference solution
  -> pass/fail comparison
```

Expose it as:

```http
POST /api/problems/{problem_id}/rerun
Content-Type: application/json
```

Request:

```json
{
  "input": "5 6\n1 5 3 3 2\n",
  "timeout_seconds": 2
}
```

Response:

```json
{
  "problem_id": "prob_x",
  "input": "...",
  "expected": "...",
  "actual": "...",
  "passed": true,
  "error": ""
}
```

If either solution errors or times out, the response should still be structured and include the failing stage in `error`; the API should return `400` only for invalid request shape or missing input.

### Review Improvements

`app.reviewer.review_problem()` will keep the current required-field and Python syntax checks, then add warnings/errors for:

- dangerous local-execution imports or calls in generated code. `socket`, `subprocess`, `shutil`, `requests`, `urllib`, `open(`, `eval(`, and `exec(` are errors. `os` is also an error for this local prototype because generated solutions and generators should not need filesystem or process access;
- extremely short statement/input/output/solution fields;
- sample count below two remains an error;
- constraints that do not mention any numeric bound;
- generator code that does not consume a seed argument.

These checks are heuristic. They are meant to protect the local prototype from obvious bad outputs, not replace a real sandbox.

### Template Coverage

Keep the existing prefix-sum, string, and two-sum templates. Add templates for:

- two pointers: pair or subsequence style problem;
- binary search: minimum feasible value or first true position style problem;
- stack: next greater element or bracket-style problem.

Each template must include:

- Chinese and English localization;
- two samples;
- `reference_solution`;
- `brute_force_solution`;
- seed-driven `generator_code` with intentionally small cases for differential testing.

Topic matching stays simple keyword routing in `app.generator`; no taxonomy service is introduced.

## Frontend Design

The frontend remains a static tool surface in `static/index.html`, `static/styles.css`, and `static/app.js`.

### Workbench Controls

Add local validation controls near the action toolbar:

- rounds number input, default `100`, clamped by the server to `1..1000`;
- timeout seconds input, default `2`, clamped by the server to `0.2..10`;
- the validation button uses these controls instead of hard-coded rounds.

### Problem List Filtering

Add simple client-side filters:

- keyword search across title, topic, tags, and id;
- source filter: all, llm, mock, mock-after-llm-failure, mock-after-language-mismatch;
- language filter: all, zh, en.

No backend list-query API is needed for this iteration because the local dataset is small.

### Reports View

Replace raw JSON-first report rendering with structured sections:

- Review summary: pass/fail, score, issue counts, checks.
- Review issues: severity, field, message.
- Validation summary: sample result, fuzz result, rounds, timeout, duration, first failed seed, failure stage.
- Failed cases: input, expected, actual, reason, copy button, rerun button.
- Export summary: package directory and latest package validation/review results.

Raw JSON can remain available in a collapsed or secondary `<pre>` block for debugging.

### Rerun Interaction

For each failed case:

- `Copy Input` copies the failing input to clipboard when the browser supports it.
- `Rerun Case` calls `/api/problems/{id}/rerun` with the failed input.
- The rerun result appears inline under that failed case and is also written to the activity log.

### Editing

Keep the current edit form, but add editable fields for:

- tags, one per line.

Do not add a complex sample editor in this iteration. Sample editing is useful, but the validation/reporting and rerun workflow is higher priority and should remain the main scope.

## Data Flow

```text
Generate or workflow-create problem
  -> save data/problems/{id}.json
  -> user reviews/edits in browser
  -> review and validate APIs return structured reports
  -> frontend renders summaries and failed cases
  -> rerun API checks one failed input on demand
  -> package API writes data/packages/{id}/
```

Reports do not need their own database. The current in-memory frontend state and existing package report files are enough for this local workbench iteration.

## Error Handling

- Validation should return structured failure details where possible instead of collapsing all failures into a generic API error.
- API request-shape errors should return `400` with a short `error` message.
- Missing problem IDs should continue returning `404`.
- Frontend actions should disable only the active button while running and log failures with the server message.
- Rerun failures should be displayed inline so the user can compare original failure and rerun behavior.

## Testing Strategy

Use test-first implementation for backend behavior changes:

1. Add tests for validation metadata and first failed seed.
2. Add tests for single-case rerun pass/fail and timeout/error shape.
3. Add tests for dangerous-code review warnings/errors.
4. Add tests that new local templates pass review and validation.
5. Update smoke checks to exercise validation controls, rerun API, and package export.

For frontend behavior, rely on the existing smoke script plus manual browser verification because the project intentionally has no frontend test framework.

## Acceptance Criteria

- `python3 -m unittest discover -s tests -p 'test_*.py'` passes.
- `python3 -m py_compile app/*.py tests/*.py scripts/*.py` passes.
- `python3 -m scripts.smoke` passes against a running local server.
- A generated mock problem can be reviewed, validated with custom rounds, exported, and inspected in the structured report UI.
- A deliberately failing problem can show a failed case and rerun that case through the new endpoint.
- The workbench remains usable without `ALGO_LLM_API_KEY`.

## Follow-Ups

- Real code sandboxing with Docker, firejail, nsjail, or a remote judge service.
- Persistent report history outside exported packages.
- Rich sample editor and code editor with syntax highlighting.
- Downloadable package archive.
- Similarity checking against an existing problem bank.

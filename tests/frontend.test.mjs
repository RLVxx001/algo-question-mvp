import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function loadAppContext() {
  const elements = {};
  function makeElement() {
    const listeners = {};
    return {
      checked: false,
      classList: { toggle() {} },
      dataset: {},
      disabled: false,
      innerHTML: "",
      listeners,
      textContent: "",
      value: "",
      addEventListener(event, callback) {
        listeners[event] = callback;
      },
      prepend() {},
      querySelector() {
        return { dataset: {}, disabled: false, innerHTML: "" };
      },
    };
  }
  const documentStub = {
    createElement() {
      return { className: "", innerHTML: "", prepend() {} };
    },
    getElementById(id) {
      elements[id] = elements[id] || makeElement();
      return elements[id];
    },
    querySelectorAll() {
      return [];
    },
  };
  const context = {
    console,
    document: documentStub,
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    window: { location: { origin: "http://test.local" } },
    __elements: elements,
  };
  vm.createContext(context);
  const source = readFileSync(new URL("../static/app.js", import.meta.url), "utf8").replace(/\ninit\(\);\s*$/, "\n");
  vm.runInContext(source, context, { filename: "static/app.js" });
  return context;
}

function formValues(values) {
  return {
    get(name) {
      return values[name];
    },
  };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("buildGenerationPayload rejects blank topic before submit", () => {
  const context = loadAppContext();

  assert.equal(typeof context.buildGenerationPayload, "function");
  assert.throws(
    () =>
      context.buildGenerationPayload(
        formValues({
          topic: "   ",
          difficulty: "easy",
          statement_language: "zh",
          count: "1",
        }),
        { useLlmInput: { checked: false } },
      ),
    /topic is required/,
  );
});

test("api reports plain text error responses with status", async () => {
  const context = loadAppContext();
  context.fetch = async () => ({
    ok: false,
    status: 502,
    text: async () => "Bad gateway",
  });

  await assert.rejects(
    () => context.api("/api/problems"),
    (error) => {
      assert.equal(error.message, "Bad gateway");
      assert.equal(error.status, 502);
      assert.deepEqual(plain(error.payload), { error: "Bad gateway" });
      return true;
    },
  );
});

test("buildGenerationPayload normalizes count and language aliases", () => {
  const context = loadAppContext();

  const payload = context.buildGenerationPayload(
    formValues({
      topic: "  binary search  ",
      difficulty: "Medium",
      statement_language: "English",
      count: "9",
    }),
    { useLlmInput: { checked: true } },
  );

  assert.deepEqual(plain(payload), {
    topic: "binary search",
    difficulty: "medium",
    statement_language: "en",
    count: 5,
    use_llm: true,
  });
});

test("buildGenerationPayload rejects invalid generation fields", () => {
  const context = loadAppContext();

  assert.throws(
    () =>
      context.buildGenerationPayload(
        formValues({
          topic: "array",
          difficulty: "expert",
          statement_language: "zh",
          count: "1",
        }),
        { useLlmInput: { checked: false } },
      ),
    /difficulty must be easy, medium, or hard/,
  );

  assert.throws(
    () =>
      context.buildGenerationPayload(
        formValues({
          topic: "array",
          difficulty: "easy",
          statement_language: "fr",
          count: "1",
        }),
        { useLlmInput: { checked: false } },
      ),
    /statement_language must be zh or en/,
  );

  assert.throws(
    () =>
      context.buildGenerationPayload(
        formValues({
          topic: "array",
          difficulty: "easy",
          statement_language: "zh",
          count: "many",
        }),
        { useLlmInput: { checked: false } },
      ),
    /count must be an integer/,
  );
});

test("validationOptions normalizes validation controls", () => {
  const context = loadAppContext();

  assert.deepEqual(
    plain(context.validationOptions({
      roundsInput: { value: "1005" },
      timeoutInput: { value: "0.1" },
    })),
    { rounds: 1000, timeout_seconds: 0.2 },
  );

  assert.deepEqual(
    plain(context.validationOptions({
      roundsInput: { value: "0" },
      timeoutInput: { value: "11" },
    })),
    { rounds: 1, timeout_seconds: 10 },
  );

  assert.throws(
    () =>
      context.validationOptions({
        roundsInput: { value: "abc" },
        timeoutInput: { value: "2" },
      }),
    /rounds must be an integer/,
  );

  assert.throws(
    () =>
      context.validationOptions({
        roundsInput: { value: "10" },
        timeoutInput: { value: "fast" },
      }),
    /timeout_seconds must be a number/,
  );
});

test("rerunOptions normalizes rerun timeout control", () => {
  const context = loadAppContext();

  assert.deepEqual(
    plain(context.rerunOptions({ timeoutInput: { value: "0.1" } })),
    { timeout_seconds: 0.2 },
  );

  assert.deepEqual(
    plain(context.rerunOptions({ timeoutInput: { value: "11" } })),
    { timeout_seconds: 10 },
  );

  assert.throws(
    () => context.rerunOptions({ timeoutInput: { value: "fast" } }),
    /timeout_seconds must be a number/,
  );
});

test("problemPatchChanged detects no-op and changed edit payloads", () => {
  const context = loadAppContext();
  const problem = {
    title: "Original",
    statement: "Statement",
    input_format: "Input",
    output_format: "Output",
    constraints: ["1 <= n <= 10"],
    samples: [{ input: "1\n", output: "1\n" }],
    tags: ["array"],
    solution_explanation: "Use counting.",
    reference_solution: "print(input())\n",
    brute_force_solution: "print(input())\n",
    generator_code: "print('1')\n",
  };

  assert.equal(
    context.problemPatchChanged(problem, {
      title: "Original",
      constraints: ["1 <= n <= 10"],
      samples: [{ input: "1\n", output: "1\n" }],
      tags: ["array"],
    }),
    false,
  );

  assert.equal(
    context.problemPatchChanged(problem, {
      title: "Changed",
      constraints: ["1 <= n <= 10"],
      samples: [{ input: "1\n", output: "1\n" }],
      tags: ["array"],
    }),
    true,
  );
});

test("mergeReportState clears reruns when validation report changes", () => {
  const context = loadAppContext();

  const result = context.mergeReportState(
    { review: { passed: true } },
    {
      "prob_a:0": { passed: false },
      "prob_a:1": { passed: true },
      "prob_b:0": { passed: true },
    },
    "prob_a",
    { validation: { failed_cases: [] } },
  );

  assert.deepEqual(plain(result.reports), {
    review: { passed: true },
    validation: { failed_cases: [] },
  });
  assert.deepEqual(plain(result.reruns), {
    "prob_b:0": { passed: true },
  });
});

test("invalidateProblemState clears reports and current problem reruns", () => {
  const context = loadAppContext();

  const result = context.invalidateProblemState(
    { review: { passed: true }, validation: { failed_cases: [] } },
    {
      "prob_a:0": { passed: false },
      "prob_b:0": { passed: true },
    },
    "prob_a",
  );

  assert.deepEqual(plain(result.reports), {});
  assert.deepEqual(plain(result.reruns), {
    "prob_b:0": { passed: true },
  });
});

test("operation lock blocks duplicate operations until released", () => {
  const context = loadAppContext();

  assert.equal(context.beginOperation("review:prob_a"), true);
  assert.equal(context.beginOperation("review:prob_a"), false);
  assert.equal(context.isOperationBusy("review:prob_a"), true);
  context.endOperation("review:prob_a");
  assert.equal(context.isOperationBusy("review:prob_a"), false);
  assert.equal(context.beginOperation("review:prob_a"), true);
});

test("loadProblems logs list failures and preserves existing problems", async () => {
  const context = loadAppContext();
  const existing = [
    {
      id: "prob_a",
      title: "Existing",
      topic: "array",
      difficulty: "easy",
      source: "mock",
      statement_language: "zh",
      tags: ["array"],
    },
  ];
  const logs = [];

  context.fetch = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ error: "store unavailable" }),
  });
  context.log = (title, message, level) => {
    logs.push({ title, message, level });
  };
  vm.runInContext(`state.problems = ${JSON.stringify(existing)};`, context);

  await assert.doesNotReject(() => context.loadProblems(false));

  assert.deepEqual(plain(vm.runInContext("state.problems", context)), existing);
  assert.deepEqual(logs, [{ title: "题目列表读取失败", message: "store unavailable", level: "warn" }]);
});

test("loadProblems clears selected problem when it disappears from refreshed list", async () => {
  const context = loadAppContext();
  const logs = [];
  let renderCount = 0;
  const selected = {
    id: "prob_deleted",
    title: "Deleted",
    topic: "array",
    difficulty: "easy",
    source: "mock",
    statement_language: "zh",
    tags: ["array"],
  };
  const kept = {
    id: "prob_kept",
    title: "Kept",
    topic: "graph",
    difficulty: "medium",
    source: "mock",
    statement_language: "zh",
    tags: ["graph"],
  };

  context.fetch = async () => ({
    ok: true,
    json: async () => ({ list: [kept] }),
  });
  context.log = (title, message, level) => {
    logs.push({ title, message, level });
  };
  context.renderAll = () => {
    renderCount += 1;
  };
  vm.runInContext(
    `
      state.problems = ${JSON.stringify([selected, kept])};
      state.selected = ${JSON.stringify(selected)};
      state.activeTab = "reports";
      state.reports.prob_deleted = { review: { passed: true } };
      state.workflows.prob_deleted = { status: "waiting_user" };
      state.similarity.prob_deleted = { has_risk: false };
      state.reruns["prob_deleted:0"] = { passed: true };
    `,
    context,
  );

  await context.loadProblems(false);

  assert.deepEqual(plain(vm.runInContext("state.problems.map((problem) => problem.id)", context)), ["prob_kept"]);
  assert.equal(vm.runInContext("state.selected", context), null);
  assert.equal(vm.runInContext("state.activeTab", context), "statement");
  assert.equal(vm.runInContext("'prob_deleted' in state.reports", context), false);
  assert.equal(vm.runInContext("'prob_deleted' in state.workflows", context), false);
  assert.equal(vm.runInContext("'prob_deleted' in state.similarity", context), false);
  assert.equal(vm.runInContext("'prob_deleted:0' in state.reruns", context), false);
  assert.equal(renderCount, 1);
  assert.deepEqual(logs, [
    {
      title: "当前题目已移除",
      message: "刷新后当前题目不在列表中，已清空详情。",
      level: "warn",
    },
  ]);
});

test("refreshProblems disables refresh button and ignores duplicate refreshes", async () => {
  const context = loadAppContext();
  let resolveRefresh;
  let requestCount = 0;
  const refreshResponse = new Promise((resolve) => {
    resolveRefresh = resolve;
  });

  context.fetch = async () => {
    requestCount += 1;
    await refreshResponse;
    return {
      ok: true,
      json: async () => ({ list: [] }),
    };
  };
  context.__elements.refreshButton.innerHTML = `<span>刷新</span>`;

  const first = context.refreshProblems();
  const second = context.refreshProblems();

  assert.equal(requestCount, 1);
  assert.equal(context.__elements.refreshButton.disabled, true);
  assert.match(context.__elements.refreshButton.innerHTML, /刷新中/);
  resolveRefresh();
  await Promise.all([first, second]);
  assert.equal(context.__elements.refreshButton.disabled, false);
  assert.equal(context.__elements.refreshButton.innerHTML, `<span>刷新</span>`);
  assert.equal(context.isOperationBusy("refresh"), false);
});

test("renderProblemList warns when filters hide the selected problem", () => {
  const context = loadAppContext();
  const selected = {
    id: "prob_selected",
    title: "Hidden Array",
    topic: "array",
    difficulty: "easy",
    source: "mock",
    statement_language: "zh",
    tags: ["array"],
  };
  const visible = {
    id: "prob_visible",
    title: "Visible Graph",
    topic: "graph",
    difficulty: "medium",
    source: "llm",
    statement_language: "en",
    tags: ["graph"],
  };

  context.__elements.problemSearchInput.value = "graph";
  vm.runInContext(
    `
      state.problems = ${JSON.stringify([selected, visible])};
      state.selected = ${JSON.stringify(selected)};
    `,
    context,
  );

  context.renderProblemList();

  assert.match(context.__elements.problemList.innerHTML, /当前详情已被筛选隐藏/);
  assert.match(context.__elements.problemList.innerHTML, /Visible Graph/);
  assert.doesNotMatch(context.__elements.problemList.innerHTML, /Hidden Array/);
});

test("loadWorkflow logs non-404 failures but ignores missing workflow records", async () => {
  const context = loadAppContext();
  const logs = [];
  context.log = (title, message, level) => {
    logs.push({ title, message, level });
  };

  context.fetch = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ error: "workflow not found" }),
  });
  await context.loadWorkflow("prob_a");

  context.fetch = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ error: "workflow store unavailable" }),
  });
  await context.loadWorkflow("prob_a");

  assert.deepEqual(logs, [
    {
      title: "流程读取失败",
      message: "workflow store unavailable",
      level: "warn",
    },
  ]);
});

test("selectProblem removes missing problem from list without throwing", async () => {
  const context = loadAppContext();
  const logs = [];
  let renderCount = 0;
  const problems = [
    {
      id: "prob_missing",
      title: "Missing",
      topic: "array",
      difficulty: "easy",
      source: "mock",
      statement_language: "zh",
      tags: ["array"],
    },
    {
      id: "prob_kept",
      title: "Kept",
      topic: "graph",
      difficulty: "medium",
      source: "mock",
      statement_language: "zh",
      tags: ["graph"],
    },
  ];

  context.fetch = async () => ({
    ok: false,
    status: 404,
    json: async () => ({ error: "problem not found" }),
  });
  context.log = (title, message, level) => {
    logs.push({ title, message, level });
  };
  context.renderAll = () => {
    renderCount += 1;
  };
  vm.runInContext(
    `
      state.problems = ${JSON.stringify(problems)};
      state.selected = ${JSON.stringify(problems[0])};
      state.reports.prob_missing = { review: { passed: true } };
      state.workflows.prob_missing = { status: "waiting_user" };
      state.similarity.prob_missing = { has_risk: false };
      state.reruns["prob_missing:0"] = { passed: true };
    `,
    context,
  );

  await assert.doesNotReject(() => context.selectProblem("prob_missing"));

  assert.deepEqual(
    plain(vm.runInContext("state.problems.map((problem) => problem.id)", context)),
    ["prob_kept"],
  );
  assert.equal(vm.runInContext("state.selected", context), null);
  assert.equal(vm.runInContext("'prob_missing' in state.reports", context), false);
  assert.equal(vm.runInContext("'prob_missing' in state.workflows", context), false);
  assert.equal(vm.runInContext("'prob_missing' in state.similarity", context), false);
  assert.equal(vm.runInContext("'prob_missing:0' in state.reruns", context), false);
  assert.equal(renderCount, 1);
  assert.deepEqual(logs, [
    {
      title: "题目不存在",
      message: "列表中的题目已不存在或无法读取，已从当前列表移除。",
      level: "warn",
    },
  ]);
});

test("selectProblem clears stale problem when reports endpoint returns not found", async () => {
  const context = loadAppContext();
  const logs = [];
  let renderCount = 0;
  const requestedPaths = [];
  const stale = {
    id: "prob_stale",
    title: "Stale",
    topic: "array",
    difficulty: "easy",
    source: "mock",
    statement_language: "zh",
    tags: ["array"],
  };
  const kept = {
    id: "prob_kept",
    title: "Kept",
    topic: "graph",
    difficulty: "medium",
    source: "mock",
    statement_language: "zh",
    tags: ["graph"],
  };

  context.fetch = async (path) => {
    requestedPaths.push(path);
    if (path === "/api/problems/prob_stale") {
      return {
        ok: true,
        json: async () => stale,
      };
    }
    if (path === "/api/problems/prob_stale/reports") {
      return {
        ok: false,
        status: 404,
        json: async () => ({ error: "problem not found" }),
      };
    }
    throw new Error(`unexpected request: ${path}`);
  };
  context.log = (title, message, level) => {
    logs.push({ title, message, level });
  };
  context.renderAll = () => {
    renderCount += 1;
  };
  vm.runInContext(
    `
      state.problems = ${JSON.stringify([stale, kept])};
      state.reports.prob_stale = { review: { passed: true } };
      state.workflows.prob_stale = { status: "waiting_user" };
      state.similarity.prob_stale = { has_risk: false };
      state.reruns["prob_stale:0"] = { passed: true };
    `,
    context,
  );

  await assert.doesNotReject(() => context.selectProblem("prob_stale"));

  assert.deepEqual(requestedPaths, ["/api/problems/prob_stale", "/api/problems/prob_stale/reports"]);
  assert.deepEqual(plain(vm.runInContext("state.problems.map((problem) => problem.id)", context)), ["prob_kept"]);
  assert.equal(vm.runInContext("state.selected", context), null);
  assert.equal(vm.runInContext("'prob_stale' in state.reports", context), false);
  assert.equal(vm.runInContext("'prob_stale' in state.workflows", context), false);
  assert.equal(vm.runInContext("'prob_stale' in state.similarity", context), false);
  assert.equal(vm.runInContext("'prob_stale:0' in state.reruns", context), false);
  assert.equal(renderCount, 1);
  assert.deepEqual(logs, [
    {
      title: "题目不存在",
      message: "列表中的题目已不存在或无法读取，已从当前列表移除。",
      level: "warn",
    },
  ]);
});

test("selectProblem ignores stale responses from earlier selections", async () => {
  const context = loadAppContext();
  let resolveSlow;
  const slowResponse = new Promise((resolve) => {
    resolveSlow = resolve;
  });
  const fast = {
    id: "prob_fast",
    title: "Fast",
    topic: "graph",
    difficulty: "medium",
    source: "mock",
    statement_language: "zh",
    tags: ["graph"],
  };
  const slow = {
    id: "prob_slow",
    title: "Slow",
    topic: "array",
    difficulty: "easy",
    source: "mock",
    statement_language: "zh",
    tags: ["array"],
  };

  context.fetch = async (path) => {
    if (path === "/api/problems/prob_slow") {
      await slowResponse;
      return {
        ok: true,
        json: async () => slow,
      };
    }
    if (path === "/api/problems/prob_fast") {
      return {
        ok: true,
        json: async () => fast,
      };
    }
    if (path.endsWith("/reports")) {
      return {
        ok: true,
        json: async () => ({}),
      };
    }
    if (path.endsWith("/similar")) {
      return {
        ok: true,
        json: async () => ({ candidates: [] }),
      };
    }
    if (path.endsWith("/workflow")) {
      return {
        ok: false,
        status: 404,
        json: async () => ({ error: "workflow not found" }),
      };
    }
    throw new Error(`unexpected request: ${path}`);
  };
  context.renderAll = () => {};

  const first = context.selectProblem("prob_slow");
  const second = context.selectProblem("prob_fast");
  await second;
  resolveSlow();
  await first;

  assert.equal(vm.runInContext("state.selected.id", context), "prob_fast");
});

test("forgetProblem invalidates pending selection responses for the removed problem", async () => {
  const context = loadAppContext();
  let resolveSlow;
  const slowResponse = new Promise((resolve) => {
    resolveSlow = resolve;
  });
  const slow = {
    id: "prob_slow",
    title: "Slow",
    topic: "array",
    difficulty: "easy",
    source: "mock",
    statement_language: "zh",
    tags: ["array"],
  };

  context.fetch = async (path) => {
    if (path === "/api/problems/prob_slow") {
      await slowResponse;
      return {
        ok: true,
        json: async () => slow,
      };
    }
    if (path.endsWith("/reports")) {
      return {
        ok: true,
        json: async () => ({}),
      };
    }
    if (path.endsWith("/similar")) {
      return {
        ok: true,
        json: async () => ({ candidates: [] }),
      };
    }
    if (path.endsWith("/workflow")) {
      return {
        ok: false,
        status: 404,
        json: async () => ({ error: "workflow not found" }),
      };
    }
    throw new Error(`unexpected request: ${path}`);
  };
  context.renderAll = () => {};
  vm.runInContext(`state.problems = ${JSON.stringify([slow])};`, context);

  const pending = context.selectProblem("prob_slow");
  vm.runInContext(`forgetProblem("prob_slow");`, context);
  resolveSlow();
  await pending;

  assert.equal(vm.runInContext("state.selected", context), null);
  assert.deepEqual(plain(vm.runInContext("state.problems", context)), []);
});

test("deleteSelectedProblem removes deleted problem locally when refresh fails", async () => {
  const context = loadAppContext();
  const logs = [];
  const requestedPaths = [];
  const deleted = {
    id: "prob_deleted",
    title: "Deleted",
    topic: "array",
    difficulty: "easy",
    source: "mock",
    statement_language: "zh",
    tags: ["array"],
  };
  const kept = {
    id: "prob_kept",
    title: "Kept",
    topic: "graph",
    difficulty: "medium",
    source: "mock",
    statement_language: "zh",
    tags: ["graph"],
  };

  context.window.confirm = () => true;
  context.fetch = async (path, options = {}) => {
    requestedPaths.push({ path, method: options.method || "GET" });
    if (path === "/api/problems/prob_deleted" && options.method === "DELETE") {
      return {
        ok: true,
        json: async () => ({ problem_id: "prob_deleted", removed_package: true }),
      };
    }
    if (path === "/api/problems") {
      return {
        ok: false,
        status: 500,
        json: async () => ({ error: "store unavailable" }),
      };
    }
    throw new Error(`unexpected request: ${path}`);
  };
  context.log = (title, message, level) => {
    logs.push({ title, message, level });
  };
  vm.runInContext(
    `
      state.problems = ${JSON.stringify([deleted, kept])};
      state.selected = ${JSON.stringify(deleted)};
      state.activeTab = "reports";
      state.reports.prob_deleted = { review: { passed: true } };
      state.workflows.prob_deleted = { status: "waiting_user" };
      state.similarity.prob_deleted = { has_risk: false };
      state.reruns["prob_deleted:0"] = { passed: true };
    `,
    context,
  );

  await context.deleteSelectedProblem();

  assert.deepEqual(requestedPaths, [
    { path: "/api/problems/prob_deleted", method: "DELETE" },
    { path: "/api/problems", method: "GET" },
  ]);
  assert.deepEqual(plain(vm.runInContext("state.problems.map((problem) => problem.id)", context)), ["prob_kept"]);
  assert.equal(vm.runInContext("state.selected", context), null);
  assert.equal(vm.runInContext("state.activeTab", context), "statement");
  assert.equal(vm.runInContext("'prob_deleted' in state.reports", context), false);
  assert.equal(vm.runInContext("'prob_deleted' in state.workflows", context), false);
  assert.equal(vm.runInContext("'prob_deleted' in state.similarity", context), false);
  assert.equal(vm.runInContext("'prob_deleted:0' in state.reruns", context), false);
  assert.deepEqual(logs, [
    { title: "题目列表读取失败", message: "store unavailable", level: "warn" },
    { title: "题目已删除", message: "prob_deleted / package=true", level: "ok" },
  ]);
});

test("runReview ignores duplicate clicks while request is in flight", async () => {
  const context = loadAppContext();
  let resolveReview;
  let requestCount = 0;
  const reviewResponse = new Promise((resolve) => {
    resolveReview = resolve;
  });
  context.fetch = async () => {
    requestCount += 1;
    await reviewResponse;
    return {
      ok: true,
      json: async () => ({ problem_id: "prob_a", passed: true, score: 95, issues: [], checks: [] }),
    };
  };
  context.renderAll = () => {};
  vm.runInContext(
    `
      state.selected = { id: "prob_a", title: "Problem" };
      state.activeTab = "reports";
    `,
    context,
  );

  const first = context.runReview();
  const second = context.runReview();

  assert.equal(requestCount, 1);
  resolveReview();
  await Promise.all([first, second]);
  assert.equal(context.isOperationBusy("review:prob_a"), false);
});

test("finishing stale review keeps current problem review button disabled", async () => {
  const context = loadAppContext();
  let resolveReview;
  const reviewResponse = new Promise((resolve) => {
    resolveReview = resolve;
  });

  context.__elements.reviewButton.innerHTML = `<span class="button-icon">?</span><span>审查</span>`;
  context.fetch = async () => {
    await reviewResponse;
    return {
      ok: true,
      json: async () => ({ problem_id: "prob_a", passed: true, score: 95, issues: [], checks: [] }),
    };
  };
  vm.runInContext(
    `
      state.selected = { id: "prob_a", title: "Problem A" };
      state.activeTab = "reports";
    `,
    context,
  );

  const pending = context.runReview();
  vm.runInContext(
    `
      state.selected = { id: "prob_b", title: "Problem B" };
      state.busy["review:prob_b"] = true;
    `,
    context,
  );
  resolveReview();
  await pending;

  assert.equal(context.__elements.reviewButton.disabled, true);
  assert.match(context.__elements.reviewButton.innerHTML, /审查中/);
  assert.equal(vm.runInContext("state.busy['review:prob_b']", context), true);
});

test("renderAll keeps selected problem action buttons disabled while operations are busy", () => {
  const context = loadAppContext();
  vm.runInContext(
    `
      state.selected = { id: "prob_a", title: "Problem" };
      state.activeTab = "reports";
      state.busy = {
        "review:prob_a": true,
        "validate:prob_a": true,
        "package:prob_a": true,
        "delete:prob_a": true
      };
    `,
    context,
  );

  context.renderAll();

  assert.equal(context.__elements.reviewButton.disabled, true);
  assert.equal(context.__elements.validateButton.disabled, true);
  assert.equal(context.__elements.packageButton.disabled, true);
  assert.equal(context.__elements.deleteButton.disabled, true);
});

test("renderWorkflow disables continue button while workflow operation is busy", () => {
  const context = loadAppContext();
  let continueButton = null;
  context.document.getElementById = (id) => {
    if (id === "continueWorkflowButton") {
      continueButton = { addEventListener() {} };
      return continueButton;
    }
    return context.__elements[id] || (context.__elements[id] = {
      classList: { toggle() {} },
      dataset: {},
      disabled: false,
      innerHTML: "",
      textContent: "",
      addEventListener() {},
      prepend() {},
      querySelector() {
        return { dataset: {}, disabled: false, innerHTML: "" };
      },
    });
  };
  vm.runInContext(
    `
      state.selected = { id: "prob_a", title: "Problem" };
      state.workflows.prob_a = {
        problem_id: "prob_a",
        status: "waiting_user",
        current_step: "statement",
        steps: [{ key: "statement", name: "题面", status: "waiting_user", mode: "manual", summary: "待确认" }]
      };
      state.busy["workflow:prob_a"] = true;
    `,
    context,
  );

  context.renderWorkflow(vm.runInContext("state.selected", context));

  assert.match(context.__elements.detailContent.innerHTML, /continueWorkflowButton[\s\S]*disabled/);
});

test("renderEdit keeps edit actions disabled while edit or workflow operation is busy", () => {
  const context = loadAppContext();
  const problem = {
    id: "prob_a",
    title: "Problem",
    statement: "Statement",
    input_format: "Input",
    output_format: "Output",
    constraints: [],
    samples: [],
    tags: [],
    solution_explanation: "Solution",
    reference_solution: "print(1)",
    brute_force_solution: "print(1)",
    generator_code: "print(1)",
  };

  vm.runInContext(
    `
      state.busy["edit:prob_a"] = true;
      state.busy["workflow:prob_a"] = true;
    `,
    context,
  );

  context.renderEdit(problem);

  assert.match(context.__elements.detailContent.innerHTML, /type="submit"[\s\S]*disabled[\s\S]*保存中/);
  assert.match(context.__elements.detailContent.innerHTML, /saveAndContinueButton[\s\S]*disabled[\s\S]*流程处理中/);
});

test("saveEdit rerenders after releasing edit busy state", async () => {
  const context = loadAppContext();
  const problem = {
    id: "prob_a",
    title: "Original title",
    statement: "Statement",
    input_format: "Input",
    output_format: "Output",
    constraints: [],
    samples: [],
    tags: [],
    solution_explanation: "Solution",
    reference_solution: "print(1)",
    brute_force_solution: "print(1)",
    generator_code: "print(1)",
  };
  const editedValues = {
    title: "Edited title",
    statement: problem.statement,
    input_format: problem.input_format,
    output_format: problem.output_format,
    constraints: "",
    sample_input: [],
    sample_output: [],
    tags: "",
    solution_explanation: problem.solution_explanation,
    reference_solution: problem.reference_solution,
    brute_force_solution: problem.brute_force_solution,
    generator_code: problem.generator_code,
  };
  const busyStates = [];

  context.FormData = class FakeFormData {
    constructor(form) {
      this.values = form.__values || {};
    }

    get(name) {
      const value = this.values[name];
      return Array.isArray(value) ? value[0] : value;
    }

    getAll(name) {
      const value = this.values[name];
      if (Array.isArray(value)) return value;
      return value == null ? [] : [value];
    }
  };
  context.renderAll = () => {
    busyStates.push(context.isOperationBusy("edit:prob_a"));
  };
  context.fetch = async (path) => ({
    ok: true,
    json: async () =>
      path.endsWith("/similar")
        ? { similar: [] }
        : { ...problem, ...editedValues, id: problem.id, changed: true },
  });
  vm.runInContext(`state.selected = ${JSON.stringify(problem)};`, context);

  await context.saveEdit({
    preventDefault() {},
    target: { __values: editedValues },
  });

  assert.equal(busyStates.at(-1), false);
});

test("renderFailedCase disables rerun button while rerun is busy", () => {
  const context = loadAppContext();
  vm.runInContext(`state.busy["rerun:prob_a:0"] = true;`, context);

  const html = context.renderFailedCase(
    { id: "prob_a" },
    { input: "1", expected: "1", actual: "0", reason: "wrong answer" },
    0,
  );

  assert.match(html, /rerun-case-button[\s\S]*disabled/);
  assert.match(html, /复跑中/);
});

test("renderFailedCase disables rerun button for truncated failed input", () => {
  const context = loadAppContext();

  const html = context.renderFailedCase(
    { id: "prob_a" },
    { input: "1\n... truncated ...", expected: "1", actual: "0", reason: "wrong answer" },
    0,
  );

  assert.match(html, /rerun-case-button[\s\S]*disabled/);
  assert.match(html, /输入已截断/);
});

test("renderFailedCase enables rerun button for empty failed input", () => {
  const context = loadAppContext();

  const html = context.renderFailedCase(
    { id: "prob_a" },
    { input: "", expected: "1", actual: "0", reason: "wrong answer" },
    0,
  );

  assert.match(html, /rerun-case-button"[^>]*>复跑用例/);
  assert.doesNotMatch(html, /rerun-case-button"[^>]*disabled/);
});

test("rerunFailedCase skips truncated failed input", async () => {
  const context = loadAppContext();
  const logs = [];
  const problem = {
    id: "prob_a",
    title: "Problem A",
    source: "mock",
    statement_language: "zh",
  };
  const validation = {
    sample_passed: false,
    fuzz_passed: false,
    failed_cases: [{ input: "1\n... truncated ...", expected: "1\n", actual: "0\n", reason: "wrong answer" }],
  };

  context.fetch = async () => {
    throw new Error("rerun request should not be sent");
  };
  context.log = (title, message, level) => {
    logs.push({ title, message, level });
  };
  vm.runInContext(
    `
      state.selected = ${JSON.stringify(problem)};
      state.reports.prob_a = { validation: ${JSON.stringify(validation)} };
    `,
    context,
  );

  await context.rerunFailedCase(0);

  assert.equal(context.isOperationBusy("rerun:prob_a:0"), false);
  assert.deepEqual(logs, [
    {
      title: "复跑不可用",
      message: "失败输入已被截断，不能代表完整用例。",
      level: "warn",
    },
  ]);
});

test("rerunFailedCase sends empty failed input", async () => {
  const context = loadAppContext();
  const problem = {
    id: "prob_a",
    title: "Problem A",
    source: "mock",
    statement_language: "zh",
  };
  const validation = {
    sample_passed: false,
    fuzz_passed: false,
    failed_cases: [{ input: "", expected: "1\n", actual: "0\n", reason: "wrong answer" }],
  };
  let requestCount = 0;

  context.fetch = async (path, options = {}) => {
    requestCount += 1;
    assert.equal(path, "/api/problems/prob_a/rerun");
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { input: "", timeout_seconds: 2 });
    return {
      ok: true,
      json: async () => ({ passed: true, expected: "1\n", actual: "1\n" }),
    };
  };
  vm.runInContext(
    `
      state.selected = ${JSON.stringify(problem)};
      state.activeTab = "reports";
      state.reports.prob_a = { validation: ${JSON.stringify(validation)} };
    `,
    context,
  );

  await context.rerunFailedCase(0);

  assert.equal(requestCount, 1);
  assert.equal(context.isOperationBusy("rerun:prob_a:0"), false);
});

test("rerunFailedCase rerenders after releasing rerun busy state", async () => {
  const context = loadAppContext();
  const problem = {
    id: "prob_a",
    title: "Problem A",
    source: "mock",
    statement_language: "zh",
  };
  const validation = {
    sample_passed: false,
    fuzz_passed: false,
    failed_cases: [{ input: "1\n", expected: "1\n", actual: "0\n", reason: "wrong answer" }],
  };

  context.fetch = async (path, options = {}) => {
    assert.equal(path, "/api/problems/prob_a/rerun");
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { input: "1\n", timeout_seconds: 2 });
    return {
      ok: true,
      json: async () => ({ passed: true, expected: "1\n", actual: "1\n" }),
    };
  };
  vm.runInContext(
    `
      state.selected = ${JSON.stringify(problem)};
      state.activeTab = "reports";
      state.reports.prob_a = { validation: ${JSON.stringify(validation)} };
    `,
    context,
  );

  await context.rerunFailedCase(0);

  assert.equal(context.isOperationBusy("rerun:prob_a:0"), false);
  assert.match(context.__elements.detailContent.innerHTML, /复跑：通过/);
  assert.match(context.__elements.detailContent.innerHTML, /复跑用例/);
  assert.doesNotMatch(context.__elements.detailContent.innerHTML, /复跑中/);
});

test("continueWorkflow captures edit patch before rerendering", async () => {
  const context = loadAppContext();
  const originalProblem = {
    id: "prob_a",
    title: "Original title",
    statement: "Original statement",
    input_format: "Input",
    output_format: "Output",
    constraints: ["1 <= n <= 10"],
    samples: [{ input: "1", output: "1" }],
    tags: ["array"],
    solution_explanation: "Original solution",
    reference_solution: "print(input())\n",
    brute_force_solution: "print(input())\n",
    generator_code: "print('1')\n",
  };
  const editedValues = {
    title: "Edited title",
    statement: originalProblem.statement,
    input_format: originalProblem.input_format,
    output_format: originalProblem.output_format,
    constraints: originalProblem.constraints.join("\n"),
    sample_input: ["1"],
    sample_output: ["1"],
    tags: originalProblem.tags.join("\n"),
    solution_explanation: originalProblem.solution_explanation,
    reference_solution: originalProblem.reference_solution,
    brute_force_solution: originalProblem.brute_force_solution,
    generator_code: originalProblem.generator_code,
  };
  const originalValues = { ...editedValues, title: originalProblem.title };
  let capturedPayload = null;

  context.FormData = class FakeFormData {
    constructor(form) {
      this.values = form.__values || {};
    }

    get(name) {
      const value = this.values[name];
      return Array.isArray(value) ? value[0] : value;
    }

    getAll(name) {
      const value = this.values[name];
      if (Array.isArray(value)) return value;
      return value == null ? [] : [value];
    }
  };
  context.__elements.editForm = { __values: editedValues };
  context.renderAll = () => {
    context.__elements.editForm = { __values: originalValues };
  };
  context.fetch = async (path, options) => {
    capturedPayload = JSON.parse(options.body);
    return {
      ok: true,
      json: async () => ({
        problem: { ...originalProblem, title: capturedPayload.patch?.title || originalProblem.title },
        workflow: {
          problem_id: originalProblem.id,
          status: "completed",
          current_step: "done",
          steps: [],
        },
        result: {},
        changed: Boolean(capturedPayload.patch),
      }),
    };
  };
  vm.runInContext(
    `
      state.selected = ${JSON.stringify(originalProblem)};
      state.activeTab = "edit";
      state.workflows[${JSON.stringify(originalProblem.id)}] = {
        problem_id: ${JSON.stringify(originalProblem.id)},
        status: "waiting_user",
        current_step: "statement",
        steps: [{ key: "statement", status: "waiting_user", summary: "" }]
      };
    `,
    context,
  );

  await context.continueWorkflow(true);

  assert.equal(capturedPayload.patch.title, "Edited title");
});

test("continueWorkflow restores workflow state when request fails", async () => {
  const context = loadAppContext();
  const problemId = "prob_a";
  context.fetch = async () => ({
    ok: false,
    status: 400,
    json: async () => ({ error: "step cannot continue" }),
  });
  vm.runInContext(
    `
      state.selected = { id: ${JSON.stringify(problemId)}, title: "Problem" };
      state.activeTab = "workflow";
      state.workflows[${JSON.stringify(problemId)}] = {
        problem_id: ${JSON.stringify(problemId)},
        status: "waiting_user",
        current_step: "statement",
        steps: [{ key: "statement", status: "waiting_user", summary: "待确认" }]
      };
    `,
    context,
  );

  await context.continueWorkflow(false);

  const workflow = vm.runInContext(`state.workflows[${JSON.stringify(problemId)}]`, context);
  assert.equal(workflow.status, "waiting_user");
  assert.equal(workflow.steps[0].status, "waiting_user");
  assert.equal(workflow.steps[0].summary, "待确认");
});

test("topic input event clears generation validation state", () => {
  const context = loadAppContext();

  context.bindEvents();

  const callback = context.__elements.topicInput.listeners.input;
  assert.equal(typeof callback, "function");
  assert.doesNotThrow(() => callback());
});

test("count input event clears generation validation state", () => {
  const context = loadAppContext();

  context.bindEvents();

  const callback = context.__elements.countInput.listeners.input;
  assert.equal(typeof callback, "function");
  assert.doesNotThrow(() => callback());
});

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

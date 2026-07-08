import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function loadAppContext() {
  const element = {
    checked: false,
    classList: { toggle() {} },
    dataset: {},
    disabled: false,
    innerHTML: "",
    textContent: "",
    value: "",
    addEventListener() {},
    prepend() {},
    querySelector() {
      return { dataset: {}, disabled: false, innerHTML: "" };
    },
  };
  const documentStub = {
    createElement() {
      return { className: "", innerHTML: "", prepend() {} };
    },
    getElementById() {
      return element;
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

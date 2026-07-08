const state = {
  problems: [],
  selected: null,
  activeTab: "statement",
  reports: {},
  workflows: {},
  reruns: {},
  runtime: null,
  similarity: {},
  busy: {},
  selectionRequestId: 0,
};

const els = {
  healthText: document.getElementById("healthText"),
  generateForm: document.getElementById("generateForm"),
  topicInput: document.getElementById("topicInput"),
  countInput: document.getElementById("countInput"),
  useLlmInput: document.getElementById("useLlmInput"),
  workflowInput: document.getElementById("workflowInput"),
  problemList: document.getElementById("problemList"),
  refreshButton: document.getElementById("refreshButton"),
  currentTitle: document.getElementById("currentTitle"),
  reviewButton: document.getElementById("reviewButton"),
  validateButton: document.getElementById("validateButton"),
  packageButton: document.getElementById("packageButton"),
  deleteButton: document.getElementById("deleteButton"),
  roundsInput: document.getElementById("roundsInput"),
  timeoutInput: document.getElementById("timeoutInput"),
  problemSearchInput: document.getElementById("problemSearchInput"),
  sourceFilter: document.getElementById("sourceFilter"),
  languageFilter: document.getElementById("languageFilter"),
  sourceMetric: document.getElementById("sourceMetric"),
  languageMetric: document.getElementById("languageMetric"),
  llmMetric: document.getElementById("llmMetric"),
  reviewMetric: document.getElementById("reviewMetric"),
  validateMetric: document.getElementById("validateMetric"),
  packageMetric: document.getElementById("packageMetric"),
  runtimeModeText: document.getElementById("runtimeModeText"),
  runtimeModelText: document.getElementById("runtimeModelText"),
  runtimeEndpointText: document.getElementById("runtimeEndpointText"),
  runtimeLimitsText: document.getElementById("runtimeLimitsText"),
  detailContent: document.getElementById("detailContent"),
  activityLog: document.getElementById("activityLog"),
  clearLogButton: document.getElementById("clearLogButton"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await readApiPayload(response);
  if (!response.ok) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

async function readApiPayload(response) {
  if (typeof response.text === "function") {
    const body = await response.text();
    const text = body.trim();
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (err) {
      return { error: text };
    }
  }
  if (typeof response.json === "function") {
    return response.json();
  }
  return {};
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function log(title, message, level = "info") {
  const item = document.createElement("div");
  item.className = "log-entry";
  item.innerHTML = `<strong class="${level}">${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p>`;
  els.activityLog.prepend(item);
}

function currentProblemId() {
  return state.selected?.id;
}

function beginOperation(key) {
  if (state.busy[key]) return false;
  state.busy[key] = true;
  return true;
}

function endOperation(key) {
  delete state.busy[key];
}

function isOperationBusy(key) {
  return Boolean(state.busy[key]);
}

function isProblemOperationBusy(action, problemId) {
  return Boolean(problemId && isOperationBusy(`${action}:${problemId}`));
}

async function checkHealth() {
  try {
    await api("/healthz");
    els.healthText.textContent = "已连接";
    els.healthText.className = "ok";
  } catch (err) {
    els.healthText.textContent = "连接失败";
    els.healthText.className = "bad";
  }
}

async function loadRuntime() {
  try {
    state.runtime = await api("/api/runtime");
    renderRuntime();
  } catch (err) {
    state.runtime = null;
    renderRuntime();
    log("运行状态读取失败", err.message, "warn");
  }
}

function renderRuntime() {
  const runtime = state.runtime;
  if (!runtime) {
    els.runtimeModeText.textContent = "未知";
    els.runtimeModeText.className = "status-pill failed";
    els.runtimeModelText.textContent = "-";
    els.runtimeEndpointText.textContent = "-";
    els.runtimeLimitsText.textContent = "-";
    return;
  }
  const llm = runtime.llm || {};
  const validation = runtime.validation || {};
  const generation = runtime.generation || {};
  const isLlm = llm.active_mode === "llm";
  els.runtimeModeText.textContent = isLlm ? "LLM" : "模板";
  els.runtimeModeText.className = `status-pill ${isLlm ? "completed" : "running"}`;
  els.runtimeModelText.textContent = llm.model || "-";
  els.runtimeEndpointText.textContent = llm.base_url || "-";
  els.runtimeLimitsText.textContent = `最多 ${generation.max_count ?? "-"} 题 / ${validation.max_rounds ?? "-"} 轮`;
  els.llmMetric.textContent = runtimeModeLabel(llm);
}

async function loadProblems(selectLatest = false) {
  try {
    const data = await api("/api/problems");
    state.problems = data.list || [];
    if (selectLatest && state.problems.length) {
      renderProblemList();
      await selectProblem(state.problems[state.problems.length - 1].id);
      return;
    }
    const selectedId = state.selected?.id;
    if (selectedId && !state.problems.some((problem) => problem.id === selectedId)) {
      forgetProblem(selectedId);
      log("当前题目已移除", "刷新后当前题目不在列表中，已清空详情。", "warn");
      return;
    }
    renderProblemList();
  } catch (err) {
    log("题目列表读取失败", err.message, "warn");
  }
}

async function refreshProblems() {
  const operationKey = "refresh";
  if (!beginOperation(operationKey)) return;
  const button = els.refreshButton;
  if (button && !button.dataset.originalText) {
    button.dataset.originalText = button.innerHTML;
  }
  if (button) {
    button.disabled = true;
    button.innerHTML = `<span class="button-icon">...</span><span>刷新中</span>`;
  }
  try {
    await loadProblems(false);
  } finally {
    endOperation(operationKey);
    if (button) {
      button.disabled = false;
      button.innerHTML = button.dataset.originalText;
    }
  }
}

function forgetProblem(id) {
  state.selectionRequestId += 1;
  state.problems = state.problems.filter((problem) => problem.id !== id);
  delete state.reports[id];
  delete state.workflows[id];
  delete state.similarity[id];
  clearRerunsForProblem(id);
  if (state.selected?.id === id) {
    state.selected = null;
    state.activeTab = "statement";
  }
  renderAll();
}

async function selectProblem(id) {
  const requestId = ++state.selectionRequestId;
  const isCurrent = () => requestId === state.selectionRequestId;
  try {
    const problem = await api(`/api/problems/${id}`);
    if (!isCurrent()) return;
    state.selected = problem;
    state.activeTab = "statement";
    state.reports[id] = state.reports[id] || {};
    if (!(await loadStoredReports(id))) {
      if (!isCurrent()) return;
      forgetProblem(id);
      log("题目不存在", "列表中的题目已不存在或无法读取，已从当前列表移除。", "warn");
      return;
    }
    if (!isCurrent()) return;
    await loadSimilarity(id);
    if (!isCurrent()) return;
    await loadWorkflow(id);
    if (!isCurrent()) return;
    renderAll();
    log("已选择题目", `${problem.title} (${problem.source})`, "ok");
  } catch (err) {
    if (!isCurrent()) return;
    if (err.status === 404) {
      forgetProblem(id);
      log("题目不存在", "列表中的题目已不存在或无法读取，已从当前列表移除。", "warn");
      return;
    }
    log("题目读取失败", err.message, "warn");
  }
}

async function loadWorkflow(id) {
  try {
    state.workflows[id] = await api(`/api/problems/${id}/workflow`);
  } catch (err) {
    delete state.workflows[id];
    if (err.status !== 404) {
      log("流程读取失败", err.message, "warn");
    }
  }
}

async function loadStoredReports(id) {
  try {
    const data = await api(`/api/problems/${id}/reports`);
    updateProblemReports(id, {
      ...(data.review ? { review: data.review } : {}),
      ...(data.validation ? { validation: data.validation } : {}),
      ...(data.package ? { package: data.package } : {}),
    });
    return true;
  } catch (err) {
    if (err.status === 404) {
      delete state.reports[id];
      return false;
    }
    log("报告读取失败", err.message, "warn");
    return true;
  }
}

function mergeReportState(existingReports, existingReruns, problemId, updates) {
  const reports = { ...(existingReports || {}), ...(updates || {}) };
  const reruns = { ...(existingReruns || {}) };
  if (Object.prototype.hasOwnProperty.call(updates || {}, "validation")) {
    Object.keys(reruns)
      .filter((key) => key.startsWith(`${problemId}:`))
      .forEach((key) => delete reruns[key]);
  }
  return { reports, reruns };
}

function invalidateProblemState(existingReports, existingReruns, problemId) {
  const reruns = { ...(existingReruns || {}) };
  Object.keys(reruns)
    .filter((key) => key.startsWith(`${problemId}:`))
    .forEach((key) => delete reruns[key]);
  return { reports: {}, reruns };
}

function updateProblemReports(id, updates) {
  const merged = mergeReportState(state.reports[id], state.reruns, id, updates);
  state.reports[id] = merged.reports;
  state.reruns = merged.reruns;
}

function invalidateProblemReports(id) {
  const invalidated = invalidateProblemState(state.reports[id], state.reruns, id);
  state.reports[id] = invalidated.reports;
  state.reruns = invalidated.reruns;
}

async function loadSimilarity(id) {
  try {
    state.similarity[id] = await api(`/api/problems/${id}/similar`);
  } catch (err) {
    delete state.similarity[id];
    log("相似题读取失败", err.message, "warn");
  }
}

function renderProblemList() {
  if (!state.problems.length) {
    els.problemList.innerHTML = `<div class="empty-state"><p>暂无题目</p></div>`;
    return;
  }
  const problems = filteredProblems();
  if (!problems.length) {
    els.problemList.innerHTML = `<div class="empty-state"><p>没有匹配的题目</p></div>`;
    return;
  }
  els.problemList.innerHTML = problems
    .map((problem) => {
      const active = state.selected?.id === problem.id ? " active" : "";
      return `
        <button class="problem-item${active}" type="button" data-id="${escapeHtml(problem.id)}">
          <span class="problem-title">${escapeHtml(problem.title)}</span>
          <span class="problem-meta">
            <span>${escapeHtml(problem.topic)}</span>
            <span>${escapeHtml(problem.difficulty)}</span>
            <span>${escapeHtml(problem.source)}</span>
            <span>${escapeHtml(languageLabel(problem.statement_language))}</span>
          </span>
        </button>
      `;
    })
    .join("");
}

function filteredProblems() {
  const keyword = String(els.problemSearchInput?.value || "").trim().toLowerCase();
  const source = els.sourceFilter?.value || "all";
  const language = els.languageFilter?.value || "all";
  return state.problems.filter((problem) => {
    const haystack = [
      problem.id,
      problem.title,
      problem.topic,
      (problem.tags || []).join(" "),
    ]
      .join(" ")
      .toLowerCase();
    const sourceMatches =
      source === "all" ||
      problem.source === source ||
      (source === "mock" && String(problem.source || "").startsWith("mock"));
    const languageMatches = language === "all" || problem.statement_language === language;
    return (!keyword || haystack.includes(keyword)) && sourceMatches && languageMatches;
  });
}

function renderAll() {
  const problem = state.selected;
  const hasProblem = Boolean(problem);
  const problemId = problem?.id;
  els.currentTitle.textContent = problem?.title || "等待选择题目";
  els.reviewButton.disabled = !hasProblem || isProblemOperationBusy("review", problemId);
  els.validateButton.disabled = !hasProblem || isProblemOperationBusy("validate", problemId);
  els.packageButton.disabled = !hasProblem || isProblemOperationBusy("package", problemId);
  els.deleteButton.disabled = !hasProblem || isProblemOperationBusy("delete", problemId);
  els.llmMetric.textContent = runtimeModeLabel(state.runtime?.llm);
  renderMetrics();
  renderProblemList();
  renderTabs();
  renderDetail();
}

function renderMetrics() {
  const problem = state.selected;
  const reports = state.reports[problem?.id] || {};
  els.sourceMetric.textContent = problem?.source || "-";
  els.languageMetric.textContent = languageLabel(problem?.statement_language);
  els.reviewMetric.textContent = reports.review
    ? `${reports.review.passed ? "通过" : "未通过"} / ${reports.review.score}`
    : "-";
  if (reports.validation) {
    const validationPassed = reports.validation.fuzz_passed && reports.validation.sample_passed;
    const failedHint = reports.validation.failure_stage
      ? ` / ${reports.validation.failure_stage}:${reports.validation.first_failed_seed ?? "-"}`
      : "";
    els.validateMetric.textContent = `${validationPassed ? "通过" : "失败"} / ${reports.validation.total_cases}${failedHint}`;
  } else {
    els.validateMetric.textContent = "-";
  }
  if (reports.package?.package_blocked) {
    els.packageMetric.textContent = "被阻止";
  } else {
    els.packageMetric.textContent = reports.package ? "已导出" : "-";
  }
}

function languageLabel(language) {
  if (language === "en") return "English";
  if (language === "zh") return "中文";
  return "-";
}

function runtimeModeLabel(llm) {
  if (!llm) return "-";
  return llm.active_mode === "llm" ? `LLM / ${llm.model || "-"}` : "模板兜底";
}

function renderTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === state.activeTab);
  });
}

function renderDetail() {
  const problem = state.selected;
  if (!problem) {
    els.detailContent.className = "detail-content empty-state";
    els.detailContent.innerHTML = `<h3>暂无题目</h3><p>从左侧生成或选择一道题。</p>`;
    return;
  }
  els.detailContent.className = "detail-content";
  if (state.activeTab === "statement") renderStatement(problem);
  if (state.activeTab === "workflow") renderWorkflow(problem);
  if (state.activeTab === "edit") renderEdit(problem);
  if (state.activeTab === "solution") renderSolution(problem);
  if (state.activeTab === "code") renderCode(problem);
  if (state.activeTab === "reports") renderReports(problem);
}

function renderStatement(problem) {
  const constraints = problem.constraints.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const samples = problem.samples
    .map(
      (sample, index) => `
        <div>
          <h4>Sample ${index + 1}</h4>
          <pre>${escapeHtml(sample.input)}</pre>
          <pre>${escapeHtml(sample.output)}</pre>
        </div>
      `,
    )
    .join("");
  els.detailContent.innerHTML = `
    <h3>${escapeHtml(problem.title)}</h3>
    <div class="tag-row">
      ${problem.tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}
    </div>
    <p>${escapeHtml(problem.statement)}</p>
    <h4>Input Format</h4>
    <p>${escapeHtml(problem.input_format)}</p>
    <h4>Output Format</h4>
    <p>${escapeHtml(problem.output_format)}</p>
    <h4>Constraints</h4>
    <ul>${constraints}</ul>
    <h4>Samples</h4>
    <div class="sample-grid">${samples}</div>
  `;
}

function renderWorkflow(problem) {
  const workflow = state.workflows[problem.id];
  if (!workflow) {
    els.detailContent.innerHTML = `
      <h3>流程</h3>
      <div class="empty-report">这道题不是通过分步流程创建的，或还没有流程记录。</div>
    `;
    return;
  }
  const steps = workflow.steps
    .map(
      (step) => `
        <button class="workflow-step" type="button" data-step="${escapeHtml(step.key)}">
          <strong>${escapeHtml(step.name)}</strong>
          <span class="status-pill ${escapeHtml(step.status)}">${escapeHtml(statusLabel(step.status))}</span>
          <span>${escapeHtml(step.summary || `${modeLabel(step.mode)}步骤`)}</span>
        </button>
      `,
    )
    .join("");
  const waiting = workflow.status === "waiting_user";
  const busy = isProblemOperationBusy("workflow", problem.id);
  els.detailContent.innerHTML = `
    <h3>分步流程</h3>
    <p>状态：${escapeHtml(statusLabel(workflow.status))}，当前：${escapeHtml(workflow.current_step)}</p>
    <div class="workflow-steps">${steps}</div>
    <button id="continueWorkflowButton" class="primary-button" type="button" ${waiting && !busy ? "" : "disabled"}>
      <span class="button-icon">></span>
      <span>${busy ? "流程处理中" : waiting ? "确认当前步骤并继续" : "当前无需确认"}</span>
    </button>
  `;
  const button = document.getElementById("continueWorkflowButton");
  if (button) {
    button.addEventListener("click", () => continueWorkflow(false));
  }
  document.querySelectorAll(".workflow-step").forEach((item) => {
    item.addEventListener("click", () => jumpToStep(item.dataset.step));
  });
}

function editActionState(problemId) {
  const editBusy = isProblemOperationBusy("edit", problemId);
  const workflowBusy = isProblemOperationBusy("workflow", problemId);
  const disabled = editBusy || workflowBusy;
  return {
    disabled,
    saveLabel: editBusy ? "保存中" : "保存编辑",
    continueLabel: workflowBusy ? "流程处理中" : editBusy ? "保存中" : "保存并继续流程",
  };
}

function syncEditActionButtons(problemId) {
  const actionState = editActionState(problemId);
  const saveButton = document.getElementById("saveEditButton");
  const continueButton = document.getElementById("saveAndContinueButton");
  if (saveButton) {
    saveButton.disabled = actionState.disabled;
    saveButton.innerHTML = `<span class="button-icon">${actionState.disabled ? "..." : "S"}</span><span>${actionState.saveLabel}</span>`;
  }
  if (continueButton) {
    continueButton.disabled = actionState.disabled;
    continueButton.innerHTML = `<span class="button-icon">${actionState.disabled ? "..." : ">"}</span><span>${actionState.continueLabel}</span>`;
  }
}

function renderEdit(problem) {
  const actions = editActionState(problem.id);
  const disabled = actions.disabled ? "disabled" : "";
  els.detailContent.innerHTML = `
    <h3>编辑题目</h3>
    <form id="editForm" class="edit-form">
      <label><span>标题</span><input name="title" value="${escapeHtml(problem.title)}"></label>
      <label><span>题面</span><textarea name="statement">${escapeHtml(problem.statement)}</textarea></label>
      <label><span>输入格式</span><textarea name="input_format">${escapeHtml(problem.input_format)}</textarea></label>
      <label><span>输出格式</span><textarea name="output_format">${escapeHtml(problem.output_format)}</textarea></label>
      <label><span>约束，每行一条</span><textarea name="constraints">${escapeHtml(problem.constraints.join("\n"))}</textarea></label>
      <section class="edit-section">
        <div class="edit-section-heading">
          <h4>样例</h4>
          <button id="addSampleButton" class="secondary-button compact-button" type="button">
            <span class="button-icon">+</span>
            <span>添加样例</span>
          </button>
        </div>
        <div id="sampleEditor" class="sample-editor">
          ${renderSampleEditors(problem.samples || [])}
        </div>
      </section>
      <label><span>标签，每行一个</span><textarea name="tags">${escapeHtml((problem.tags || []).join("\n"))}</textarea></label>
      <label><span>题解</span><textarea name="solution_explanation">${escapeHtml(problem.solution_explanation)}</textarea></label>
      <label class="code-field"><span>标准解 Python</span><textarea name="reference_solution" spellcheck="false">${escapeHtml(problem.reference_solution)}</textarea></label>
      <label class="code-field"><span>暴力解 Python</span><textarea name="brute_force_solution" spellcheck="false">${escapeHtml(problem.brute_force_solution)}</textarea></label>
      <label class="code-field"><span>数据生成器 Python</span><textarea name="generator_code" spellcheck="false">${escapeHtml(problem.generator_code)}</textarea></label>
      <div class="toolbar">
        <button id="saveEditButton" class="primary-button" type="submit" ${disabled}><span class="button-icon">${actions.disabled ? "..." : "S"}</span><span>${actions.saveLabel}</span></button>
        <button id="saveAndContinueButton" class="secondary-button" type="button" ${disabled}><span class="button-icon">${actions.disabled ? "..." : ">"}</span><span>${actions.continueLabel}</span></button>
      </div>
    </form>
  `;
  document.getElementById("editForm").addEventListener("submit", saveEdit);
  document.getElementById("saveAndContinueButton").addEventListener("click", () => continueWorkflow(true));
  bindSampleEditor();
}

function renderSampleEditors(samples) {
  const rows = samples.length ? samples : [{ input: "", output: "" }];
  return rows.map((sample, index) => renderSampleEditor(sample, index)).join("");
}

function renderSampleEditor(sample, index) {
  return `
    <article class="sample-editor-card" data-sample-card>
      <div class="sample-editor-heading">
        <strong data-sample-title>样例 ${index + 1}</strong>
        <button class="secondary-button compact-button remove-sample-button" type="button">
          <span class="button-icon">-</span>
          <span>移除</span>
        </button>
      </div>
      <div class="sample-editor-grid">
        <label><span>输入</span><textarea name="sample_input">${escapeHtml(sample.input ?? "")}</textarea></label>
        <label><span>输出</span><textarea name="sample_output">${escapeHtml(sample.output ?? "")}</textarea></label>
      </div>
    </article>
  `;
}

function bindSampleEditor() {
  document.getElementById("addSampleButton")?.addEventListener("click", () => {
    const sampleEditor = document.getElementById("sampleEditor");
    if (!sampleEditor) return;
    const index = sampleEditor.querySelectorAll("[data-sample-card]").length;
    sampleEditor.insertAdjacentHTML("beforeend", renderSampleEditor({ input: "", output: "" }, index));
    bindSampleRemoveButtons();
  });
  bindSampleRemoveButtons();
}

function bindSampleRemoveButtons() {
  document.querySelectorAll(".remove-sample-button").forEach((button) => {
    button.onclick = () => {
      const sampleEditor = document.getElementById("sampleEditor");
      const card = button.closest("[data-sample-card]");
      if (!sampleEditor || !card) return;
      const cards = sampleEditor.querySelectorAll("[data-sample-card]");
      if (cards.length <= 1) {
        card.querySelectorAll("textarea").forEach((textarea) => {
          textarea.value = "";
        });
      } else {
        card.remove();
      }
      renumberSampleCards();
    };
  });
}

function renumberSampleCards() {
  document.querySelectorAll("#sampleEditor [data-sample-card]").forEach((card, index) => {
    const title = card.querySelector("[data-sample-title]");
    if (title) title.textContent = `样例 ${index + 1}`;
  });
}

function statusLabel(status) {
  const labels = {
    pending: "待处理",
    completed: "已完成",
    waiting_user: "待确认",
    running: "运行中",
    failed: "失败",
  };
  return labels[status] || status || "-";
}

function modeLabel(mode) {
  return mode === "manual" ? "人工确认" : "自动";
}

function jumpToStep(step) {
  const targetTabs = {
    idea: "solution",
    statement: "statement",
    constraints: "statement",
    solutions: "solution",
    generator: "code",
    review: "reports",
    validate: "reports",
    package: "reports",
  };
  state.activeTab = targetTabs[step] || "workflow";
  renderAll();
}

function renderSolution(problem) {
  els.detailContent.innerHTML = `
    <h3>题解</h3>
    <p>${escapeHtml(problem.solution_explanation)}</p>
    <h4>Topic</h4>
    <p>${escapeHtml(problem.topic)}</p>
    <h4>Difficulty</h4>
    <p>${escapeHtml(problem.difficulty)}</p>
    <h4>题面语言</h4>
    <p>${escapeHtml(languageLabel(problem.statement_language))}</p>
  `;
}

function renderCode(problem) {
  els.detailContent.innerHTML = `
    <div class="code-split">
      <div>
        <h3>标准解</h3>
        <pre>${escapeHtml(problem.reference_solution)}</pre>
      </div>
      <div>
        <h3>暴力解</h3>
        <pre>${escapeHtml(problem.brute_force_solution)}</pre>
      </div>
    </div>
    <h3>数据生成器</h3>
    <pre>${escapeHtml(problem.generator_code)}</pre>
  `;
}

function renderReports(problem) {
  const reports = state.reports[problem.id] || {};
  els.detailContent.innerHTML = `
    <h3>报告</h3>
    ${renderSimilaritySummary(state.similarity[problem.id])}
    ${renderReviewSummary(reports.review)}
    ${renderValidationSummary(problem, reports.validation)}
    ${renderPackageSummary(problem, reports.package)}
  `;
  bindReportActions(problem);
}

function renderReportBlock(report, emptyText) {
  if (!report) {
    return `<div class="empty-report">${escapeHtml(emptyText)}</div>`;
  }
  return `<pre>${escapeHtml(JSON.stringify(report, null, 2))}</pre>`;
}

function renderSimilaritySummary(report) {
  if (!report) {
    return `
      <section class="report-card">
        <div class="report-heading"><h4>相似题</h4><span class="status-pill">未读取</span></div>
        <div class="empty-report">还没有读取相似题分析。</div>
      </section>
    `;
  }
  const candidates = report.candidates || [];
  const rows = candidates.length
    ? candidates
        .map(
          (candidate) => `
            <li class="issue ${escapeHtml(candidate.risk)}">
              <strong>${escapeHtml(candidate.risk)}</strong>
              <span>${escapeHtml(candidate.title)}</span>
              <p>${escapeHtml(candidate.reason)} / ${escapeHtml(candidate.problem_id)}</p>
            </li>
          `,
        )
        .join("")
    : `<li class="issue ok"><strong>ok</strong><span>all</span><p>未发现明显相似题。</p></li>`;
  const highest = candidates[0]?.risk || "ok";
  return `
    <section class="report-card">
      <div class="report-heading">
        <h4>相似题</h4>
        <span class="status-pill ${report.has_risk ? "waiting_user" : "completed"}">
          ${report.has_risk ? `风险 ${escapeHtml(highest)}` : "未发现"}
        </span>
      </div>
      <div class="summary-grid">
        <div><span>阈值</span><strong>${escapeHtml(report.threshold ?? "-")}</strong></div>
        <div><span>候选</span><strong>${candidates.length}</strong></div>
        <div><span>最高风险</span><strong>${escapeHtml(highest)}</strong></div>
        <div><span>状态</span><strong>${report.has_risk ? "需人工确认" : "正常"}</strong></div>
      </div>
      <ul class="issue-list">${rows}</ul>
      <details><summary>原始相似题 JSON</summary>${renderReportBlock(report, "")}</details>
    </section>
  `;
}

function renderReviewSummary(report) {
  if (!report) {
    return `
      <section class="report-card">
        <div class="report-heading"><h4>审查</h4><span class="status-pill">未运行</span></div>
        <div class="empty-report">还没有运行审查。</div>
      </section>
    `;
  }
  const issues = report.issues || [];
  const errors = issues.filter((issue) => issue.severity === "error").length;
  const warns = issues.filter((issue) => issue.severity === "warn").length;
  const issueRows = issues.length
    ? issues
        .map(
          (issue) => `
            <li class="issue ${escapeHtml(issue.severity)}">
              <strong>${escapeHtml(issue.severity)}</strong>
              <span>${escapeHtml(issue.field)}</span>
              <p>${escapeHtml(issue.message)}</p>
            </li>
          `,
        )
        .join("")
    : `<li class="issue ok"><strong>ok</strong><span>all</span><p>未发现审查问题。</p></li>`;
  return `
    <section class="report-card">
      <div class="report-heading">
        <h4>审查</h4>
        <span class="status-pill ${report.passed ? "completed" : "failed"}">${report.passed ? "通过" : "未通过"}</span>
      </div>
      <div class="summary-grid">
        <div><span>分数</span><strong>${escapeHtml(report.score)}</strong></div>
        <div><span>错误</span><strong>${errors}</strong></div>
        <div><span>警告</span><strong>${warns}</strong></div>
        <div><span>检查项</span><strong>${(report.checks || []).length}</strong></div>
      </div>
      <ul class="issue-list">${issueRows}</ul>
      <details><summary>原始审查 JSON</summary>${renderReportBlock(report, "")}</details>
    </section>
  `;
}

function renderValidationSummary(problem, report) {
  if (!report) {
    return `
      <section class="report-card">
        <div class="report-heading"><h4>验证</h4><span class="status-pill">未运行</span></div>
        <div class="empty-report">还没有运行验证。</div>
      </section>
    `;
  }
  const failedCases = report.failed_cases || [];
  const caseRows = failedCases.length
    ? failedCases.map((failed, index) => renderFailedCase(problem, failed, index)).join("")
    : `<div class="empty-report">没有失败用例。</div>`;
  return `
    <section class="report-card">
      <div class="report-heading">
        <h4>验证</h4>
        <span class="status-pill ${report.fuzz_passed && report.sample_passed ? "completed" : "failed"}">
          ${report.fuzz_passed && report.sample_passed ? "通过" : "失败"}
        </span>
      </div>
      <div class="summary-grid">
        <div><span>样例</span><strong>${report.sample_passed ? "通过" : "失败"}</strong></div>
        <div><span>对拍</span><strong>${report.fuzz_passed ? "通过" : "失败"}</strong></div>
        <div><span>轮数</span><strong>${escapeHtml(report.rounds ?? "-")}</strong></div>
        <div><span>总用例</span><strong>${escapeHtml(report.total_cases ?? "-")}</strong></div>
        <div><span>超时</span><strong>${escapeHtml(report.timeout_seconds ?? "-")}s</strong></div>
        <div><span>耗时</span><strong>${escapeHtml(report.duration_ms ?? "-")}ms</strong></div>
        <div><span>失败 seed</span><strong>${escapeHtml(report.first_failed_seed ?? "-")}</strong></div>
        <div><span>失败阶段</span><strong>${escapeHtml(report.failure_stage ?? "-")}</strong></div>
      </div>
      <div class="failed-case-list">${caseRows}</div>
      <details><summary>原始验证 JSON</summary>${renderReportBlock(report, "")}</details>
    </section>
  `;
}

function renderFailedCase(problem, failed, index) {
  const key = `${problem.id}:${index}`;
  const rerun = state.reruns[key];
  const hasInput = Boolean(failed.input);
  const rerunBusy = isOperationBusy(`rerun:${problem.id}:${index}`);
  const rerunBlock = rerun
    ? `
      <div class="rerun-result ${rerun.passed ? "passed" : "failed"}">
        <strong>复跑：${rerun.passed ? "通过" : "失败"}</strong>
        ${rerun.error ? `<p>${escapeHtml(rerun.error)}</p>` : ""}
        <div class="code-split compact">
          <pre>${escapeHtml(rerun.expected)}</pre>
          <pre>${escapeHtml(rerun.actual)}</pre>
        </div>
      </div>
    `
    : "";
  return `
    <article class="failed-case">
      <div class="report-heading">
        <h5>失败用例 ${index + 1}</h5>
        <span class="status-pill failed">${escapeHtml(failed.reason || "failed")}</span>
      </div>
      <div class="mini-toolbar">
        <button class="secondary-button copy-case-button" type="button" data-failed-index="${index}" ${hasInput ? "" : "disabled"}>复制输入</button>
        <button class="secondary-button rerun-case-button" type="button" data-failed-index="${index}" ${hasInput && !rerunBusy ? "" : "disabled"}>${rerunBusy ? "复跑中" : "复跑用例"}</button>
      </div>
      <h5>Input</h5>
      <pre>${escapeHtml(failed.input || "")}</pre>
      <div class="code-split compact">
        <div><h5>Expected</h5><pre>${escapeHtml(failed.expected || "")}</pre></div>
        <div><h5>Actual</h5><pre>${escapeHtml(failed.actual || "")}</pre></div>
      </div>
      ${rerunBlock}
    </article>
  `;
}

function renderPackageSummary(problem, report) {
  if (!report) {
    return `
      <section class="report-card">
        <div class="report-heading"><h4>导出</h4><span class="status-pill">未导出</span></div>
        <div class="empty-report">还没有导出题目包。</div>
      </section>
    `;
  }
  if (report.package_blocked) {
    return `
      <section class="report-card">
        <div class="report-heading"><h4>导出</h4><span class="status-pill failed">被阻止</span></div>
        <div class="empty-report">${escapeHtml(report.error || "审查或验证未通过，暂未生成题目包。")}</div>
        <details><summary>原始导出 JSON</summary>${renderReportBlock(report, "")}</details>
      </section>
    `;
  }
  const downloadUrl = report.download_url || `/api/problems/${encodeURIComponent(problem.id)}/package/download`;
  return `
    <section class="report-card">
      <div class="report-heading"><h4>导出</h4><span class="status-pill completed">已导出</span></div>
      <div class="empty-report">${escapeHtml(report.package_dir || "已生成题目包")}</div>
      <div class="package-actions">
        <a class="secondary-button download-link" href="${escapeHtml(downloadUrl)}" download>
          <span class="button-icon">D</span>
          <span>下载 ZIP</span>
        </a>
      </div>
      <details><summary>原始导出 JSON</summary>${renderReportBlock(report, "")}</details>
    </section>
  `;
}

function bindReportActions(problem) {
  document.querySelectorAll(".copy-case-button").forEach((button) => {
    button.addEventListener("click", () => copyFailedCase(problem, Number(button.dataset.failedIndex)));
  });
  document.querySelectorAll(".rerun-case-button").forEach((button) => {
    button.addEventListener("click", () => rerunFailedCase(Number(button.dataset.failedIndex)));
  });
}

async function copyFailedCase(problem, index) {
  const failed = state.reports[problem.id]?.validation?.failed_cases?.[index];
  if (!failed?.input) return;
  try {
    await navigator.clipboard.writeText(failed.input);
    log("已复制失败输入", `case=${index + 1}`, "ok");
  } catch (err) {
    log("复制失败", err.message, "warn");
  }
}

async function rerunFailedCase(index) {
  const problem = state.selected;
  const failed = state.reports[problem?.id]?.validation?.failed_cases?.[index];
  if (!problem || !failed?.input) return;
  let options;
  try {
    options = rerunOptions();
    markValidationInvalid();
  } catch (err) {
    markValidationInvalid(err);
    log("复跑失败", err.message, "bad");
    return;
  }
  const operationKey = `rerun:${problem.id}:${index}`;
  if (!beginOperation(operationKey)) return;
  try {
    const data = await api(`/api/problems/${problem.id}/rerun`, {
      method: "POST",
      body: JSON.stringify({
        input: failed.input,
        timeout_seconds: options.timeout_seconds,
      }),
    });
    state.reruns[`${problem.id}:${index}`] = data;
    renderAll();
    state.activeTab = "reports";
    renderTabs();
    renderDetail();
    log("复跑完成", `passed=${data.passed}`, data.passed ? "ok" : "bad");
  } catch (err) {
    log("复跑失败", err.message, "bad");
  } finally {
    endOperation(operationKey);
  }
}

function setBusy(button, busy, text) {
  if (!button.dataset.originalText) {
    button.dataset.originalText = button.innerHTML;
  }
  if (busy) {
    button.disabled = true;
    button.innerHTML = `<span class="button-icon">...</span><span>${text}</span>`;
    return;
  }
  button.innerHTML = button.dataset.originalText;
  renderAll();
}

function fieldError(field, message) {
  const error = new Error(message);
  error.field = field;
  return error;
}

function parseClampedInteger(value, field, defaultValue, min, max) {
  const text = String(value ?? "").trim();
  if (!text) return defaultValue;
  if (!/^[+-]?\d+$/.test(text)) {
    throw fieldError(field, `${field} must be an integer`);
  }
  return Math.max(min, Math.min(Number.parseInt(text, 10), max));
}

function parseClampedNumber(value, field, defaultValue, min, max) {
  const text = String(value ?? "").trim();
  if (!text) return defaultValue;
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) {
    throw fieldError(field, `${field} must be a number`);
  }
  return Math.max(min, Math.min(parsed, max));
}

function normalizeDifficulty(value) {
  const normalized = String(value || "easy").trim().toLowerCase();
  if (["easy", "medium", "hard"].includes(normalized)) return normalized;
  throw fieldError("difficulty", "difficulty must be easy, medium, or hard");
}

function normalizeStatementLanguage(value) {
  if (value == null) return "zh";
  const normalized = String(value).trim().toLowerCase();
  if (["zh", "cn", "chinese", "中文", "汉语"].includes(normalized)) return "zh";
  if (["en", "english"].includes(normalized)) return "en";
  throw fieldError("statement_language", "statement_language must be zh or en");
}

function buildGenerationPayload(form, controls = els) {
  const topic = String(form.get("topic") ?? "").trim();
  if (!topic) {
    throw fieldError("topic", "topic is required");
  }
  return {
    topic,
    difficulty: normalizeDifficulty(form.get("difficulty")),
    statement_language: normalizeStatementLanguage(form.get("statement_language")),
    count: parseClampedInteger(form.get("count"), "count", 1, 1, 5),
    use_llm: Boolean(controls.useLlmInput?.checked),
  };
}

function markInputInvalid(input, invalid, focus = false) {
  input?.classList?.toggle("input-error", invalid);
  if (invalid && focus) {
    input?.focus?.();
  }
}

function markGenerationInvalid(error = null) {
  markInputInvalid(els.topicInput, false);
  markInputInvalid(els.countInput, false);
  if (!error) return;
  if (error.field === "count") {
    markInputInvalid(els.countInput, true, true);
  } else {
    markInputInvalid(els.topicInput, true, true);
  }
}

function markValidationInvalid(error = null) {
  markInputInvalid(els.roundsInput, false);
  markInputInvalid(els.timeoutInput, false);
  if (!error) return;
  if (error.field === "timeout_seconds") {
    markInputInvalid(els.timeoutInput, true, true);
  } else {
    markInputInvalid(els.roundsInput, true, true);
  }
}

async function handleGenerate(event) {
  event.preventDefault();
  const form = new FormData(els.generateForm);
  let payload;
  try {
    payload = buildGenerationPayload(form, els);
    markGenerationInvalid();
  } catch (err) {
    markGenerationInvalid(err);
    log("生成失败", err.message, "bad");
    return;
  }
  const useWorkflow = els.workflowInput.checked;
  const manualSteps = Array.from(form.getAll("manual_steps")).map(String);
  const operationKey = "generate";
  if (!beginOperation(operationKey)) return;
  const button = els.generateForm.querySelector("button[type='submit']");
  button.disabled = true;
  button.innerHTML = `<span class="button-icon">...</span><span>生成中</span>`;
  try {
    if (useWorkflow) {
      const data = await api("/api/workflows/start", {
        method: "POST",
        body: JSON.stringify({ ...payload, manual_steps: manualSteps }),
      });
      state.workflows[data.problem.id] = data.workflow;
      log("流程已启动", workflowEventSummary(data.result), "ok");
      await loadProblems(false);
      await selectProblem(data.problem.id);
      state.activeTab = "workflow";
      renderAll();
    } else {
      const data = await api("/api/problems/generate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      log("生成完成", `${data.list.length} 道题已创建`, "ok");
      await loadProblems(false);
      if (data.list[0]) {
        await selectProblem(data.list[0].id);
      }
    }
  } catch (err) {
    log("生成失败", err.message, "bad");
  } finally {
    endOperation(operationKey);
    button.disabled = false;
    button.innerHTML = `<span class="button-icon">+</span><span>生成题目</span>`;
  }
}

function workflowEventSummary(result) {
  const events = result?.events || [];
  if (!events.length) return "无流程事件";
  return events.map((event) => `${event.step}:${event.status}`).join(", ");
}

function problemPatchFromEditForm(form) {
  const sampleInputs = form.getAll("sample_input").map(String);
  const sampleOutputs = form.getAll("sample_output").map(String);
  const samples = sampleInputs
    .map((input, index) => ({ input, output: sampleOutputs[index] || "" }))
    .filter((sample) => sample.input.trim() || sample.output.trim());
  return {
    title: String(form.get("title") || ""),
    statement: String(form.get("statement") || ""),
    input_format: String(form.get("input_format") || ""),
    output_format: String(form.get("output_format") || ""),
    constraints: String(form.get("constraints") || "")
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean),
    samples,
    tags: String(form.get("tags") || "")
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean),
    solution_explanation: String(form.get("solution_explanation") || ""),
    reference_solution: String(form.get("reference_solution") || ""),
    brute_force_solution: String(form.get("brute_force_solution") || ""),
    generator_code: String(form.get("generator_code") || ""),
  };
}

function problemPatchChanged(problem, patch) {
  if (!problem || !patch) return false;
  return Object.entries(patch).some(([key, value]) => JSON.stringify(problem[key]) !== JSON.stringify(value));
}

function validationOptions(controls = els) {
  return {
    rounds: parseClampedInteger(controls.roundsInput?.value, "rounds", 100, 1, 1000),
    timeout_seconds: parseClampedNumber(controls.timeoutInput?.value, "timeout_seconds", 2, 0.2, 10),
  };
}

function rerunOptions(controls = els) {
  return {
    timeout_seconds: parseClampedNumber(controls.timeoutInput?.value, "timeout_seconds", 2, 0.2, 10),
  };
}

async function saveEdit(event) {
  event.preventDefault();
  const id = currentProblemId();
  if (!id) return;
  const patch = problemPatchFromEditForm(new FormData(event.target));
  if (!problemPatchChanged(state.selected, patch)) {
    log("编辑未保存", "当前表单没有变化，报告和导出包保持有效。", "info");
    return;
  }
  const operationKey = `edit:${id}`;
  if (!beginOperation(operationKey)) return;
  syncEditActionButtons(id);
  try {
    const problem = await api(`/api/problems/${id}/edit`, {
      method: "POST",
      body: JSON.stringify({ patch }),
    });
    state.selected = problem;
    if (problem.changed !== false) {
      state.reports[id] = {};
      clearRerunsForProblem(id);
    }
    await loadSimilarity(id);
    const message =
      problem.changed === false ? "当前表单没有变化，报告和导出包保持有效。" : "旧报告和导出包已失效，请重新审查/验证。";
    log("编辑已保存", message, "ok");
  } catch (err) {
    log("编辑失败", err.message, "bad");
  } finally {
    endOperation(operationKey);
    renderAll();
  }
}

async function continueWorkflow(includePatch) {
  const id = currentProblemId();
  if (!id) return;
  const operationKey = `workflow:${id}`;
  if (!beginOperation(operationKey)) return;
  const previousWorkflow = cloneWorkflow(state.workflows[id]);
  const payload = { confirm_current: true };
  const editForm = document.getElementById("editForm");
  if (includePatch && editForm) {
    const patch = problemPatchFromEditForm(new FormData(editForm));
    if (problemPatchChanged(state.selected, patch)) {
      payload.patch = patch;
    }
  }
  markCurrentWorkflowStepRunning(id);
  renderAll();
  try {
    const data = await api(`/api/problems/${id}/workflow/continue`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.selected = data.problem;
    state.workflows[id] = data.workflow;
    if (data.changed || data.reports_invalidated || data.package_invalidated) {
      invalidateProblemReports(id);
    }
    updateProblemReports(id, {
      ...(data.result?.reports?.review ? { review: data.result.reports.review } : {}),
      ...(data.result?.reports?.validation ? { validation: data.result.reports.validation } : {}),
      ...(data.result?.reports?.package ? { package: data.result.reports.package } : {}),
    });
    renderAll();
    log("流程继续", workflowEventSummary(data.result), "ok");
  } catch (err) {
    if (previousWorkflow) {
      state.workflows[id] = previousWorkflow;
      renderAll();
    }
    log("流程失败", err.message, "bad");
  } finally {
    endOperation(operationKey);
    renderAll();
  }
}

function markCurrentWorkflowStepRunning(id) {
  const workflow = state.workflows[id];
  if (!workflow) return;
  const current = workflow.steps.find((step) => step.key === workflow.current_step);
  if (current) {
    current.status = "running";
    current.summary = "进行中";
  }
  workflow.status = "running";
}

function cloneWorkflow(workflow) {
  return workflow ? JSON.parse(JSON.stringify(workflow)) : null;
}

async function runReview() {
  const id = currentProblemId();
  if (!id) return;
  const operationKey = `review:${id}`;
  if (!beginOperation(operationKey)) return;
  setBusy(els.reviewButton, true, "审查中");
  try {
    const data = await api(`/api/problems/${id}/review`, { method: "POST", body: "{}" });
    updateProblemReports(id, { review: data });
    renderAll();
    log("审查完成", `score=${data.score}, passed=${data.passed}`, data.passed ? "ok" : "warn");
  } catch (err) {
    log("审查失败", err.message, "bad");
  } finally {
    endOperation(operationKey);
    setBusy(els.reviewButton, false, "");
  }
}

async function runValidate() {
  const id = currentProblemId();
  if (!id) return;
  let options;
  try {
    options = validationOptions();
    markValidationInvalid();
  } catch (err) {
    markValidationInvalid(err);
    log("验证失败", err.message, "bad");
    return;
  }
  const operationKey = `validate:${id}`;
  if (!beginOperation(operationKey)) return;
  setBusy(els.validateButton, true, "验证中");
  try {
    const data = await api(`/api/problems/${id}/validate`, {
      method: "POST",
      body: JSON.stringify(options),
    });
    updateProblemReports(id, { validation: data });
    renderAll();
    log("验证完成", `fuzz=${data.fuzz_passed}, cases=${data.total_cases}`, data.fuzz_passed ? "ok" : "bad");
  } catch (err) {
    log("验证失败", err.message, "bad");
  } finally {
    endOperation(operationKey);
    setBusy(els.validateButton, false, "");
  }
}

async function runPackage() {
  const id = currentProblemId();
  if (!id) return;
  let options;
  try {
    options = validationOptions();
    markValidationInvalid();
  } catch (err) {
    markValidationInvalid(err);
    log("导出失败", err.message, "bad");
    return;
  }
  const operationKey = `package:${id}`;
  if (!beginOperation(operationKey)) return;
  setBusy(els.packageButton, true, "导出中");
  try {
    const data = await api(`/api/problems/${id}/package`, {
      method: "POST",
      body: JSON.stringify(options),
    });
    updateProblemReports(id, {
      review: data.review,
      validation: data.validation,
      package: { package_dir: data.package_dir, download_url: data.download_url },
    });
    renderAll();
    log("导出完成", data.package_dir, "ok");
  } catch (err) {
    if (err.payload?.package_blocked) {
      updateProblemReports(id, {
        review: err.payload.review,
        validation: err.payload.validation,
        package: {
          package_blocked: true,
          error: err.payload.error,
        },
      });
      state.activeTab = "reports";
      renderAll();
      log("导出被阻止", "审查或验证未通过，报告已更新。", "warn");
    } else {
      log("导出失败", err.message, "bad");
    }
  } finally {
    endOperation(operationKey);
    setBusy(els.packageButton, false, "");
  }
}

async function deleteSelectedProblem() {
  const problem = state.selected;
  if (!problem) return;
  const confirmed = window.confirm(`删除题目「${problem.title}」？导出目录和 ZIP 也会一起删除。`);
  if (!confirmed) return;
  const operationKey = `delete:${problem.id}`;
  if (!beginOperation(operationKey)) return;
  setBusy(els.deleteButton, true, "删除中");
  try {
    const data = await api(`/api/problems/${problem.id}`, { method: "DELETE" });
    forgetProblem(problem.id);
    await loadProblems(false);
    renderAll();
    log("题目已删除", `${data.problem_id} / package=${data.removed_package}`, "ok");
  } catch (err) {
    log("删除失败", err.message, "bad");
  } finally {
    endOperation(operationKey);
    setBusy(els.deleteButton, false, "");
  }
}

function clearRerunsForProblem(problemId) {
  Object.keys(state.reruns)
    .filter((key) => key.startsWith(`${problemId}:`))
    .forEach((key) => delete state.reruns[key]);
}

function bindEvents() {
  els.generateForm.addEventListener("submit", handleGenerate);
  els.topicInput.addEventListener("input", () => markGenerationInvalid());
  els.countInput.addEventListener("input", () => markGenerationInvalid());
  els.refreshButton.addEventListener("click", refreshProblems);
  els.problemList.addEventListener("click", (event) => {
    const button = event.target.closest(".problem-item");
    if (button) selectProblem(button.dataset.id);
  });
  els.reviewButton.addEventListener("click", runReview);
  els.validateButton.addEventListener("click", runValidate);
  els.packageButton.addEventListener("click", runPackage);
  els.deleteButton.addEventListener("click", deleteSelectedProblem);
  els.clearLogButton.addEventListener("click", () => {
    els.activityLog.innerHTML = "";
  });
  [els.problemSearchInput, els.sourceFilter, els.languageFilter].forEach((control) => {
    control?.addEventListener("input", renderProblemList);
    control?.addEventListener("change", renderProblemList);
  });
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      renderTabs();
      renderDetail();
    });
  });
}

async function init() {
  bindEvents();
  await checkHealth();
  await loadRuntime();
  await loadProblems(false);
  log("工作台就绪", window.location.origin, "ok");
}

init();

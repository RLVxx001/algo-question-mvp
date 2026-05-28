const state = {
  problems: [],
  selected: null,
  activeTab: "statement",
  reports: {},
  workflows: {},
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
  sourceMetric: document.getElementById("sourceMetric"),
  languageMetric: document.getElementById("languageMetric"),
  reviewMetric: document.getElementById("reviewMetric"),
  validateMetric: document.getElementById("validateMetric"),
  packageMetric: document.getElementById("packageMetric"),
  detailContent: document.getElementById("detailContent"),
  activityLog: document.getElementById("activityLog"),
  clearLogButton: document.getElementById("clearLogButton"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
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

async function loadProblems(selectLatest = false) {
  const data = await api("/api/problems");
  state.problems = data.list || [];
  renderProblemList();
  if (selectLatest && state.problems.length) {
    await selectProblem(state.problems[state.problems.length - 1].id);
  } else if (state.selected) {
    renderProblemList();
  }
}

async function selectProblem(id) {
  const problem = await api(`/api/problems/${id}`);
  state.selected = problem;
  state.activeTab = "statement";
  state.reports[id] = state.reports[id] || {};
  await loadStoredReports(id);
  await loadWorkflow(id);
  renderAll();
  log("已选择题目", `${problem.title} (${problem.source})`, "ok");
}

async function loadWorkflow(id) {
  try {
    state.workflows[id] = await api(`/api/problems/${id}/workflow`);
  } catch (err) {
    delete state.workflows[id];
  }
}

async function loadStoredReports(id) {
  try {
    const data = await api(`/api/problems/${id}/reports`);
    state.reports[id] = {
      ...(state.reports[id] || {}),
      ...(data.review ? { review: data.review } : {}),
      ...(data.validation ? { validation: data.validation } : {}),
      ...(data.package ? { package: data.package } : {}),
    };
  } catch (err) {
    log("报告读取失败", err.message, "warn");
  }
}

function renderProblemList() {
  if (!state.problems.length) {
    els.problemList.innerHTML = `<div class="empty-state"><p>暂无题目</p></div>`;
    return;
  }
  els.problemList.innerHTML = state.problems
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

function renderAll() {
  const problem = state.selected;
  const hasProblem = Boolean(problem);
  els.currentTitle.textContent = problem?.title || "等待选择题目";
  els.reviewButton.disabled = !hasProblem;
  els.validateButton.disabled = !hasProblem;
  els.packageButton.disabled = !hasProblem;
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
  els.validateMetric.textContent = reports.validation
    ? `${reports.validation.fuzz_passed ? "通过" : "失败"} / ${reports.validation.total_cases}`
    : "-";
  els.packageMetric.textContent = reports.package ? "已导出" : "-";
}

function languageLabel(language) {
  if (language === "en") return "English";
  if (language === "zh") return "中文";
  return "-";
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
  els.detailContent.innerHTML = `
    <h3>分步流程</h3>
    <p>状态：${escapeHtml(statusLabel(workflow.status))}，当前：${escapeHtml(workflow.current_step)}</p>
    <div class="workflow-steps">${steps}</div>
    <button id="continueWorkflowButton" class="primary-button" type="button" ${waiting ? "" : "disabled"}>
      <span class="button-icon">></span>
      <span>${waiting ? "确认当前步骤并继续" : "当前无需确认"}</span>
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

function renderEdit(problem) {
  els.detailContent.innerHTML = `
    <h3>编辑题目</h3>
    <form id="editForm" class="edit-form">
      <label><span>标题</span><input name="title" value="${escapeHtml(problem.title)}"></label>
      <label><span>题面</span><textarea name="statement">${escapeHtml(problem.statement)}</textarea></label>
      <label><span>输入格式</span><textarea name="input_format">${escapeHtml(problem.input_format)}</textarea></label>
      <label><span>输出格式</span><textarea name="output_format">${escapeHtml(problem.output_format)}</textarea></label>
      <label><span>约束，每行一条</span><textarea name="constraints">${escapeHtml(problem.constraints.join("\n"))}</textarea></label>
      <label><span>题解</span><textarea name="solution_explanation">${escapeHtml(problem.solution_explanation)}</textarea></label>
      <div class="toolbar">
        <button class="primary-button" type="submit"><span class="button-icon">S</span><span>保存编辑</span></button>
        <button id="saveAndContinueButton" class="secondary-button" type="button"><span class="button-icon">></span><span>保存并继续流程</span></button>
      </div>
    </form>
  `;
  document.getElementById("editForm").addEventListener("submit", saveEdit);
  document.getElementById("saveAndContinueButton").addEventListener("click", () => continueWorkflow(true));
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
    <h4>审查</h4>
    ${renderReportBlock(reports.review, "还没有运行审查。点击右上角“审查”后会在这里显示结果。")}
    <h4>验证</h4>
    ${renderReportBlock(reports.validation, "还没有运行验证。点击右上角“验证”后会在这里显示对拍结果。")}
    <h4>导出</h4>
    ${renderReportBlock(reports.package, "还没有导出题目包。点击右上角“导出”后会在这里显示目录。")}
  `;
}

function renderReportBlock(report, emptyText) {
  if (!report) {
    return `<div class="empty-report">${escapeHtml(emptyText)}</div>`;
  }
  return `<pre>${escapeHtml(JSON.stringify(report, null, 2))}</pre>`;
}

function setBusy(button, busy, text) {
  button.disabled = busy || !state.selected;
  if (!button.dataset.originalText) {
    button.dataset.originalText = button.innerHTML;
  }
  button.innerHTML = busy ? `<span class="button-icon">...</span><span>${text}</span>` : button.dataset.originalText;
}

async function handleGenerate(event) {
  event.preventDefault();
  const form = new FormData(els.generateForm);
  const payload = {
    topic: String(form.get("topic") || "array"),
    difficulty: String(form.get("difficulty") || "easy"),
    statement_language: String(form.get("statement_language") || "zh"),
    count: Number(form.get("count") || 1),
    use_llm: els.useLlmInput.checked,
  };
  const useWorkflow = els.workflowInput.checked;
  const manualSteps = Array.from(form.getAll("manual_steps")).map(String);
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
  return {
    title: String(form.get("title") || ""),
    statement: String(form.get("statement") || ""),
    input_format: String(form.get("input_format") || ""),
    output_format: String(form.get("output_format") || ""),
    constraints: String(form.get("constraints") || "")
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean),
    solution_explanation: String(form.get("solution_explanation") || ""),
  };
}

async function saveEdit(event) {
  event.preventDefault();
  const id = currentProblemId();
  if (!id) return;
  const patch = problemPatchFromEditForm(new FormData(event.target));
  try {
    const problem = await api(`/api/problems/${id}/edit`, {
      method: "POST",
      body: JSON.stringify({ patch }),
    });
    state.selected = problem;
    renderAll();
    log("编辑已保存", problem.title, "ok");
  } catch (err) {
    log("编辑失败", err.message, "bad");
  }
}

async function continueWorkflow(includePatch) {
  const id = currentProblemId();
  if (!id) return;
  markCurrentWorkflowStepRunning(id);
  renderAll();
  const payload = { confirm_current: true };
  const editForm = document.getElementById("editForm");
  if (includePatch && editForm) {
    payload.patch = problemPatchFromEditForm(new FormData(editForm));
  }
  try {
    const data = await api(`/api/problems/${id}/workflow/continue`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.selected = data.problem;
    state.workflows[id] = data.workflow;
    state.reports[id] = {
      ...(state.reports[id] || {}),
      ...(data.result?.reports?.review ? { review: data.result.reports.review } : {}),
      ...(data.result?.reports?.validation ? { validation: data.result.reports.validation } : {}),
      ...(data.result?.reports?.package ? { package: data.result.reports.package } : {}),
    };
    renderAll();
    log("流程继续", workflowEventSummary(data.result), "ok");
  } catch (err) {
    log("流程失败", err.message, "bad");
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

async function runReview() {
  const id = currentProblemId();
  if (!id) return;
  setBusy(els.reviewButton, true, "审查中");
  try {
    const data = await api(`/api/problems/${id}/review`, { method: "POST", body: "{}" });
    state.reports[id] = { ...(state.reports[id] || {}), review: data };
    renderAll();
    log("审查完成", `score=${data.score}, passed=${data.passed}`, data.passed ? "ok" : "warn");
  } catch (err) {
    log("审查失败", err.message, "bad");
  } finally {
    setBusy(els.reviewButton, false, "");
  }
}

async function runValidate() {
  const id = currentProblemId();
  if (!id) return;
  setBusy(els.validateButton, true, "验证中");
  try {
    const data = await api(`/api/problems/${id}/validate`, {
      method: "POST",
      body: JSON.stringify({ rounds: 100 }),
    });
    state.reports[id] = { ...(state.reports[id] || {}), validation: data };
    renderAll();
    log("验证完成", `fuzz=${data.fuzz_passed}, cases=${data.total_cases}`, data.fuzz_passed ? "ok" : "bad");
  } catch (err) {
    log("验证失败", err.message, "bad");
  } finally {
    setBusy(els.validateButton, false, "");
  }
}

async function runPackage() {
  const id = currentProblemId();
  if (!id) return;
  setBusy(els.packageButton, true, "导出中");
  try {
    const data = await api(`/api/problems/${id}/package`, {
      method: "POST",
      body: JSON.stringify({ rounds: 100 }),
    });
    state.reports[id] = {
      ...(state.reports[id] || {}),
      review: data.review,
      validation: data.validation,
      package: { package_dir: data.package_dir },
    };
    renderAll();
    log("导出完成", data.package_dir, "ok");
  } catch (err) {
    log("导出失败", err.message, "bad");
  } finally {
    setBusy(els.packageButton, false, "");
  }
}

function bindEvents() {
  els.generateForm.addEventListener("submit", handleGenerate);
  els.refreshButton.addEventListener("click", () => loadProblems(false));
  els.problemList.addEventListener("click", (event) => {
    const button = event.target.closest(".problem-item");
    if (button) selectProblem(button.dataset.id);
  });
  els.reviewButton.addEventListener("click", runReview);
  els.validateButton.addEventListener("click", runValidate);
  els.packageButton.addEventListener("click", runPackage);
  els.clearLogButton.addEventListener("click", () => {
    els.activityLog.innerHTML = "";
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
  await loadProblems(false);
  log("工作台就绪", window.location.origin, "ok");
}

init();

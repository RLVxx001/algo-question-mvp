# API

默认服务地址：

```text
http://127.0.0.1:18081
```

前端工作台：

```http
GET /
GET /static/app.js
GET /static/styles.css
```

## Health

```http
GET /healthz
```

响应：

```json
{"ok": true}
```

## 运行状态

```http
GET /api/runtime
```

返回服务运行参数、LLM 是否已配置、当前模型名、生成数量上限和验证上限。响应不会返回 `ALGO_LLM_API_KEY`。

```json
{
  "ok": true,
  "llm": {
    "configured": false,
    "active_mode": "template",
    "model": "gpt-5.5",
    "fallback_source": "mock"
  },
  "generation": {"max_count": 5},
  "validation": {"max_rounds": 1000}
}
```

## 生成题目

```http
POST /api/problems/generate
Content-Type: application/json
```

所有 POST JSON 请求体都必须是对象；数组、字符串或非法 JSON 会返回 `400`。

请求：

```json
{
  "topic": "prefix sum",
  "difficulty": "easy",
  "language": "python",
  "statement_language": "zh",
  "count": 1,
  "use_llm": true
}
```

说明：

- `topic`: 知识点或题型，必须是非空字符串；只包含空白字符会返回 `400`。
- `difficulty`: `easy`、`medium` 或 `hard`；大小写会规范化，其他值返回 `400`。
- `language`: 题解、暴力解和数据生成器的代码语言；当前仅支持 `python`，兼容 `py` / `python3` / `py3` 别名，其他值返回 `400`。
- `statement_language`: 题面语言，`zh` 或 `en`，默认 `zh`。兼容 `chinese` / `english` 等明确别名，其他值返回 `400`。
- `count`: 1 到 5；兼容字符串数字，超出范围会收敛到 1 或 5，非整数返回 `400`。
- `use_llm`: true 时优先调用 LLM；未配置 key 或失败时降级模板。也兼容 `"true"` / `"false"` 这类字符串布尔值，其他字符串会返回 `400`。

## 查看题目列表

```http
GET /api/problems
```

## 查看题目详情

```http
GET /api/problems/{problem_id}
```

## 编辑题目

```http
POST /api/problems/{problem_id}/edit
Content-Type: application/json
```

请求：

```json
{
  "patch": {
    "title": "新的标题",
    "statement": "新的题面",
    "samples": [
      {"input": "3 5\n1 2 3\n", "output": "2\n"}
    ],
    "reference_solution": "import sys\n..."
  }
}
```

支持更新的字段包括：`title`、`statement`、`input_format`、`output_format`、`constraints`、`samples`、`tags`、`solution_explanation`、`reference_solution`、`brute_force_solution`、`generator_code`。传入其他顶层字段会返回 `400`，不会覆盖原题内容。

`constraints` 和 `tags` 必须是字符串数组，`samples` 必须是对象数组，且每个样例都包含 `input` 和 `output`。补丁格式不合法时返回 `400`，不会覆盖原题内容。

编辑成功后会删除该题目已有的审查报告、验证报告、导出目录和 ZIP，因为它们已经不再对应当前题目内容。响应是在题目详情基础上增加：

```json
{
  "reports_invalidated": true,
  "package_invalidated": true
}
```

## 删除题目

```http
DELETE /api/problems/{problem_id}
```

删除内容：

- `data/problems/{problem_id}.json`
- `data/workflows/{problem_id}.json`，如果存在
- `data/reports/{problem_id}/`，如果存在
- `data/packages/{problem_id}/`，如果存在
- `data/packages/{problem_id}.zip`，如果存在

响应：

```json
{
  "problem_id": "prob_x",
  "deleted": true,
  "removed_package": true
}
```

## 查看已有报告

```http
GET /api/problems/{problem_id}/reports
```

会返回已持久化的审查报告、验证报告、题目包目录和 ZIP 下载地址；没有运行过的部分返回 `null`。如果没有导出目录，但最新审查或验证报告显示导出条件不满足，`package` 会返回 `package_blocked: true`，用于刷新后继续展示阻断原因。

报告持久化位置：

```text
data/reports/{problem_id}/review_report.json
data/reports/{problem_id}/validation_report.json
```

如果题目是旧版本导出的、还没有 `data/reports/{problem_id}/`，接口会兼容读取 `data/packages/{problem_id}/` 下的报告。

## 查看相似题

```http
GET /api/problems/{problem_id}/similar
```

会从当前本地题库中排除自己后进行相似度分析，返回最多 5 个候选。相似度基于标题、知识点、标签和题面词元的加权 Jaccard 分数，用于人工审核重复风险，不会阻止生成或导出。

```json
{
  "problem_id": "prob_x",
  "threshold": 0.35,
  "has_risk": true,
  "candidates": [
    {
      "problem_id": "prob_y",
      "title": "目标和配对计数",
      "score": 0.82,
      "risk": "high",
      "matched_fields": ["title", "topic", "tags"],
      "reason": "matched title, topic, tags with score 0.82"
    }
  ]
}
```

## 审查题目

```http
POST /api/problems/{problem_id}/review
Content-Type: application/json
```

返回：

- `passed`: 是否无 error。
- `score`: 0 到 100 的规则分。
- `issues`: error/warn 列表。
- `checks`: 已执行检查项。

响应会写入 `data/reports/{problem_id}/review_report.json`，刷新前端后仍可从 `/reports` 读回。

## 验证题目

```http
POST /api/problems/{problem_id}/validate
Content-Type: application/json
```

请求：

```json
{
  "rounds": 100,
  "timeout_seconds": 2
}
```

验证内容：

- 用标准解跑样例，检查输出。
- 用数据生成器生成随机用例。
- 暴力解和标准解对拍。
- `rounds` 范围限制为 1 到 1000。
- `timeout_seconds` 为每次运行标准解、暴力解或生成器的超时时间，范围限制为 0.2 到 10。
- `rounds` 或 `timeout_seconds` 不是数字、或传入布尔值时返回 `400`，不会写入报告。

响应会包含运行元数据：

- `rounds`: 实际执行的随机轮数。
- `timeout_seconds`: 实际使用的单进程超时。
- `sample_count`: 样例数量。
- `duration_ms`: 本次验证总耗时。
- `first_failed_seed`: 第一条随机失败用例的 seed；样例失败或无失败时为 `null`。
- `failure_stage`: `sample`、`generator`、`brute_force`、`reference`、`compare` 或 `null`。

响应会写入 `data/reports/{problem_id}/validation_report.json`，刷新前端后仍可从 `/reports` 读回。

## 复跑单个用例

```http
POST /api/problems/{problem_id}/rerun
Content-Type: application/json
```

请求：

```json
{
  "input": "5 6\n1 5 3 3 2\n",
  "timeout_seconds": 2
}
```

响应：

```json
{
  "problem_id": "prob_x",
  "input": "5 6\n1 5 3 3 2\n",
  "expected": "2",
  "actual": "2",
  "passed": true,
  "error": "",
  "failure_stage": null
}
```

`expected` 来自暴力解，`actual` 来自标准解。该接口用于复现验证报告里的失败输入。

`timeout_seconds` 不是数字、或传入布尔值时返回 `400`。

## 导出题目包

```http
POST /api/problems/{problem_id}/package
Content-Type: application/json
```

请求：

```json
{
  "rounds": 100,
  "timeout_seconds": 2
}
```

接口会重新运行审查和验证。只有 `review.passed`、`validation.sample_passed` 和 `validation.fuzz_passed` 全部为 `true` 时才会创建导出目录和 ZIP。

`rounds` 或 `timeout_seconds` 不是数字、或传入布尔值时返回 `400`，不会创建或删除导出目录。

导出目录：

```text
data/packages/{problem_id}/
```

成功响应会包含：

```json
{
  "problem_id": "prob_x",
  "package_blocked": false,
  "package_dir": "data/packages/prob_x",
  "download_url": "/api/problems/prob_x/package/download",
  "validation": {},
  "review": {}
}
```

如果审查或验证失败，接口返回 `400`，不会创建 `data/packages/{problem_id}/`，并会保存最新报告到 `data/reports/{problem_id}/`：

```json
{
  "problem_id": "prob_x",
  "package_blocked": true,
  "error": "package blocked by failed review or validation",
  "validation": {},
  "review": {}
}
```

导出文件：

- `problem.md`
- `problem.json`
- `reference_solution.py`
- `brute_force_solution.py`
- `generator.py`
- `validation_report.json`
- `review_report.json`
- `README.md`

## 下载题目 ZIP

```http
GET /api/problems/{problem_id}/package/download
```

下载前需要先成功调用 `/api/problems/{problem_id}/package` 或完成分步流程的导出步骤。接口会基于 `data/packages/{problem_id}/` 生成 `data/packages/{problem_id}.zip` 并返回：

- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename="{problem_id}.zip"`

## 启动分步流程

```http
POST /api/workflows/start
Content-Type: application/json
```

请求：

```json
{
  "topic": "循环",
  "difficulty": "easy",
  "statement_language": "zh",
  "use_llm": true,
  "manual_steps": ["statement", "constraints"]
}
```

`manual_steps` 表示哪些生成阶段需要停下来等人工确认；没列进去的步骤会自动通过或自动执行。支持的步骤名为：

- `idea`
- `statement`
- `constraints`
- `solutions`
- `generator`

不传 `manual_steps` 时默认在 `statement` 停下；显式传空数组 `[]` 表示所有步骤都自动执行。传入未知步骤名会返回 `400`，并且不会保存草稿题目。

分步流程走到 `package` 步骤时也会重新运行审查和验证，并遵守和 `/api/problems/{problem_id}/package` 相同的导出闸门。失败时流程状态会变为 `failed`，返回的 `reports.package.package_blocked` 为 `true`，不会创建导出目录。

## 查看流程状态

```http
GET /api/problems/{problem_id}/workflow
```

## 确认当前流程步骤并继续

```http
POST /api/problems/{problem_id}/workflow/continue
Content-Type: application/json
```

请求：

```json
{
  "confirm_current": true,
  "patch": {
    "title": "修改后的标题",
    "statement": "修改后的题面"
  }
}
```

`patch` 可选，用于在确认当前步骤时保存用户修改。

`confirm_current` 默认 `true`；也兼容 `"true"` / `"false"` 这类字符串布尔值。其他字符串会返回 `400`，不会推进流程。

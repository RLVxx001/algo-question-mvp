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

## 生成题目

```http
POST /api/problems/generate
Content-Type: application/json
```

请求：

```json
{
  "topic": "prefix sum",
  "difficulty": "easy",
  "statement_language": "zh",
  "count": 1,
  "use_llm": true
}
```

说明：

- `topic`: 知识点或题型。
- `difficulty`: 当前只是传给模型和记录，尚未做强约束。
- `statement_language`: 题面语言，`zh` 或 `en`，默认 `zh`。
- `count`: 1 到 5。
- `use_llm`: true 时优先调用 LLM；未配置 key 或失败时降级模板。

## 查看题目列表

```http
GET /api/problems
```

## 查看题目详情

```http
GET /api/problems/{problem_id}
```

## 删除题目

```http
DELETE /api/problems/{problem_id}
```

删除内容：

- `data/problems/{problem_id}.json`
- `data/workflows/{problem_id}.json`，如果存在
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

如果该题目已经导出过，会返回磁盘中的审查报告、验证报告、题目包目录和 ZIP 下载地址；没有运行过的部分返回 `null`。

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

响应会包含运行元数据：

- `rounds`: 实际执行的随机轮数。
- `timeout_seconds`: 实际使用的单进程超时。
- `sample_count`: 样例数量。
- `duration_ms`: 本次验证总耗时。
- `first_failed_seed`: 第一条随机失败用例的 seed；样例失败或无失败时为 `null`。
- `failure_stage`: `sample`、`generator`、`brute_force`、`reference`、`compare` 或 `null`。

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

导出目录：

```text
data/packages/{problem_id}/
```

响应会包含：

```json
{
  "problem_id": "prob_x",
  "package_dir": "data/packages/prob_x",
  "download_url": "/api/problems/prob_x/package/download",
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

下载前需要先调用 `/api/problems/{problem_id}/package` 或完成分步流程的导出步骤。接口会基于 `data/packages/{problem_id}/` 生成 `data/packages/{problem_id}.zip` 并返回：

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

`manual_steps` 表示哪些步骤需要停下来等人工确认；没列进去的步骤会自动通过或自动执行。

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

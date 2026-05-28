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

## 查看已有报告

```http
GET /api/problems/{problem_id}/reports
```

如果该题目已经导出过，会返回磁盘中的审查报告、验证报告和题目包目录；没有运行过的部分返回 `null`。

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
{"rounds": 100}
```

验证内容：

- 用标准解跑样例，检查输出。
- 用数据生成器生成随机用例。
- 暴力解和标准解对拍。
- `rounds` 范围限制为 1 到 1000。

## 导出题目包

```http
POST /api/problems/{problem_id}/package
Content-Type: application/json
```

请求：

```json
{"rounds": 100}
```

导出目录：

```text
data/packages/{problem_id}/
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

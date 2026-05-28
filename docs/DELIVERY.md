# 交付说明

## 当前状态

这是一个可运行的 v0.1 成品，支持：

- HTTP 生成算法题目包。
- 使用 OpenAI 兼容接口调用模型。
- 未配置模型时使用本地模板兜底。
- 静态质量审查。
- 样例验证和随机对拍。
- 导出可交付题目包目录。
- 本地自动化测试。

## 启动

```bash
cd /Users/a123/Desktop/tool/wurenji/algo-question-mvp
export ALGO_LLM_BASE_URL='http://8.138.45.45:8318'
export ALGO_LLM_API_KEY='your-api-key'
export ALGO_LLM_MODEL='gpt-5.5'
python3 -m app.server
```

不配置 `ALGO_LLM_API_KEY` 也可以启动，会走本地模板。

## 本地验收

```bash
cd /Users/a123/Desktop/tool/wurenji/algo-question-mvp
make compile
make test
```

服务启动后可以跑 HTTP 主流程 smoke：

```bash
python3 -m scripts.smoke --base-url http://127.0.0.1:18081
```

如果当前服务已配置 `ALGO_LLM_API_KEY`，可以同时验证真实模型链路：

```bash
python3 -m scripts.smoke --base-url http://127.0.0.1:18081 --include-llm
```

## 人工验收流程

1. 调 `/api/problems/generate` 生成题目。
2. 调 `/api/problems/{id}/review` 看静态质量。
3. 调 `/api/problems/{id}/validate` 做对拍。
4. 调 `/api/problems/{id}/package` 导出题目包。
5. 打开导出目录里的 `problem.md`、`validation_report.json`、`review_report.json` 做最终确认。

## 发布前必须补的能力

如果要开放给外部用户，必须先补：

- 代码执行沙箱。
- API 鉴权。
- 请求限流。
- 题目查重。
- 生成失败重试和结构修复。
- 更完整的日志和审计。

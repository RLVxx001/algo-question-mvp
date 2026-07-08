# Algorithm Question MVP

独立算法出题服务原型，目标不是一次性“让 AI 写题”，而是生成可验证的题目包：

- 题面、输入输出、约束、样例
- 标准解
- 暴力解
- 测试数据生成器
- 自动样例校验与随机对拍
- 导出目录和可下载 ZIP

## 启动

```bash
cd /Users/a123/Desktop/学校相关项目/algo-question-mvp
python3 -m app.server
```

默认地址：

```text
http://127.0.0.1:18081
```

打开根路径就是前端工作台：

```text
http://127.0.0.1:18081/
```

## 配置 LLM

服务支持 OpenAI 兼容的 `/v1/chat/completions` 接口。推荐通过环境变量配置：

```bash
export ALGO_LLM_BASE_URL='http://8.138.45.45:8318'
export ALGO_LLM_API_KEY='your-api-key'
export ALGO_LLM_MODEL='gpt-5.5'
```

如果没有配置 `ALGO_LLM_API_KEY`，服务会使用本地模板生成题目，仍可完整跑通验证链路。

## 生成题目

```bash
curl -sS http://127.0.0.1:18081/api/problems/generate \
  -H 'Content-Type: application/json' \
  -d '{"topic":"prefix sum","difficulty":"easy","statement_language":"zh","count":1}'
```

响应里的 `list[0].id` 是题目 ID。

`statement_language` 可选：

- `zh`: 中文题面，默认值
- `en`: 英文题面

## 验证题目

```bash
curl -sS http://127.0.0.1:18081/api/problems/<problem_id>/validate \
  -H 'Content-Type: application/json' \
  -d '{"rounds":100}'
```

验证内容：

- 样例输出是否与标准解一致
- 数据生成器生成随机输入
- 标准解与暴力解对拍

## 查询

```bash
curl -sS http://127.0.0.1:18081/api/problems
curl -sS http://127.0.0.1:18081/api/problems/<problem_id>
curl -sS http://127.0.0.1:18081/api/problems/<problem_id>/reports
```

运行审查或验证后，报告会持久化到 `data/reports/<problem_id>/`，刷新前端后仍可读取。

## 审查题目

```bash
curl -sS http://127.0.0.1:18081/api/problems/<problem_id>/review \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## 导出题目包

```bash
curl -sS http://127.0.0.1:18081/api/problems/<problem_id>/package \
  -H 'Content-Type: application/json' \
  -d '{"rounds":100}'
```

题目包会导出到：

```text
data/packages/<problem_id>/
```

下载 ZIP：

```bash
curl -L -o "<problem_id>.zip" \
  http://127.0.0.1:18081/api/problems/<problem_id>/package/download
```

## 删除题目

```bash
curl -sS -X DELETE http://127.0.0.1:18081/api/problems/<problem_id>
```

会同步删除题目 JSON、流程记录、持久化报告、导出目录和 ZIP。

## 本地测试

```bash
make compile
make test
```

服务启动后可以跑 HTTP 主流程 smoke。smoke 会验证生成、审查、对拍、导出、ZIP 下载和删除清理：

```bash
python3 -m scripts.smoke --base-url http://127.0.0.1:18081
```

## MVP 边界

- 当前只执行 Python 题解和 Python 数据生成器。
- 当前验证代码在本机子进程运行，只适合可信环境原型，不适合直接开放给公网用户提交任意代码。
- LLM 输出如果格式不稳定，会自动降级到本地模板；后续可以补 JSON schema 修复、二次审查和沙箱隔离。

## 文档

- `docs/ARCHITECTURE.md`
- `docs/API.md`
- `docs/VALIDATION.md`
- `docs/DELIVERY.md`
- `docs/FRONTEND.md`

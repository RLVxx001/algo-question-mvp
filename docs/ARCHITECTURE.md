# 架构说明

这个 MVP 的定位是独立算法出题服务，不依赖当前仓库里的图库业务。

## 目标

第一版只解决一件事：把“AI 自动出题”变成可验证的工程流水线。

核心产物是一道完整题目包：

- 题面、输入输出、约束、样例
- 标准解
- 暴力解
- 数据生成器
- 静态质量审查报告
- 样例与随机对拍验证报告

## 模块

```text
HTTP API
  -> generator
       -> LLM generator
       -> local template fallback
  -> store
       -> JSON problem persistence
  -> reviewer
       -> required fields
       -> sample shape
       -> metadata
       -> Python syntax
       -> unsupported type checks
  -> validator
       -> sample check
       -> generator-driven fuzzing
       -> reference vs brute-force differential test
  -> exporter
       -> publishable package directory
```

## 文件

- `app/server.py`: HTTP 入口
- `app/generator.py`: LLM 调用和本地模板降级
- `app/reviewer.py`: 静态质量审查
- `app/validator.py`: 对拍验证
- `app/exporter.py`: 题目包导出
- `app/store.py`: 题目 JSON 存储
- `static/index.html`: 前端工作台入口
- `static/styles.css`: 前端样式
- `static/app.js`: 前端交互和 API 调用
- `tests/test_mvp.py`: 本地自动化验收测试

## 重要设计选择

1. Python 标准库实现 HTTP 服务，避免 MVP 阶段引入框架和数据库。
2. 所有 LLM 产物都必须包含标准解、暴力解、生成器，不接受只有题面的结果。
3. 验证不是相信模型，而是运行代码做差分测试。
4. LLM 失败时自动降级本地模板，保证演示和主链路不断。
5. 真实 API key 只从环境变量读取，不写入仓库文件。

## 多轮流程

分步流程不是只做 UI 外壳。`/api/workflows/start` 会先创建空题目草稿，然后按步骤推进：

```text
idea -> statement -> constraints -> solutions -> generator -> review -> validate -> package
```

每个生成步骤只写入当前阶段字段：

- `idea`: 标题、标签、解法方向摘要
- `statement`: 题面、输入格式、输出格式
- `constraints`: 约束、样例
- `solutions`: 标准解、暴力解、题解
- `generator`: 数据生成器

如果某一步配置为人工确认，系统会先生成该阶段草稿，然后停在 `waiting_user`。用户编辑确认后，后续步骤才会基于最新题面继续生成。因此用户改了题意，约束、样例、解法和数据生成器会在确认之后重新按当前内容生成。

## 当前边界

- 只支持 Python 3 题解和生成器。
- 本地子进程直接执行生成的代码，只适合可信本地原型。
- 还没有 Docker/firejail/nsjail 级别沙箱。
- 还没有相似题检索、题目查重、难度校准、人工审核工作台。

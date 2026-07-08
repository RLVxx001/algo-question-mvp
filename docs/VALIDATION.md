# 验证策略

这个功能的核心不是“生成”，而是“验证”。MVP 采用三层验证。

## 1. 静态审查

`reviewer` 检查：

- 必填文本字段是否为空。
- 样例是否至少两组，且包含 input/output。
- 约束和标签是否存在。
- 标准解、暴力解、生成器是否能通过 Python 语法编译。
- 生成代码是否包含明显危险的本地执行能力，如 `subprocess`、`socket`、`urllib`、`open(`、`eval(`、`exec(` 等。
- 题面、输入输出格式、题解是否过短。
- 约束是否包含数字边界。
- 数据生成器是否使用 seed 参数。
- 题目主题是否体现在标题、题面或标签里。
- 是否出现当前不支持的交互题、浮点精度题风险。

静态审查只能发现格式和低级质量问题，不能证明算法正确。

## 2. 样例校验

`validator` 会把标准解写入临时文件，对每组样例运行一次。

如果样例输出和标准解输出不一致，题目不能进入发布包。

## 3. 随机对拍

每轮对拍流程：

```text
generator.py(seed) -> test input
brute_force_solution.py(input) -> expected output
reference_solution.py(input) -> actual output
compare expected and actual
```

这能发现大量常见错误：

- 边界处理错误
- 索引错误
- 重复元素统计错误
- 负数处理错误
- 样例对但随机数据不对

验证报告会记录本次运行的 `rounds`、`timeout_seconds`、`sample_count`、`duration_ms`、`first_failed_seed` 和 `failure_stage`。这些字段用于在页面上快速判断失败发生在样例、生成器、暴力解、标准解还是标准/暴力输出比较阶段。

审查和验证报告会在运行后写入 `data/reports/{problem_id}/`，不需要等到导出题目包。前端刷新后会通过 `/api/problems/{id}/reports` 重新读取这些报告；导出题目包时会先重新运行审查和验证，只有两者都通过才会把报告写入 `data/packages/{problem_id}/`，作为可交付包的一部分。失败时接口返回 `400`，不会生成可下载包，但最新报告仍会保存在 `data/reports/{problem_id}/`。

## 4. 单用例复跑

当验证报告里出现失败用例时，前端可以调用 `/api/problems/{id}/rerun` 只复跑这一个输入。

复跑流程：

```text
failed input -> brute_force_solution.py -> expected
failed input -> reference_solution.py -> actual
compare expected and actual
```

复跑不会重新生成随机数据，也不会导出题目包。它用于定位一次失败是否稳定复现，以及标准解和暴力解当前分别输出什么。

## 不能覆盖的风险

- 数据生成器覆盖不足。
- 暴力解本身错误。
- 标准解和暴力解共享同一种错误理解。
- 大数据性能没有在当前 MVP 中压测。
- 生成代码目前没有强沙箱，不能开放给不可信用户。

## 生成器规模原则

随机对拍里的 `generator.py` 是给暴力解校验用的，不是正式压测数据生成器。它应该覆盖边界形态和结构复杂度，但规模必须让 `brute_force_solution.py` 在超时时间内稳定完成。

例如树题可以生成单点、链、完全二叉树、随机二叉树、全 0 权值等结构，但节点数应控制在几十到一两百以内；正式约束里的 `n = 200000` 这类数据应该放到后续独立的性能测试/造数阶段。否则会出现标准解没错、样例也对，但随机对拍因为暴力解超时而失败的情况。

## 下一阶段建议

1. 引入容器或系统沙箱执行生成代码。
2. 每类题型维护专门的边界数据生成策略。
3. 增加错误解/hack 数据生成。
4. 增加 LLM 二次审查，独立审查题意歧义和复杂度。
5. 增加相似题检索，避免生成已有题变体。

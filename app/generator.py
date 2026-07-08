from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.models import GeneratedProblem, ProblemRequest


SYSTEM_PROMPT = """You are an algorithm problem setter.
Return exactly one JSON object. Do not wrap it in markdown.
The problem must be deterministic and suitable for automatic judging.
Use Python 3 for reference_solution, brute_force_solution, and generator_code.
Both solutions must read stdin and write stdout.
generator_code must accept one integer seed as argv[1] and print one valid test input.
The reference and brute force solutions must produce identical output for every valid input.
"""


def generate_problem(req: ProblemRequest) -> GeneratedProblem:
    req.statement_language = _normalize_statement_language(req.statement_language)
    if req.use_llm and os.getenv("ALGO_LLM_API_KEY"):
        try:
            problem = _generate_with_llm(req)
            if _matches_statement_language(problem, req.statement_language):
                return problem
            return _mock_problem(req, source="mock-after-language-mismatch")
        except Exception:
            return _mock_problem(req, source="mock-after-llm-failure")
    return _mock_problem(req, source="mock")


def create_problem_draft(req: ProblemRequest, source: str = "draft") -> GeneratedProblem:
    req.statement_language = _normalize_statement_language(req.statement_language)
    return GeneratedProblem(
        id=f"prob_{int(time.time())}_{uuid4().hex[:8]}",
        title="",
        topic=req.topic,
        difficulty=req.difficulty,
        statement="",
        input_format="",
        output_format="",
        constraints=[],
        samples=[],
        tags=[],
        solution_explanation="",
        reference_solution="",
        brute_force_solution="",
        generator_code="",
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
        statement_language=req.statement_language,
    )


def generate_workflow_stage(problem: GeneratedProblem, req: ProblemRequest, stage: str) -> GeneratedProblem:
    req.statement_language = _normalize_statement_language(req.statement_language)
    if req.use_llm and os.getenv("ALGO_LLM_API_KEY"):
        try:
            updated = _generate_stage_with_llm(problem, req, stage)
            if stage in {"idea", "statement", "constraints"} and not _matches_statement_language(updated, req.statement_language):
                return _generate_stage_from_template(problem, req, stage, source="mock-after-language-mismatch")
            return updated
        except Exception:
            return _generate_stage_from_template(problem, req, stage, source="mock-after-llm-failure")
    return _generate_stage_from_template(problem, req, stage, source="mock")


def _generate_with_llm(req: ProblemRequest) -> GeneratedProblem:
    base_url = os.getenv("ALGO_LLM_BASE_URL", "http://8.138.45.45:8318").rstrip("/")
    api_key = os.getenv("ALGO_LLM_API_KEY", "")
    model = os.getenv("ALGO_LLM_MODEL", "gpt-5.5")
    human_language = "Simplified Chinese" if req.statement_language == "zh" else "English"

    user_prompt = f"""Generate one algorithm problem package.
Topic: {req.topic}
Difficulty: {req.difficulty}
Programming language: Python 3
Human-facing language: {human_language}

Required JSON fields:
title, statement, input_format, output_format, constraints, samples, tags,
solution_explanation, reference_solution, brute_force_solution, generator_code.

Quality rules:
- Include 2 samples.
- constraints must be an array of strings.
- samples must be an array of objects with input and output string fields.
- Keep the problem in the selected topic.
- Avoid interactive, floating point, and special judge problems.
- Make brute_force_solution simple enough for small random cases.
- generator_code should generate small and medium valid cases so fuzzing is fast.
- title, statement, input_format, output_format, constraints, tags, and solution_explanation must be written in the human-facing language.
- Code, stdin/stdout behavior, and sample input/output must remain valid Python/judge text.
"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
    }
    req_body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=req_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    parsed = _parse_json_object(content)
    return _problem_from_payload(req, parsed, "llm")


def _generate_stage_with_llm(problem: GeneratedProblem, req: ProblemRequest, stage: str) -> GeneratedProblem:
    base_url = os.getenv("ALGO_LLM_BASE_URL", "http://8.138.45.45:8318").rstrip("/")
    api_key = os.getenv("ALGO_LLM_API_KEY", "")
    model = os.getenv("ALGO_LLM_MODEL", "gpt-5.5")
    human_language = "Simplified Chinese" if req.statement_language == "zh" else "English"
    stage_fields = {
        "idea": "title, tags, solution_explanation",
        "statement": "title, statement, input_format, output_format, tags",
        "constraints": "constraints, samples",
        "solutions": "solution_explanation, reference_solution, brute_force_solution",
        "generator": "generator_code",
    }
    fields = stage_fields.get(stage)
    if not fields:
        return problem
    user_prompt = f"""Update only this algorithm problem stage.
Stage: {stage}
Fields to return: {fields}
Topic: {req.topic}
Difficulty: {req.difficulty}
Human-facing language: {human_language}

Current problem JSON:
{json.dumps(problem.to_dict(), ensure_ascii=False)}

Rules:
- Return exactly one JSON object containing only the requested fields.
- Human-facing text must use the requested human-facing language.
- Python code must be complete and read stdin/write stdout.
- If generating samples, include exactly 2 samples with input/output strings.
- If generating solutions or generator, they must match the current statement and constraints.
- If generating generator_code, generated random cases must be intentionally small enough for brute_force_solution to finish quickly in differential testing.
- For O(n^2) brute force, keep generated n <= 80 unless the current problem has a smaller natural bound.
"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.6,
    }
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    patch = _parse_json_object(data["choices"][0]["message"]["content"])
    return _apply_stage_patch(problem, patch, source="llm")


def _parse_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return json.loads(content)


def _problem_from_payload(req: ProblemRequest, payload: dict[str, Any], source: str) -> GeneratedProblem:
    problem_id = f"prob_{int(time.time())}_{uuid4().hex[:8]}"
    return GeneratedProblem(
        id=problem_id,
        title=str(payload["title"]),
        topic=req.topic,
        difficulty=req.difficulty,
        statement=str(payload["statement"]),
        input_format=str(payload["input_format"]),
        output_format=str(payload["output_format"]),
        constraints=list(payload["constraints"]),
        samples=list(payload["samples"]),
        tags=list(payload["tags"]),
        solution_explanation=str(payload["solution_explanation"]),
        reference_solution=str(payload["reference_solution"]),
        brute_force_solution=str(payload["brute_force_solution"]),
        generator_code=str(payload["generator_code"]),
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
        statement_language=req.statement_language,
    )


def _apply_stage_patch(problem: GeneratedProblem, patch: dict[str, Any], source: str) -> GeneratedProblem:
    allowed = {
        "title",
        "statement",
        "input_format",
        "output_format",
        "constraints",
        "samples",
        "tags",
        "solution_explanation",
        "reference_solution",
        "brute_force_solution",
        "generator_code",
    }
    for key, value in patch.items():
        if key in allowed:
            setattr(problem, key, value)
    problem.source = source
    return problem


def _generate_stage_from_template(problem: GeneratedProblem, req: ProblemRequest, stage: str, source: str) -> GeneratedProblem:
    full = _mock_problem(req, source=source)
    if stage == "idea":
        problem.title = full.title
        problem.tags = full.tags
        problem.solution_explanation = full.solution_explanation
    elif stage == "statement":
        problem.title = problem.title or full.title
        problem.statement = full.statement
        problem.input_format = full.input_format
        problem.output_format = full.output_format
        problem.tags = full.tags
    elif stage == "constraints":
        problem.constraints = full.constraints
        problem.samples = full.samples
    elif stage == "solutions":
        problem.solution_explanation = full.solution_explanation
        problem.reference_solution = full.reference_solution
        problem.brute_force_solution = full.brute_force_solution
    elif stage == "generator":
        problem.generator_code = full.generator_code
    problem.source = source
    problem.statement_language = req.statement_language
    return problem


def _mock_problem(req: ProblemRequest, source: str = "mock") -> GeneratedProblem:
    topic = req.topic.lower()
    if "two pointer" in topic or "two pointers" in topic or "双指针" in topic:
        return _localize_mock_problem(_two_pointers_problem(req, source), req.statement_language)
    if "binary" in topic or "二分" in topic or "binary search" in topic:
        return _localize_mock_problem(_binary_search_problem(req, source), req.statement_language)
    if "stack" in topic or "栈" in topic:
        return _localize_mock_problem(_stack_problem(req, source), req.statement_language)
    if "prefix" in topic or "前缀" in topic or "sum" in topic:
        return _localize_mock_problem(_prefix_sum_problem(req, source), req.statement_language)
    if "string" in topic or "字符串" in topic:
        return _localize_mock_problem(_string_problem(req, source), req.statement_language)
    return _localize_mock_problem(_two_sum_count_problem(req, source), req.statement_language)


def _normalize_statement_language(language: str) -> str:
    value = (language or "zh").strip().lower()
    if value in {"en", "english"}:
        return "en"
    return "zh"


def _localize_mock_problem(problem: GeneratedProblem, language: str) -> GeneratedProblem:
    language = _normalize_statement_language(language)
    problem.statement_language = language
    if language == "en":
        return problem

    if problem.title == "Count Pairs With Target Sum":
        problem.title = "目标和配对计数"
        problem.statement = "给定一个整数数组和目标值 k，统计有多少个下标对 (i, j) 满足 1 <= i < j <= n 且 a[i] + a[j] = k。"
        problem.input_format = "第一行包含两个整数 n 和 k。第二行包含 n 个整数。"
        problem.output_format = "输出一个整数，表示满足条件的下标对数量。"
        problem.constraints = ["1 <= n <= 200000", "-10^9 <= a[i], k <= 10^9"]
        problem.tags = ["哈希表", "计数", "数组"]
        problem.solution_explanation = "从左到右扫描数组。对于当前值 x，之前出现过的所有 k - x 都可以和它组成一对。先累加数量，再记录 x 的出现次数。"
        return problem

    if problem.title == "Range Sum Queries":
        problem.title = "区间和查询"
        problem.statement = "给定一个整数数组，需要回答 q 次查询。每次查询给出 l 和 r，要求输出 a[l] 到 a[r] 的元素和。下标从 1 开始。"
        problem.input_format = "第一行包含两个整数 n 和 q。第二行包含 n 个整数。接下来 q 行，每行包含两个整数 l 和 r。"
        problem.output_format = "对每个查询，单独输出一行对应的区间和。"
        problem.constraints = ["1 <= n, q <= 200000", "-10^9 <= a[i] <= 10^9", "1 <= l <= r <= n"]
        problem.tags = ["前缀和", "数组", "查询"]
        problem.solution_explanation = "预处理前缀和数组 pref，其中 pref[i] 表示前 i 个数的和。每次查询 [l, r] 的答案为 pref[r] - pref[l - 1]。"
        return problem

    if problem.title == "Longest Balanced Binary Substring":
        problem.title = "最长平衡二进制子串"
        problem.statement = "给定一个二进制字符串 s，求最长的连续子串长度，使得该子串中字符 0 和字符 1 的数量相同。"
        problem.input_format = "输入只有一行，包含一个二进制字符串 s。"
        problem.output_format = "输出一个整数，表示满足条件的最长子串长度。"
        problem.constraints = ["1 <= |s| <= 200000", "s 只包含字符 0 和 1"]
        problem.tags = ["前缀和", "哈希表", "字符串"]
        problem.solution_explanation = "把 0 看作 -1，把 1 看作 +1。如果两个位置的前缀平衡值相同，那么它们之间的子串中 0 和 1 数量相同。记录每个平衡值第一次出现的位置即可。"
        return problem

    if problem.title == "Count Pairs With Sum At Most K":
        problem.title = "和不超过 K 的配对数"
        problem.statement = "给定一个整数数组和整数 k，统计有多少个下标对 (i, j) 满足 1 <= i < j <= n 且 a[i] + a[j] <= k。"
        problem.input_format = "第一行包含两个整数 n 和 k。第二行包含 n 个整数。"
        problem.output_format = "输出一个整数，表示满足条件的下标对数量。"
        problem.constraints = ["1 <= n <= 200000", "-10^9 <= a[i], k <= 10^9"]
        problem.tags = ["双指针", "排序", "数组"]
        problem.solution_explanation = "先将数组排序。固定左端点 left，如果 a[left] + a[right] <= k，则 left 可以和 left+1 到 right 的所有位置配对，答案增加 right-left，然后左端点右移；否则右端点左移。"
        return problem

    if problem.title == "First Position At Least X":
        problem.title = "第一个不小于 X 的位置"
        problem.statement = "给定一个非降序整数数组，需要回答 q 次查询。每次给出 x，输出数组中第一个大于等于 x 的位置，下标从 1 开始；如果不存在，输出 -1。"
        problem.input_format = "第一行包含两个整数 n 和 q。第二行包含 n 个非降序整数。接下来 q 行，每行一个整数 x。"
        problem.output_format = "对每次查询单独输出一行答案。"
        problem.constraints = ["1 <= n, q <= 200000", "-10^9 <= a[i], x <= 10^9", "数组按非降序排列"]
        problem.tags = ["二分", "数组", "查询"]
        problem.solution_explanation = "对每个查询 x，在有序数组中二分第一个满足 a[pos] >= x 的位置。如果位置存在就输出 1-based 下标，否则输出 -1。"
        return problem

    if problem.title == "Next Greater Element":
        problem.title = "右侧第一个更大元素"
        problem.statement = "给定一个整数数组，对每个位置 i，求它右侧第一个严格大于 a[i] 的元素值；如果不存在则输出 -1。"
        problem.input_format = "第一行包含一个整数 n。第二行包含 n 个整数。"
        problem.output_format = "输出 n 个整数，第 i 个数表示位置 i 的答案。"
        problem.constraints = ["1 <= n <= 200000", "-10^9 <= a[i] <= 10^9"]
        problem.tags = ["单调栈", "数组"]
        problem.solution_explanation = "从右向左扫描数组，维护一个单调递减栈。处理 a[i] 时弹出所有小于等于它的值，栈顶就是右侧第一个更大元素；之后把 a[i] 入栈。"
        return problem

    return problem


def _matches_statement_language(problem: GeneratedProblem, language: str) -> bool:
    text = " ".join(
        [
            problem.title,
            problem.statement,
            problem.input_format,
            problem.output_format,
            problem.solution_explanation,
        ]
    )
    chinese_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_letters = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    if language == "zh":
        return chinese_chars >= 12
    return ascii_letters >= 30 and chinese_chars < 12


def _two_sum_count_problem(req: ProblemRequest, source: str) -> GeneratedProblem:
    problem_id = f"prob_{int(time.time())}_{uuid4().hex[:8]}"
    return GeneratedProblem(
        id=problem_id,
        title="Count Pairs With Target Sum",
        topic=req.topic,
        difficulty=req.difficulty,
        statement=(
            "Given an array of integers and a target value k, count how many index pairs "
            "(i, j) satisfy 1 <= i < j <= n and a[i] + a[j] = k."
        ),
        input_format="The first line contains two integers n and k. The second line contains n integers.",
        output_format="Print one integer: the number of valid pairs.",
        constraints=["1 <= n <= 200000", "-10^9 <= a[i], k <= 10^9"],
        samples=[
            {"input": "5 6\n1 5 3 3 2\n", "output": "2\n"},
            {"input": "4 4\n2 2 2 2\n", "output": "6\n"},
        ],
        tags=["hash table", "counting", "array"],
        solution_explanation=(
            "Scan the array from left to right. For each value x, all previous values equal "
            "k - x can pair with it. Accumulate that count and then record x."
        ),
        reference_solution="""import sys
from collections import defaultdict

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n, k = data[0], data[1]
    arr = data[2:2+n]
    seen = defaultdict(int)
    ans = 0
    for x in arr:
        ans += seen[k - x]
        seen[x] += 1
    print(ans)

if __name__ == "__main__":
    main()
""",
        brute_force_solution="""import sys

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n, k = data[0], data[1]
    arr = data[2:2+n]
    ans = 0
    for i in range(n):
        for j in range(i + 1, n):
            if arr[i] + arr[j] == k:
                ans += 1
    print(ans)

if __name__ == "__main__":
    main()
""",
        generator_code="""import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
rng = random.Random(seed)
n = rng.randint(1, 80)
k = rng.randint(-20, 20)
arr = [rng.randint(-20, 20) for _ in range(n)]
if seed % 7 == 0:
    arr = [k // 2] * n
print(n, k)
print(*arr)
""",
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


def _two_pointers_problem(req: ProblemRequest, source: str) -> GeneratedProblem:
    problem_id = f"prob_{int(time.time())}_{uuid4().hex[:8]}"
    return GeneratedProblem(
        id=problem_id,
        title="Count Pairs With Sum At Most K",
        topic=req.topic,
        difficulty=req.difficulty,
        statement=(
            "Given an array of integers and an integer k, count how many index pairs "
            "(i, j) satisfy 1 <= i < j <= n and a[i] + a[j] <= k."
        ),
        input_format="The first line contains two integers n and k. The second line contains n integers.",
        output_format="Print one integer: the number of valid pairs.",
        constraints=["1 <= n <= 200000", "-10^9 <= a[i], k <= 10^9"],
        samples=[
            {"input": "5 6\n1 2 3 4 5\n", "output": "6\n"},
            {"input": "4 3\n1 1 1 1\n", "output": "6\n"},
        ],
        tags=["two pointers", "sorting", "array"],
        solution_explanation=(
            "Sort the array. If the current smallest plus current largest is at most k, "
            "the smallest value can pair with every value up to the right pointer, so add "
            "right - left and move left. Otherwise move right leftward."
        ),
        reference_solution="""import sys

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n, k = data[0], data[1]
    arr = sorted(data[2:2+n])
    left = 0
    right = n - 1
    ans = 0
    while left < right:
        if arr[left] + arr[right] <= k:
            ans += right - left
            left += 1
        else:
            right -= 1
    print(ans)

if __name__ == "__main__":
    main()
""",
        brute_force_solution="""import sys

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n, k = data[0], data[1]
    arr = data[2:2+n]
    ans = 0
    for i in range(n):
        for j in range(i + 1, n):
            if arr[i] + arr[j] <= k:
                ans += 1
    print(ans)

if __name__ == "__main__":
    main()
""",
        generator_code="""import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
rng = random.Random(seed)
n = rng.randint(1, 70)
k = rng.randint(-30, 40)
arr = [rng.randint(-30, 40) for _ in range(n)]
if seed % 6 == 0:
    arr = sorted(arr)
if seed % 11 == 0:
    arr = [0] * n
print(n, k)
print(*arr)
""",
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


def _binary_search_problem(req: ProblemRequest, source: str) -> GeneratedProblem:
    problem_id = f"prob_{int(time.time())}_{uuid4().hex[:8]}"
    return GeneratedProblem(
        id=problem_id,
        title="First Position At Least X",
        topic=req.topic,
        difficulty=req.difficulty,
        statement=(
            "Given a nondecreasing array, answer q queries. Each query gives x and asks for "
            "the first 1-based position whose value is at least x. Print -1 if no such value exists."
        ),
        input_format=(
            "The first line contains n and q. The second line contains n nondecreasing integers. "
            "Each of the next q lines contains one integer x."
        ),
        output_format="For each query, print the first valid 1-based position or -1 on its own line.",
        constraints=["1 <= n, q <= 200000", "-10^9 <= a[i], x <= 10^9", "a is nondecreasing"],
        samples=[
            {"input": "5 3\n1 3 5 7 9\n4\n1\n10\n", "output": "3\n1\n-1\n"},
            {"input": "3 2\n2 2 8\n2\n9\n", "output": "1\n-1\n"},
        ],
        tags=["binary search", "array", "query"],
        solution_explanation=(
            "For each x, binary search the first index where a[index] >= x. If the insertion "
            "position is inside the array, output it using 1-based indexing; otherwise output -1."
        ),
        reference_solution="""import sys
from bisect import bisect_left

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n, q = data[0], data[1]
    arr = data[2:2+n]
    pos = 2 + n
    out = []
    for _ in range(q):
        x = data[pos]
        pos += 1
        idx = bisect_left(arr, x)
        out.append(str(idx + 1 if idx < n else -1))
    print("\\n".join(out))

if __name__ == "__main__":
    main()
""",
        brute_force_solution="""import sys

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n, q = data[0], data[1]
    arr = data[2:2+n]
    pos = 2 + n
    out = []
    for _ in range(q):
        x = data[pos]
        pos += 1
        answer = -1
        for i, value in enumerate(arr, 1):
            if value >= x:
                answer = i
                break
        out.append(str(answer))
    print("\\n".join(out))

if __name__ == "__main__":
    main()
""",
        generator_code="""import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
rng = random.Random(seed)
n = rng.randint(1, 80)
q = rng.randint(1, 60)
arr = sorted(rng.randint(-50, 50) for _ in range(n))
print(n, q)
print(*arr)
for _ in range(q):
    print(rng.randint(-60, 60))
""",
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


def _stack_problem(req: ProblemRequest, source: str) -> GeneratedProblem:
    problem_id = f"prob_{int(time.time())}_{uuid4().hex[:8]}"
    return GeneratedProblem(
        id=problem_id,
        title="Next Greater Element",
        topic=req.topic,
        difficulty=req.difficulty,
        statement=(
            "Given an integer array, for every position i find the first value to its right "
            "that is strictly greater than a[i]. If it does not exist, the answer for i is -1."
        ),
        input_format="The first line contains one integer n. The second line contains n integers.",
        output_format="Print n integers: the answer for each position from left to right.",
        constraints=["1 <= n <= 200000", "-10^9 <= a[i] <= 10^9"],
        samples=[
            {"input": "5\n2 1 3 2 4\n", "output": "3 3 4 4 -1\n"},
            {"input": "4\n4 3 2 1\n", "output": "-1 -1 -1 -1\n"},
        ],
        tags=["monotonic stack", "array"],
        solution_explanation=(
            "Scan from right to left and maintain a decreasing stack of candidate values. "
            "Pop values that are less than or equal to a[i]; the remaining top is the next "
            "greater value, then push a[i]."
        ),
        reference_solution="""import sys

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n = data[0]
    arr = data[1:1+n]
    ans = [-1] * n
    stack = []
    for i in range(n - 1, -1, -1):
        while stack and stack[-1] <= arr[i]:
            stack.pop()
        if stack:
            ans[i] = stack[-1]
        stack.append(arr[i])
    print(*ans)

if __name__ == "__main__":
    main()
""",
        brute_force_solution="""import sys

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n = data[0]
    arr = data[1:1+n]
    ans = []
    for i in range(n):
        value = -1
        for j in range(i + 1, n):
            if arr[j] > arr[i]:
                value = arr[j]
                break
        ans.append(value)
    print(*ans)

if __name__ == "__main__":
    main()
""",
        generator_code="""import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
rng = random.Random(seed)
n = rng.randint(1, 90)
if seed % 5 == 0:
    arr = list(range(n, 0, -1))
elif seed % 5 == 1:
    arr = list(range(1, n + 1))
else:
    arr = [rng.randint(-40, 40) for _ in range(n)]
print(n)
print(*arr)
""",
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


def _prefix_sum_problem(req: ProblemRequest, source: str) -> GeneratedProblem:
    problem_id = f"prob_{int(time.time())}_{uuid4().hex[:8]}"
    return GeneratedProblem(
        id=problem_id,
        title="Range Sum Queries",
        topic=req.topic,
        difficulty=req.difficulty,
        statement=(
            "Given an integer array, answer q queries. Each query gives l and r, and asks for "
            "the sum of a[l] through a[r]. Indices are 1-based."
        ),
        input_format=(
            "The first line contains n and q. The second line contains n integers. "
            "Each of the next q lines contains l and r."
        ),
        output_format="For each query, print the requested sum on its own line.",
        constraints=["1 <= n, q <= 200000", "-10^9 <= a[i] <= 10^9", "1 <= l <= r <= n"],
        samples=[
            {"input": "5 3\n1 2 3 4 5\n1 3\n2 5\n4 4\n", "output": "6\n14\n4\n"},
            {"input": "1 2\n-7\n1 1\n1 1\n", "output": "-7\n-7\n"},
        ],
        tags=["prefix sum", "array", "query"],
        solution_explanation="Build prefix sums where pref[i] is the sum of the first i values. Then each query is pref[r] - pref[l-1].",
        reference_solution="""import sys

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n, q = data[0], data[1]
    arr = data[2:2+n]
    pref = [0]
    for x in arr:
        pref.append(pref[-1] + x)
    pos = 2 + n
    out = []
    for _ in range(q):
        l, r = data[pos], data[pos + 1]
        pos += 2
        out.append(str(pref[r] - pref[l - 1]))
    print("\\n".join(out))

if __name__ == "__main__":
    main()
""",
        brute_force_solution="""import sys

def main():
    data = list(map(int, sys.stdin.read().split()))
    if not data:
        return
    n, q = data[0], data[1]
    arr = data[2:2+n]
    pos = 2 + n
    out = []
    for _ in range(q):
        l, r = data[pos], data[pos + 1]
        pos += 2
        out.append(str(sum(arr[l - 1:r])))
    print("\\n".join(out))

if __name__ == "__main__":
    main()
""",
        generator_code="""import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
rng = random.Random(seed)
n = rng.randint(1, 70)
q = rng.randint(1, 70)
arr = [rng.randint(-100, 100) for _ in range(n)]
print(n, q)
print(*arr)
for _ in range(q):
    l = rng.randint(1, n)
    r = rng.randint(l, n)
    print(l, r)
""",
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )


def _string_problem(req: ProblemRequest, source: str) -> GeneratedProblem:
    problem_id = f"prob_{int(time.time())}_{uuid4().hex[:8]}"
    return GeneratedProblem(
        id=problem_id,
        title="Longest Balanced Binary Substring",
        topic=req.topic,
        difficulty=req.difficulty,
        statement=(
            "Given a binary string s, find the maximum length of a contiguous substring that "
            "contains the same number of 0 characters and 1 characters."
        ),
        input_format="The only line contains a binary string s.",
        output_format="Print one integer: the maximum valid substring length.",
        constraints=["1 <= |s| <= 200000", "s contains only characters 0 and 1"],
        samples=[
            {"input": "01001\n", "output": "4\n"},
            {"input": "1111\n", "output": "0\n"},
        ],
        tags=["prefix sum", "hash table", "string"],
        solution_explanation=(
            "Map 0 to -1 and 1 to +1. Two equal prefix balances define a substring with equal "
            "numbers of 0 and 1, so store the first index for each balance."
        ),
        reference_solution="""import sys

def main():
    s = sys.stdin.read().strip()
    first = {0: 0}
    bal = 0
    ans = 0
    for i, ch in enumerate(s, 1):
        bal += 1 if ch == "1" else -1
        if bal in first:
            ans = max(ans, i - first[bal])
        else:
            first[bal] = i
    print(ans)

if __name__ == "__main__":
    main()
""",
        brute_force_solution="""import sys

def main():
    s = sys.stdin.read().strip()
    n = len(s)
    ans = 0
    for i in range(n):
        zeros = 0
        ones = 0
        for j in range(i, n):
            if s[j] == "0":
                zeros += 1
            else:
                ones += 1
            if zeros == ones:
                ans = max(ans, j - i + 1)
    print(ans)

if __name__ == "__main__":
    main()
""",
        generator_code="""import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
rng = random.Random(seed)
n = rng.randint(1, 100)
if seed % 5 == 0:
    s = "0" * n
elif seed % 5 == 1:
    s = "1" * n
else:
    s = "".join(rng.choice("01") for _ in range(n))
print(s)
""",
        created_at=datetime.now(timezone.utc).isoformat(),
        source=source,
    )

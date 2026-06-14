---
name: blind-test
description: |
  产出文件型 skill 的端到端盲测：测试 skill 的产出物（DESIGN.md / 文档 / 模板 / spec）
  能不能被下游 LLM 当 prompt 用——即产出能不能"work"。
  当用户说：
  - "盲测这个 skill"
  - "产出能不能 work"
  - "端到端测试 skill"
  - "DESIGN.md 测下游"
  - "产出文件给下游用对吗"
  - "blind test"
  使用此 skill。
  即使用户没说 "盲测"，只要涉及产出文件型 skill（产出物 = 下游 prompt）的端到端验证，
  也应该触发。
  不要用于：L1/L2/L3 分层评分（→ iterate-skill）/ description 触发率（→ trigger-tune）/
  纯 CLI 工具型 skill（无文件产出，盲测无意义）。
metadata:
  author: Eric Travis Chong
  version: 0.1.0
---

# Blind Test

产出文件型 skill 的端到端盲测方法学。

**为什么要做盲测**：评分（L1/L2/L3）只能告诉你"产出符不符合模板规范"；盲测才告诉你"这东西给别人用，他真能用起来吗"——后者才是这类 skill 真正的存在意义。分数再高、不跑盲测也可能是虚的。

## 前置条件

需要先用 [iterate-skill](../iterate-skill/SKILL.md) 跑完 Step 3，让 executor（负责执行的 subagent）产出 source 文件（如 `source-DESIGN.md`、`source-spec.md`）并放到 run_dir 里。

## 文件结构

```
blind-test/
├── SKILL.md
└── references/
    └── blind-test-methodology.md     ← 完整 4 步流程 + 可直接复制的 Agent B prompt 模板 + 评分 rubric
```

依赖 plugin 顶层共享：
- `../../agents/executor.md` — Step 1-2（准备 fixture、跑 skill），已在 iterate-skill Step 3 跑过
- `../../agents/comparator.md` — 评分参考

## I/O Contract

Input: target skill 产出的 source 文件（如 source-DESIGN.md）+ run_dir 路径
Output: blindtest-output.html + comparison-report.md，写入同一个 run_dir

## 执行流程

完整 4 步见 [references/blind-test-methodology.md](references/blind-test-methodology.md)。

**关键分工**——谁做哪步不能搞错（这是为了防止一个真实翻车复现）：

- **Step 1-2**（准备 fixture、跑 skill 产出 source 文件）
  → 由 executor subagent 完成。**iterate-skill Step 3 已经跑过这一步**，source 文件已经在 run_dir 里了。

- **Step 3-4**（派出全新的 Agent B、做对比评分）
  → **必须由 main orchestrator（调用这个 skill 的 Claude 本体）来做，绝不能交给 executor**：
  1. 读 `<run_dir>/source-DESIGN.md`（或对应 source 文件）
  2. 按 methodology 里的模板填好占位符，组成 Agent B 的 prompt
  3. 用 Agent tool 派出一个全新的、没有任何上下文的 subagent（即 Agent B）
  4. 等它写出 `blindtest-output.html`（或对应产出文件）后读回内容
  5. 写 `comparison-report.md` 到同一个 run_dir（按 methodology 的评分标准打分）

**为什么这样分工**：executor 本身就是 subagent，Claude Code 系统规定 **subagent 不能再嵌套派出 Agent**。如果强行让 executor 做 Step 3，它会悄悄地自问自答来冒充 Agent B（这叫 silent-simulated），盲测就彻底失效了。

真实翻车案例：design-md 第三轮迭代就栽在这里——executor 自己模拟 Agent B 打了 49/50 高分，但产出的 HTML 里出现了 fixture 里才有的精确数值 `translate(-3, 1)`，证明它根本没有"盲"，只是把之前看过的内容照搬进来了（这叫 working memory leak，工作记忆泄漏）。

## 执行规则

1. **分工铁律**：Step 1-2 由 executor 做（iterate-skill Step 3 已完成），Step 3-4 必须由 main orchestrator 亲自做。绝对不允许 executor 去嵌套派 Agent B——那会变成自问自答，测试结果没有意义。

2. **Agent B 的 prompt 必须真正"盲"**：不能把 source 文件的路径告诉它，不能把 source 内容粘进去。严格按 methodology 里的模板填占位符，**一句多余的上下文都不要加**。

3. **把分数映射到 L3.5**：comparison-report.md 里打的分，要对应到 target skill 的 evals/eval-criteria.md 里的 L3.5 子维度（"产出 skill 的品质保证"）。

4. **检查有没有信息泄漏**：如果 blindtest-output.html 里出现了 source 文件里的精确数值、特定命名或特殊标识符，说明 Agent B 不够"盲"或者 prompt 泄漏了内容，必须重跑。

## 完成标准（checklist）

- [ ] Agent B 已被派出并产出了 blindtest-output（不是 main orchestrator 自己写的）
- [ ] comparison-report.md 已生成
- [ ] 分数已映射到 L3.5
- [ ] 没有信息泄漏（blindtest-output 里没有出现 source 文件的精确数值或特定命名）

## Troubleshooting（疑难解答）

**问题：Agent B 什么都没输出，或者输出里有 source 里的精确数值**
原因：prompt 不够"盲"，Agent B 间接看到了 fixture 内容
解法：检查你填的 ready-to-copy 模板，把所有 source 文件路径和内容引用全部移除

**问题：comparison-report 没办法打分，维度对不上**
原因：blindtest-output 和 source-DESIGN.md 考的根本不是同一件事
解法：回看 methodology 里的评分标准，很可能是 target skill 的产出格式本身就跟 rubric 不对齐

**问题：executor 已经自己产出了 blindtest-output**
原因：executor 不该做 Step 3-4，但跑了（这是分工错误）
解法：那个产出是 executor 自问自答出来的，不可信。必须由 main orchestrator 重新派一个全新的 Agent B 来做

## 参考资料

- [references/blind-test-methodology.md](references/blind-test-methodology.md) — 完整 4 步流程 + 可直接复制的 Agent B prompt 模板 + 评分 rubric
- plugin 顶层 `../../agents/executor.md` — Step 1-2 的实现（iterate-skill 里已经跑过）
- 前置依赖：[iterate-skill SKILL.md](../iterate-skill/SKILL.md)（必须先跑完它的 Step 1-3 再来这里）

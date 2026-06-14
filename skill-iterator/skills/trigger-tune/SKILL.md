---
name: trigger-tune
description: |
  优化 skill description 触发准确率：基于 Iterate eval report 的 trigger 标注，
  调整 description，跑触发测试（20 prompt：should-trigger / should-NOT-trigger），用户确认。
  当用户说：
  - "description 触发不准"
  - "trigger accuracy"
  - "20 个 prompt 测一下"
  - "优化触发短语"
  - "误触发太多"
  - "应该触发但没触发"
  使用此 skill。
  即使用户没说 "trigger"，只要涉及 skill description 触发率调优，也应该触发。
  不要用于：内容质量评分（→ iterate-skill）/ 产出端到端测试（→ blind-test）/
  从零创建 skill（→ skill-creator）。
metadata:
  author: Eric Travis Chong
  version: 0.1.0
---

# Trigger Tune

专门调 skill description 的触发准确率——说一句话，这个 skill 该不该自动跳出来。
流程：从 iterate-skill 的评测报告里找出触发问题 → 改 description → 跑 20 道测试题 → 用户拍板。

**本 skill 只做测试，不直接改文件**：测出该改什么之后，改 description 这一步要走 skill-creator 流程
（让 AI 用 Edit 改 target SKILL.md，PostToolUse hook 自动跑 lint 检查）。

## 前置条件

至少满足一个：
- iterate-skill 跑过一次，评测报告里标注了哪些场景触发太少（under-trigger）或触发太多（over-trigger）
- 用户直接告诉你：哪些情况误触发了、哪些本该触发却没触发

## 文件结构

```
trigger-tune/
├── SKILL.md         ← 本文件
└── references/
    └── steps.md     ← Trigger 4 步
```

依赖 plugin 顶层共享：
- `../../scripts/run_eval.py` — 触发测试（临时注入 .claude/commands/ + stream-json + content_block_start early detection + 10 worker 并发）

## I/O Contract

Input: target skill SKILL.md（含 description）+ 触发问题清单
Output: 改好的 description + 触发测试结果 evals/workspace/trigger-test/results.json + 用户确认

## 执行流程

→ [references/steps.md](references/steps.md)（Step 1-4：读问题 → 改 description → 触发测试 → 用户确认）

**Pattern**：Sequential 骨架，Iterative Loop 包住 Step 2-4（不通过就循环回 Step 2）。

## 执行规则

1. **测试题至少 20 道**：10 道"应该触发"+ 10 道"不该触发"。少于 20 道样本太少，结论不可信。

2. **"不该触发"的测试题必须是边界题，不能拿明显无关的凑数**：比如 skill 是"帮你设计网页"，测"帮我写斐波那契"毫无意义——任何 description 都不会触发，测不出任何东西。要拿**看起来很像、但其实不该触发**的题，比如"帮我设计一个 logo"——这样才能真正测出 description 的触发边界画在哪。

3. **改 description 走 skill-creator**：本 skill 测出该改什么，**Edit target SKILL.md 让 AI 走 skill-creator** 流程。PostToolUse hook 自动跑 quick_validate.py 验合规。

4. **通过率阈值**：should-trigger 通过率 ≥ 90%，should-NOT-trigger 正确拒绝率 ≥ 90%。不通过回 Step 2 继续调。

## 完成标准（Done = 这四项全打勾）

- [ ] 触发测试跑完（≥ 20 个 prompt）
- [ ] should-trigger 通过率 ≥ 90%
- [ ] should-NOT-trigger 正确拒绝率 ≥ 90%
- [ ] 用户确认通过

## Troubleshooting（遇到报错看这里）

Error: run_eval.py 找不到
Cause: plugin 顶层 scripts/ 未正确 install 或路径错
Solution: 确认 `python3 ../../scripts/run_eval.py --help` 能跑

Error: stream-json 解析失败
Cause: claude CLI 版本不匹配（output-format 字段名变更）
Solution: 检查 `claude --output-format stream-json --help` 输出，更新 run_eval.py 中的字段解析

Error: should-NOT-trigger 全部误触发
Cause: description 太 pushy（"即使...也应该触发"覆盖范围太广）
Solution: 收紧 pushy markers 边界 + 加 strict markers（"不要用于：..."）

Error: should-trigger 大量漏触发
Cause: description 太 strict 或触发短语没覆盖用户实际说法
Solution: 加更多触发短语（用户视角语句）+ 加 "即使没说 X 也应该触发"

## 参考资料

- [references/steps.md](references/steps.md) — Trigger 4 步执行步骤
- plugin 顶层 `../../scripts/run_eval.py` — 触发测试核心实现
- 触发问题来源：[iterate-skill SKILL.md](../iterate-skill/SKILL.md)（先跑 iterate 拿 eval report）

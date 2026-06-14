---
name: iterate-skill
description: |
  让 skill 真干一次活 → 打分（分三关：有没有做出来 / 做得对不对 / 做得好不好）→ 给改进建议 → 出成绩单。
  每道题强制跑两遍（一遍用 skill、一遍不用），逼自己回答「这 skill 到底有没有真本事」。
  当用户说：
  - "跑 L1/L2/L3 评分"
  - "benchmark 这个 skill"
  - "看 skill 内容质量"
  - "iteration N"
  - "下一轮迭代"
  - "改进 skill 内容"
  使用此 skill。
  即使用户没说 "iterate"，只要涉及对已有 skill 跑分层质量评估或多轮内容改进，也应该触发。
  不要用于：description 触发率优化（→ trigger-tune）/ 产出端到端测试（→ blind-test）/
  从零创建 skill（→ skill-creator）。
metadata:
  author: Eric Travis Chong
  version: 0.1.0
---

# Iterate Skill

让 skill 真干一次活，打三关分（有没有做出来 / 做得对不对 / 做得好不好），给出改进建议，出成绩单。
每道题强制跑两遍——一遍开 skill、一遍不开——逼自己回答「这 skill 到底有没有真本事」。

**只读不改**：本 skill 不会动 target skill 的任何文件。要改 target，必须走 skill-creator (Eric Travis version)。

## 前置条件

target skill 必须有 `evals/eval-criteria.md`（里面写了 L1/L2/L3 评分标准 + 测试题）。
没有这个文件 → 先让用户用 **skill-creator** 的 Step 3 生成，再回来跑。

## 文件结构

```
iterate-skill/
├── SKILL.md          ← 本文件
└── references/
    └── steps.md      ← Iterate 5 步 + Step 5.5 (Review + Apply)
```

依赖 plugin 顶层共享（详见 [umbrella SKILL.md](../skill-iterator/SKILL.md) 前置共享）。

## 输入 / 输出

输入：一个已有 skill 的目录（里面要有评分标准文件 `evals/eval-criteria.md`）
输出：打分结果 + 改进建议持久化到 `evals/workspace/iteration-N/`，然后等用户决定继续下一轮还是结束

## 执行流程

→ [references/steps.md](references/steps.md)（Step 1-5 + Step 5.5）

整体结构：按顺序跑五步（Sequential），Step 1 内部根据起点分三条路（Conditional），Step 5 跑完可以回到 Step 1 再来一轮（Iterative Loop）。

## 执行规则

1. **grader agent 直接写 grading-l3.json**：按 `../../references/schemas.md` §5 的格式写。
   grader prompt 里把字段和格式规则列清楚，grader 照着写就行。
   为什么：如果 grader 先输出 markdown、再 parse、再 validate，三步走每步都可能漂移出错；一次按 schema 写对，省两个中间脚本。

2. **只读不改（铁律）**：本 skill 绝不 Edit/Write target 文件。Step 5.5 里 Travis 决定要 apply 哪些建议，apply 时由 AI 走 skill-creator 流程去改 target。
   为什么：评卷的人不能同时改试卷——利益冲突。

3. **必须打开 viewer 给用户看**：Step 5 一定要打开 eval viewer，不能只报一串数字。
   如果 grading pipeline 没跑完整，跑 `prepare_viewer.py --minimal` 仍能把已有产出推进 viewer。
   「用户能看到产出」比「metrics 完美」更重要。

4. **三关分开评，不混用**：第一关（有没有做出来，系统里叫 L1）和第二关（做得对不对，L2）用脚本自动评；第三关（做得好不好，L3）专门交给 grader agent 评。

5. **每道题强制跑两遍（系统里叫 baseline 双跑）**：with_skill（开 skill）+ without_skill（不开 skill）各跑一次。
   为什么：每轮迭代都必须正面回答「这 skill 比 Claude 裸跑到底强在哪里」——防止越跑越不知道在优化什么。
   如果某道题只想跑一遍：在 eval-plan.json 给该 case 加 `"configurations": ["with_skill"]`。

6. **检查 SKILL.md 合规 vs 检查 case 产出质量，是两件不同的事**：
   `grade_skill_artifact()` 是检查这次产出的 SKILL.md 格式是否合规（YAML、section 是否齐全等），**不是**在评每道题的答案好不好。
   每道题答案的质量靠 eval-plan.json 里写的 assertions + grader agent 评 L3。
   把这两件事混在一起，会导致该检查的没检查、不该算的被算进去（这是早期踩过的坑，直接导致一轮 iteration 卡住）。

7. **如果 target skill 的产出物会被下游 LLM 当 prompt 用，建议叠加 blind-test**：比如 target skill 产出的是 DESIGN.md / 文档 / 模板这类东西，L1/L2/L3 只测「产出符不符合规范」，但不测「产出能不能真正 work」。后者要另外跑 [blind-test skill](../blind-test/SKILL.md)。

8. **每轮跑完，有重要发现就更新 Decision Log**：架构变更、eval 标准调整、意外发现，记进 `../../decision.md`（plugin 层不强制，target skill 层强烈推荐）。

## 完成标准（怎么算做对了）

- [ ] eval plan 已确认并执行
- [ ] L1/L2 脚本评分完成（grading-l1-l2.json 存在且合法）
- [ ] L3 grader agent 评分完成（grading-l3.json 存在且合法）
- [ ] suggestions.json 已生成
- [ ] eval viewer 已打开，用户已看到结果
- [ ] 用户明确说"满意"或"继续下一轮"

## Troubleshooting（碰到问题怎么办）

Error: 找不到 evals/eval-criteria.md
Cause: 目标 skill 未经过 skill-creator 的 Step 3
Solution: 先用 skill-creator 为目标 skill 生成 evals/eval-criteria.md

Error: session 中断，eval 进度丢失
Cause: 大模型断网或 token 耗尽
Solution: 所有数据已持久化到 evals/workspace/iteration-N/。重新进入会从 Step 1 自动检测断点继续。

Error: grading-l3.json 格式错误
Cause: grader agent 输出的 JSON 不符合 schemas.md §5
Solution: 重新 spawn grader agent，明确指令"严格按 schemas.md §5 重写"

Error: viewer 打不开
Cause: 端口被占用或 grading 数据不完整
Solution: 用 `python3 ../../scripts/eval_viewer/generate_review.py --static` 生成独立 HTML 文件直接打开

## 参考资料

- [references/steps.md](references/steps.md) — Iterate 5 步 + Step 5.5
- plugin 顶层 `../../references/schemas.md` — grading JSON schema
- plugin 顶层 `../../agents/` — executor / grader / comparator / suggest
- 叠加测试：[blind-test SKILL.md](../blind-test/SKILL.md)（产出文件型 skill）
- 触发优化：[trigger-tune SKILL.md](../trigger-tune/SKILL.md)（description tuning）

---
name: skill-iterator
description: |
  前台 · 分诊台：听懂你想测一个已有 skill 的哪方面，把你领到 iterate-skill / trigger-tune / blind-test 三条流程之一。
  当用户说：
  - "帮我改进 skill"
  - "跑 eval 测一下"
  - "benchmark 这个 skill"
  - "这个 skill 的触发准不准"
  - "A/B 对比两个版本"
  - "盲测产出物"
  - "跑 iteration"
  使用此 skill 做意图识别 + 路由。
  即使用户没说 "iterator" 字面词，只要涉及对已有 skill 做评估 / 改进 / 测试，也应该触发。
  不要用于：从零创建新 skill（→ skill-creator）/ 解释 skill 概念（直接答即可）。
metadata:
  author: Eric Travis Chong
  version: 0.1.0
---

# Skill Iterator（前台 · 分诊台）

你拿一个**已经做好的 skill** 来，说想测它。这个文件只干一件事：**听懂你想测哪方面，把你领到对的房间**——它本身不做测试，只做分诊。

**重要：只评不改。** 这个 plugin 全程不碰你那个被测的 skill 文件。需要改的时候，改的活交给 skill-creator（Eric Travis 版）；本 plugin 只负责测、只负责给出一份「改进建议清单」。

## 输入 / 输出

- 输入：你对一个已有 skill 的「评估 / 改进 / 测试」请求
- 输出：把你领进 3 个房间之一 —— iterate-skill / trigger-tune / blind-test

## 怎么分诊（听你想测什么 → 进哪个房间）

- 想知道**内容好不好**（它干出来的活准不准、好不好）→ **iterate-skill**（房间 1）
- 想知道**触发词准不准**（你说一句话，它该不该跳出来；有没有乱跳或漏跳）→ **trigger-tune**（房间 2）
- 想知道**产出能不能用**（产出文件型的 skill：把它产出的东西丢给别人，别人能不能照着用起来）→ **blind-test**（房间 3）
- **听不出来你要哪个** → 别猜，直接问你三选一：「你想测哪样：(a) 内容好不好 (b) 触发词准不准 (c) 产出能不能用？」

**要一次测好几样怎么办？** 顺序固定：先 iterate-skill，再 trigger-tune。原因：iterate-skill 出的成绩单里会顺手标出「哪些触发词有问题」，这正好是 trigger-tune 的输入 —— 先有成绩单，trigger-tune 才有的放矢。

## 三个房间共用的家当

3 个房间都靠 plugin 顶层这几样东西干活（在这里统一说明，免得每个房间各讲一遍）：

- `../../scripts/` — 干活的脚本（跑测试、打分、出成绩单网页那一堆）
- `../../agents/` — 4 个 AI 小工：executor（执行者）/ grader（评分者）/ comparator（对比者）/ suggest（建议者）
- `../../assets/eval_viewer/` — 成绩单网页的模板
- `../../references/schemas.md` — 各种中间文件长什么样的唯一标准（要改格式，只改这一处）

## 完成标准（怎么算这一步做对了）

- [ ] 你的请求被领进了对的房间
- [ ] 听不出来你要哪个时，主动问了，没自作主张
- [ ] 你要一次测好几样时，按「先 iterate → 再 trigger → 再 blind-test」的顺序走

## 疑难解答（卡住了怎么办）

问题：你既要测内容、又要调触发词
为什么：一个房间不够用
怎么办：先进房间 1（iterate-skill，它的成绩单会标出触发词问题）→ 再进房间 2（trigger-tune）

问题：被测的 skill 没有 `evals/eval-criteria.md` 这份文件
为什么：它还没经过 skill-creator 的第 3 步（那一步才会给它生成评分标准）
怎么办：先让用户用 skill-creator 给它补上 `evals/eval-criteria.md`，再回来测

问题：拿不准该进哪个房间
为什么：你的描述同时沾了好几个房间的边
怎么办：直接问你三选一，不要猜

## 参考资料

- [iterate-skill](../iterate-skill/SKILL.md) — 房间 1：测内容好不好（最常用的主流程）
- [trigger-tune](../trigger-tune/SKILL.md) — 房间 2：调触发词准不准
- [blind-test](../blind-test/SKILL.md) — 房间 3：测产出能不能用

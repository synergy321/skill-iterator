# Skill-Iterator-Plugin 架构说明

这个文档讲：这个 plugin 为什么拆成 4 个 skill、各层的资源放在哪里、跟 skill-creator 之间约定了什么。

---

## 为什么拆成 4 个 skill

| skill | 干什么 | 用户怎么触发 |
|---|---|---|
| **skill-iterator** | 总入口——判断你说的是哪类需求，分流到下面 3 个 | "帮我改进 skill" / "跑 eval" / "benchmark" |
| **iterate-skill** | 主流程：跑 L1/L2/L3 评分 + 出报告 + 产下一轮改进建议 | "L1/L2/L3 评分" / "看 skill 质量" / "下一轮迭代" |
| **trigger-tune** | 专门调 skill 的 description 触发准不准 | "触发不准" / "20 prompt 测一下" |
| **blind-test** | 端到端盲测：让 skill 跑一遍真实任务，看产出能不能用 | "盲测" / "产出能不能 work" |

**为什么不做成 1 个 skill**：如果全塞进一个 skill，SKILL.md 会超过 400 行，description 边界模糊，触发时判断不准。

**为什么不拆成 5 个（OpenAI 那种）**：OpenAI 那个 plugin-eval 拆了 `evaluate-skill` + `evaluate-plugin` 是因为它要测两种对象（skill 和 plugin）。我们只测 skill，多拆一层没意义。

**blind-test 为什么单独拆出来**：老版本（v2）把盲测藏在 iterate-skill 执行规则第 7 条里，用户得先触发 iterate 才能想起来跑盲测。拆出来之后，用户说"产出能不能 work"就能直接触发，不用绕路。

---

## 资源在哪里：plugin 顶层 vs skill 内部

### Plugin 顶层放的东西（4 个 skill 共用，改一处全生效）

```
Skill-Iterator-Plugin/
├── scripts/          ← 13 个 .py 脚本（含 __init__.py）+ eval_viewer/ 子目录
├── agents/           ← 4 个子 agent：executor / grader / comparator / suggest
├── assets/           ← HTML 模板（eval_viewer/ 用）
├── references/       ← plugin 级文档（schemas.md + 本文件 architecture.md）
└── hooks/            ← hooks.json（生效版）+ hooks.json.example（有完整注释的参考版）
```

### Skill 内部放的东西（每个 skill 各自一份）

```
skills/<skill-name>/
├── SKILL.md          ← 这个 skill 的聊天入口：触发短语 + 输入输出约定 + 完成标准
└── references/       ← 这个 skill 专属的文档
    └── steps.md      ← 这个 skill 的详细执行步骤
```

skill 内部**不放 scripts/agents/assets 的副本**——通过相对路径 `../../scripts/...` 引用顶层的那一份。

**为什么这样分**：
- scripts/agents 是具体逻辑，如果每个 skill 各自一份，改了顶层忘了改 skill 内的，两份就会悄悄跑偏（`quick_validate.py` 头部注释写明了："Both copies MUST stay identical"——所以宁可只存一份）
- SKILL.md 是每个 skill 各自的聊天界面，必须独立（`quick_validate.py` 规则 4：6 类顶级目录是铁律）
- references/steps.md 流程文档每个 skill 独立，因为 iterate-skill 和 trigger-tune 的步骤数和内容完全不同

---

## 跟 skill-creator 的约定

这个 plugin 和 Travis 版 skill-creator 之间有一个系统级约定：

| skill-creator 产出什么 | 本 plugin 怎么用 |
|---|---|
| `evals/eval-criteria.md`（L1/L2/L3 定义 + Eval Cases） | iterate-skill 第 1 步读它，当作这轮 eval 的基准 |
| `scripts/quick_validate.py` | 本 plugin 的 scripts/ 里有一份**必须完全一致**（头部注释强制要求）|
| 6 类目录铁律 / 5 条架构合规规则 | hooks/hooks.json 的 Hook 1+2 调用 quick_validate.py 来强制检查 |

**只读原则（read-only invariant）**：这 4 个 skill **永远不直接修改被测 skill 的文件**。所有对被测 skill 的改动（按建议应用）都走 skill-creator 流程：让 AI 用 Edit/Write 改目标文件 → PostToolUse hook 自动跑 quick_validate.py 检查是否合规 → 不合规 AI 自己修 → 循环到 lint 通过为止。

WHY：评的人不能改被评的，这是信任边界（跟 OpenAI 的 metric pack additive 模型思路一样）。

---

## 4 + 1 个自动化 Hook

Hook 是"某件事发生后自动触发的检查"。这里有 4 个：

| Hook | 什么时候触发 | 针对哪些文件 | 干什么 |
|---|---|---|---|
| Hook 1 | 任何 Edit/Write/MultiEdit 操作完成后 | 文件名匹配 `SKILL.md$` | 跑 quick_validate.py，检查 SKILL.md 架构合规 |
| Hook 2 | 任何 Edit/Write/MultiEdit 操作完成后 | 工作区中间文件 | 跑 quick_validate.py --workspace，检查 JSON schema 合规 |
| Hook 3 | Claude Code 停下来（Stop 事件）时 | — | 检查这轮 iteration 有没有收尾 |
| Hook 4 | 每次开新 session（SessionStart）时 | — | 列出正在进行中的 iteration（默认 `_disabled`，删掉字段才启用）|

v0.1 默认只开 **Hook 1+2**（lint 强制，必须有）。Hook 3+4 写在 `hooks.json.example` 里当参考，不默认打开。

WHY：Travis 的 guardrail-or-die 原则——lint 比提醒优先。lint 不通过会进 AI context 让它自修；提醒类的 hook 是加分项。

---

## 这个 plugin 借鉴了谁的什么

| 借鉴了什么 | 来自哪里 | 落在这里哪个位置 |
|---|---|---|
| 多个聊天入口 + 聊天层/核心引擎分层设计 | OpenAI plugin-eval | 4-skill 拆分 + plugin 顶层 scripts/ |
| workspace/iteration-N 目录组织 + viewer 回顾 + train/test 分离 | Anthropic skill-creator | iterate-skill / trigger-tune 步骤 |
| L1/L2/L3 评分 + 3→4 分边界 + 6 类目录铁律 + 5 条架构合规规则 | Travis Eric 版 | 全部保留，不动 |
| read-only invariant（评的人不改被评的） | Travis Eric 版（独有） | 全部 4 个 skill 强制执行 |

---

## 关联文档

- plugin 顶层 `references/schemas.md` — 所有 JSON 格式的唯一标准（改字段必须先改这里）
- skill 内部 `references/steps.md` — 每个 skill 自己的详细执行步骤
- skill 内部 `references/blind-test-methodology.md`（仅 blind-test skill）— 盲测 4 步方法
- hooks/hooks.json.example — 4 个 hook 的完整示例 + 内联注释

# Blind Test Methodology — 产出文件型 skill 专用

## 适用场景

只在 **产出物会被下游 LLM 当 prompt / 输入用** 的 skill 上跑这套：
- 设计系统文档（DESIGN.md / design tokens spec）
- 文档生成器（README / docs / spec sheets）
- Prompt 模板生成器
- 任何"产出 = 给下一个 agent 当输入"的 skill

**不适用于**：交互型 skill（处理 reject / clarify 这类交互的）、评估另一个 skill 本身的质量（用 grader.md 就够了）、产出物不会被下游 LLM 读取的 skill。

评分（L1/L2/L3）能告诉你"产出符不符合规范"。但这套盲测真正考的是"**产出物拿给一个完全不知道背景的 AI，它能用起来吗**"——这两件事不一样，必须分开测，因为产出符合规范不代表产出有用。

---

## 核心流程（4 步）

```
┌─────────────────────────────────────────────────────────────┐
│  Step 1: Spawn Agent A → craft fixture                      │
│  Step 2: Run skill on fixture → extract artifact            │
│  Step 3: Spawn Agent B (FRESH, no context) →                │
│           use artifact only, generate downstream output     │
│  Step 4: Compare fixture ↔ Agent B output                   │
└─────────────────────────────────────────────────────────────┘
```

### Step 1: 派出 Agent A，让它造一份测试素材（fixture）

这一步是为了准备一份真实可信、风格足够具体的测试用素材，后面的所有对比都以它为基准。

让 Agent A 在 prompt 里做到这几点：
- **风格要具体，不能模糊** —— 不要写"现代简约 SaaS 风"，要具体到能用 2-3 个词说清楚（比如"risograph 丝网印刷风格 + 暖色纸张质感 + offset 印刷错位感"）。越具体，下游 Agent B 越难"随便猜对"，测试才有区分度。
- **产出必须能独立运行** —— fixture 要是完整的东西（完整 HTML / 完整文档等），不是片段。
- **把设计决策藏在注释里** —— 让 Agent A 把"我做了什么决定、为什么"写在 HTML 末尾注释里（`<!-- DESIGN_INTENT: ... -->`）。这样你事后能验证 fixture 真的有足够的设计信号，但 Agent B 看不到这些注释，不会被剧透。
- **明确禁止参考知名品牌** —— 在 prompt 里写清楚"Don't copy Linear / Vercel / Stripe"。

为什么不直接用真实网站当 fixture：见下面 [Setup 前提](#setup-前提-case-selection-rule)。

### Step 2: 在 fixture 上跑 skill，拿到产出物（artifact）

这一步是为了得到"skill 的真实产出"——不要手动调 prompt，就按 skill 的默认流程跑。如果 skill 有中间产出，也保留下来，方便后续排查问题。

artifact（产出物）是接下来交给 Agent B 的唯一素材。

### Step 3: Main orchestrator 派出全新的 Agent B（没有任何上下文）

这一步是为了模拟"一个完全不知道背景的人拿到产出物之后能不能用起来"——这才是盲测的核心。

**执行分工（硬性规定）**：Step 3 必须由 **main orchestrator** 来做——也就是调用 skill-iterator 的 Claude 本体，**不是** executor subagent。

为什么这样规定：Claude Code 系统不允许 subagent 再嵌套派出 Agent。如果让 executor 去派 Agent B，它会悄悄地退回到"自己扮演 Agent B 自问自答"（技术上叫 silent-fallback to self-simulation），盲测就失去意义了。

真实翻车案例：design-md 第三轮迭代就是这么翻的——executor 自己模拟 Agent B 打了 49/50，看起来很高，但产出的 HTML 里出现了 fixture 里才有的精确数值 `translate(-3, 1)`，这只能是它之前看过这个数据、在工作记忆里还留着（working memory leak，工作记忆泄漏），根本不是真正的盲测。

**Agent B 必须满足**：
- 完全没有任何上下文（不知道 fixture 长什么样）
- 只能看到 artifact（DESIGN.md / 模板 / 其他产出文件）
- 任务是"基于这份 artifact，生成对应的配套产出"，比如：
  - 例：fixture 是作品集首页 → Agent B 生成作品详情页
  - 例：fixture 是 README → Agent B 生成 CONTRIBUTING.md
  - 例：fixture 是品牌语气文档 → Agent B 写一段产品发布文案

Prompt 里要明确写：**"do NOT search the web, do NOT try to identify what site/brand this is from"**（不要搜网络，不要猜这是哪个品牌的东西）。

**可以直接复制的 Agent B prompt 模板**（main orchestrator 填好占位符后，通过 Agent tool 派出）：

```
You are Agent B in a blind test. You receive ONLY a {{ARTIFACT_TYPE}} below — you have NO access to the original fixture/source this artifact describes.

Your job: {{DOWNSTREAM_TASK}}

## Rules
- Do NOT search the web.
- Do NOT try to identify what brand/site/project this is from.
- Do NOT read any file other than the artifact content I paste below.
- Output: save {{EXPECTED_OUTPUT}} to exactly this path: {{OUTPUT_PATH}}
- Honor the signals in the artifact faithfully (hex codes, typography families, tone, stroke tiers, signature moves — use exactly what's named).

## Artifact content (the ONLY thing you have access to)

{{ARTIFACT_CONTENT}}

Return a brief report (under 100 words) describing design decisions you made and any gaps in the artifact that forced guessing.
```

占位符说明：
- `{{ARTIFACT_TYPE}}`：例如 `DESIGN.md`、`README.md`、`brand voice doc`
- `{{DOWNSTREAM_TASK}}`：例如 `generate a companion PROJECT DETAIL page as self-contained HTML`
- `{{EXPECTED_OUTPUT}}`：例如 `a valid HTML file`
- `{{OUTPUT_PATH}}`：run_dir 里的绝对路径，例如 `/Users/travis/.claude/skills/design-md/evals/workspace/iteration-N/results/<case>/with_skill/blindtest-output.html`
- `{{ARTIFACT_CONTENT}}`：把 executor 产出的 `source-DESIGN.md`（或对应 artifact）的完整内容粘贴进来

### Step 4: Main orchestrator 对比 fixture 和 Agent B 的产出

这一步是为了用客观评分说清楚"skill 的产出有没有让 Agent B 用起来接近原始意图"。

同样由 main orchestrator 来做（它手里同时有 fixture 原文 + blindtest-output + DESIGN_INTENT 注释，可以做完整对比）。把评分结果写成 `comparison-report.md` 放到 run_dir。

评分维度**因产出物类型而异**——别用错了对应关系。下面按三种常见类型各给一套：

**视觉类产出物**（DESIGN.md / 品牌规范 / 设计文档；Agent B 用它生成 UI 或视觉资产）
- 颜色 / Token 是否还原（hex 是否一致）
- 字体 / 排版是否还原
- 几何结构 / 布局是否还原
- 整体氛围 / 情绪 / 语气是否还原
- 关键签名细节（比如 offset 印刷感 / 特定 padding 规律）是否还原

**文档类产出物**（README / spec / API 文档 / runbook；Agent B 用它回答问题或帮人上手）
- 关键信息覆盖：fixture 里藏的重要事实 / 命令 / 注意点，Agent B 有没有准确提到？
- 命令 / API 准确度：Agent B 提到的命令、参数有没有编造？（必须和 fixture 里的一致）
- 边界情况覆盖：fixture 里有的 troubleshooting 场景，Agent B 能回答吗？
- 上手完整度：Agent B 能不能一步步带人装好、跑起来？
- 没有凭空捏造的函数签名或 API 接口

**Prompt 模板类产出物**（system prompt / few-shot 模板 / 指令规范；下游 AI 实例照着去做事）
- 约束遵守：Agent B 的输出有没有遵守 fixture 里定义的所有硬性要求（格式 / 字数 / 禁用词）？
- 语气一致：Agent B 的输出语气有没有跟模板要求的一致？
- 边界情况处理：模板里写的 fallback / 拒绝行为，Agent B 有没有触发？

**评分映射（三种类型通用）**：
- 各维度平均 ≥ 8/10 且不需要补充 prompt = L3.5 得 5 分
- ≥ 6/10 = 4 分
- 部分维度对、部分偏 = 3 分
- 完全对不上 = 1 分

如果你要测的产出物不在以上三类，**别硬套**——根据"这个产出物的下游真正考验什么"自己列 4-5 个维度，但维度名要对应可观察的行为，不要写得过于抽象或诗化（参考 design-md 的教训：维度名模糊了，打分就没有可操作性）。

---

## Setup 前提（Case Selection Rule）

**fixture 必须是 AI 训练数据里没见过的内容。** 原因很简单：如果 AI 在训练阶段就见过这个素材，它在 Agent B 那步并不是靠 artifact 来"还原"——它是靠记忆直接"背"出来的，测试就彻底失效了。

举个会出问题的例子：假设你拿一个知名设计师的获奖作品集当 fixture，Agent B 输出的视觉一致性可能看起来很高——但那很可能不是它靠你给的 artifact 还原出来的，而是它训练时就见过这个网站、直接凭记忆"背"出来的（比如脑补出 artifact 里根本没写的品牌名、地点、配色）。这种情况下分数再高也没意义，等于没测。

**绝对不能用这些来做 fixture**：
- 获过奖的设计（Awwwards / FWA / Red Dot 上榜的）
- 知名 SaaS 营销网站（Stripe / Linear / Vercel / Notion / Tailwind 之类）
- 有一定流量的设计师作品集
- 训练数据截止日期之前发布且被大量收录的内容

**推荐用这些来做 fixture**：
- **让 Agent A 凭空造**（首选，最可控，肯定在训练数据外）
- 你自己还没公开 / 刚上线的 app 或小众网站
- 朋友 / 学生的个人网站（没什么流量）
- 自己手写的 demo HTML

**选好 fixture 之后，过两关再用**：
1. 先搜一下，确认这个东西流量不大、没有广泛传播
2. 让另一个独立的 LLM 描述这个素材——如果它能详细说出视觉细节，说明训练数据里有，换一个

### 基准线信息公平原则（Baseline Info-Access Principle）

做 A/B 对比时，"没有 skill"那一组（`without_skill`）拿到的信息**不能比真实使用者拿到的更多**。对于视觉提取类 skill（design-md / 任何产出 prompt 前缀 artifact 的 skill）来说，这条规则非常关键：

- **错误做法**：把带完整 `<style>` 块和 CSS 变量的完整 fixture 丢给 `without_skill` 组。一个什么 skill 都没有的 AI，只需要扫一遍就能把所有颜色 hex 值都抄走——这等于把答案直接给它了，比的不再是 skill 有没有价值，而是"谁复制 hex 更准"。真实翻车：design-md 第三轮和第四轮 Case 2 的 `without_skill` 都打到了 49/50，就是因为它直接读了 fixture 里的 `<style>` 块。A/B 对比完全没有意义了。
- **正确做法**：给 `without_skill` 组一个**精简版 fixture**——把 inline `<style>` / `<script>` / inline color 属性全部删掉，只保留 HTML 结构、文案内容和资源链接。这才是"一个没有 skill 的 AI 真正能看到的输入"。

所以 fixture 作者在设计 case 时要提供两个版本：
- `fixtureN-full.html` — 给 `with_skill` 组用（完整输入，skill 本来就该处理这种）
- `fixtureN-stripped.html` — 给 `without_skill` 组用（去掉样式后的裸结构，模拟真实的信息劣势）

为什么要这么麻烦：这套 A/B 测的是"skill 的提取和转化能力"，不是"谁更擅长复制 hex 值"。如果把答案直接放在基准组的输入里，整个测量就废了。

怎么生成精简版：最简单是用 `sed` 删掉 `<style>...</style>` 块，或者写一个一次性的 Python 小脚本。把 strip 方法记录在 fixture 的 setup notes 里，方便下次复现。

---

## Troubleshooting — 碰到问题先排查是哪层出错

碰到"blindtest 结果和 fixture 差距很大"时，**不要直接把锅扣在 skill 上**。按下面三层顺序排查——大多数问题出在前两层，不是 skill 本身。

### 第 1 层：测试方法有没有错？

先确认测试本身是不是有效的：

- fixture 素材是不是 AI 训练数据里见过的内容？（这是最常见的失效原因）
- Agent B 真的没有收到 fixture 的 HTML 吗？（检查 prompt 有没有漏写禁止搜网的规则）
- 对比对象有没有搞错？（应该是 fixture vs blindtest-output，不是 fixture vs DESIGN.md——后者根本不是盲测）
- Agent B 的任务描述合理吗？（"生成 about 页" 和 "生成跟 home 一样的 home" 是完全不同的难度）

### 第 2 层：fixture 本身有没有问题？

测试方法没错再看素材质量：

- fixture 的风格够不够具体？太通用的 demo 测不出 skill 价值——因为下游无论怎么生成都"差不多"
- fixture 里的信号太少吗？（只有 3 种颜色 + 1 种字体 → skill 没东西可提，产出物必然单薄，Agent B 靠猜也能过）
- fixture 里有没有干扰噪声（无关装饰、自相矛盾的元素）影响 skill 的提取？

### 第 3 层：才轮到 skill 本身

前两层都排除了，再看 skill：

- skill 有没有漏掉某个维度？（比如 design-md 早期漏掉了 stroke width，下游所有线条都压成了 1px）
- skill 的转化规则太模糊？（用 "thin" 表示两个档位，Agent B 分不清该用哪个）
- skill 的命名约束有没有矛盾？（"hex 只能写在 Color 那一节" + "所有地方都要用 hex 描述颜色"——互相打架）

### 真实案例

design-md 第二轮迭代发现三档分隔线变成了一档：
- 第一反应：skill 有问题，应该匿名化 DESIGN.md 里的标题
- Travis 推翻了：其实是第 1 层出错了（素材选了一个知名网站，AI 训练数据里有它，测试被污染了）
- 后来又发现 stroke vocab 那次问题：那次才真的是第 3 层——skill 的 translation-map 漏掉了 stroke width 维度

**按 1 → 2 → 3 的顺序排查，省时间也省 token。** 直接跳到第 3 层意味着你要改 skill；但如果测试本身是错的，改了 skill 跑同样的测试还是得同样的（错误的）结论。先把测试环境排干净，结论才真的反映 skill 质量。

---

## 盲测 Case 设计

每个产出文件型 skill 至少要有 2 个 case：
- **普通难度（happy）**：fixture 风格中等复杂（5-6 种颜色 + 2 种字体 + 有明确整体感 + 1-2 个签名细节）
- **压力测试（stress）**：fixture 风格极度具体（多档线条粗细 / 罕见混合模式 / 非主流网格布局）

压力测试 case 才是真正检验 skill 能不能准确转化风格信号的——普通难度的 case，任何 skill 随便跑一下都能"差不多过"，看不出高低。

---

## 接入 Iterate 流程

skill-iterator 的 Iterate Step 4（打分）跑完 L1/L2/L3 之后：

```
IF skill 是产出文件型（artifact 会被下游 LLM 当 prompt 用）→
   必须额外跑 blind test（按本文档 4 步流程）
   → 评分映射到 L3.5
   → 这一分比 L1/L2/L3 加在一起更能反映 skill 的真实价值
```

不跑盲测，等于只测了"产出符不符合规范"，没测"产出能不能被人用起来"。对产出文件型 skill 来说，后者才是它存在的意义。

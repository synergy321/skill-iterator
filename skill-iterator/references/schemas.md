# skill-iterator — JSON Schemas（所有中间文件格式的唯一标准）

> 这是这条流水线读写的**所有 JSON 文件的唯一格式定义**。改任何文件的字段，先来这里更新，再改脚本——不要让两边跑偏。
>
> 来源参考了 Anthropic 的 `skills/skill-creator/references/schemas.md` 的设计思路：需要某个字段，先来这里查；要改某个 schema，改的那个 commit 里必须同时更新这里。
>
> **一个规则**：脚本读 eval-plan 里的字段，必须走 `utils.load_eval_plan()` 拿（它会帮你填默认值）。其他 JSON 文件直接读。所有默认值统一放在 `utils.py`，不要散在各脚本里各自 hardcode。

---

## 文件地图（一眼看清各文件是谁生、谁用、干什么）

| 文件 | 谁写进去 | 谁读它 | 干什么 |
|---|---|---|---|
| `eval-plan.json` | 用户手写 | orchestrator 的入口 | 这次跑哪些 case、配置是什么 |
| `eval_plan.json` | `prepare_viewer.py` 生成 | viewer、`generate_benchmark.py`（兜底用） | 规范化之后的 eval-plan 副本，viewer 能直接读 |
| `results/<case>/[config/][run-N/]grading-l1-l2.json` | `grade_l1_l2.py` 或 target 的 `evals/checks.sh`（由 grade 调用） | `prepare_viewer.merge_grading`、`generate_benchmark.collect_runs` | L1/L2 代码自动检查的结果 |
| `<skill>/evals/checks.sh` | skill 作者 / iterate-skill 工作流（不是 skill-creator）写 | `run_iteration.py grade`（skill_production 模式）| 被测 skill 自己声明的 `[code]` L1/L2 检查脚本；backlog#1 的修复方案 |
| `results/<case>/[config/][run-N/]grading-l3.json` | grader 子 agent | `prepare_viewer.merge_grading`、`generate_benchmark.collect_runs` | L3 断言 + rubric 分数 |
| `results/<case>/[config/][run-N/]grading.json` | `prepare_viewer.merge_grading` 合并生成 | viewer | L1/L2 + L3 合并后的最终评分，viewer 直接展示 |
| `results/<case>/[config/][run-N/]metrics.json` | executor 子 agent | `generate_benchmark.collect_runs` | 跑一次用了多少工具调用、产出了什么文件、有没有报错 |
| `results/<case>/[config/][run-N/]timing.json` | executor + orchestrator 事后补填 | `generate_benchmark.collect_runs` | 这次跑了多少 token、多长时间、工具调用次数 |
| `results/<case>/[config/][run-N/]run_status.json` | `prepare_viewer.setup_run_status` | viewer | 标记"这个 run 目录有数据"还是"空的" |
| `results/<case>/eval_metadata.json` | `prepare_viewer.write_eval_metadata` | viewer | 这个 case 的基本信息（prompt 是什么、eval_id 是什么、叫什么名字），viewer 用来显示标签 |
| `aggregate.json` | `aggregate_results.py` | viewer（可选） | 每个 case 的通过率 + L3 平均分 |
| `benchmark.json` | `generate_benchmark.py` | viewer | 整体指标汇总 + 每次 run 的概要 |
| `version-compare/pairs/<case>/run-<r>/{A,B}/` + `comparison.json` | vc-setup 复制匿名产物；comparator 子 agent 写判决 | vc-aggregate | 版本对版本盲评的匿名考场 + 单场判决 |
| `version-compare.json` | vc-aggregate（`iterate-run.js` VersionCompare 收尾） | orchestrator（Step 5 汇报）、下一轮 vc-setup（读指纹） | 本轮 vs 上一轮盲评汇总：每对胜负 + tally + verdict |
| `feedback.json` | 用户（viewer 文本框填写，Tier B 规划中）| `suggest.md` agent | 用户对这轮 iteration 的自由文字反馈 |

---

## 1. `eval-plan.json` — 用户手写，放在顶层

用 `utils.load_eval_plan(iter_dir)` 读取——这个函数会帮你把缺的字段填上默认值（见下方"Normalized by load_eval_plan"列）。

| 字段 | 类型 | 必填 | 默认值 | 会被 load_eval_plan 规范化？ |
|---|---|---|---|---|
| `iteration` | int | 是 | — | 否 |
| `status` | str | 否 | `"approved"` | 否 |
| `generated_from` | object | 否 | `{}` | 否 |
| `cases` | array | 是 | — | 每个 case 单独规范化 |

### `cases[]` 里每条 case 的字段

| 字段 | 类型 | 必填 | 默认值（load_eval_plan 补） | 谁读它 |
|---|---|---|---|---|
| `id` | str | 是 | — | 所有下游 |
| `name` | str | 否 | 用 `case["id"]` 填（prepare_viewer 补） | viewer |
| `prompt` | str | 是 | — | run_iteration（打印）、executor |
| `expected_output` | str | 否 | `""` | 仅参考，不作为评分依据 |
| `mode` | str | 否 | `"skill_production"` | run_iteration cmd_grade；prepare_viewer setup_outputs；generate_benchmark |
| `configurations` | array[str] | 否 | `["with_skill", "without_skill"]` | run_iteration cmd_setup + cmd_grade；prepare_viewer；generate_benchmark |
| `runs_per_configuration` | int | 否 | `1`（有旧字段 `runs` 时用 `runs` 兜底） | run_iteration、prepare_viewer、generate_benchmark、aggregate_results |
| `interaction_type` | str | 否 | 不填时 cmd_grade 从 `l1_l2_grader` 字段推断 | run_iteration cmd_grade → grade_l1_l2 `--interaction <type>` |
| `assertions` | array | 否 | `[]` | grader 子 agent、viewer |
| `files` | array | 否 | `[]` | executor（预置输入文件） |
| `eval_id` | int | 否 | 从 1 开始自动注入（load_eval_plan 做） | generate_benchmark、viewer |
| `l1_l2_grader` | str | 否（旧字段） | `""` | run_iteration cmd_grade 兜底推断用 |

### `cases[].assertions[]` 里每条断言的字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | 是 | 断言 ID，例如 `skill_md_exists` |
| `grader` | `"code"` 或 `"llm"` | 是 | `"code"` = 跑代码自动检查；`"llm"` = 交给 grader 子 agent 的 L3 评分 |
| `criteria` | str | 是 | 用文字描述"满足什么才算通过"，评分时展示给 grader |

### 合法值范围

- `mode`：`"skill_production"` | `"case_output"` | `"interaction"`
- `interaction_type`：`"reject"` | `"clarify"` | `"query"` | （规划中：`"ingest"` | `"lint"`）
- `configurations` 的每个值：`"with_skill"` | `"without_skill"`（其他字符串也行，会变成对应子目录名）

---

## 2. `eval_plan.json`（下划线版）

`prepare_viewer.convert_eval_plan` 写出的规范化副本。schema 和 `eval-plan.json` 完全一样，但所有字段都已补齐默认值。`generate_benchmark` 优先读这个版本（有了它就不再读带连字符的那个——这是 Cut I 的修复点）。

---

## 3. `grading-l1-l2.json` — `grade_l1_l2.py` 的输出

根据 mode 不同，这个文件有 4 种形状。

### 形状 A：`mode == "skill_production"`（检查 skill 产出的文件）

```json
{
  "mode": "skill_artifact",
  "skill_dir": "...",
  "l1": [{"id": "L1-1", "description": "...", "passed": true, "evidence": "..."}],
  "l2": [{"id": "L2-1", "description": "...", "passed": true, "evidence": "..."}],
  "summary": {
    "l1_pass": 3, "l1_total": 4,
    "l2_pass": 7, "l2_total": 9,
    "total_pass": 10, "total": 13
  }
}
```

L1 ID 范围：L1-1 到 L1-4。L2 ID 范围：L2-1 到 L2-9。这些检查项写死在 `grade_l1_l2.py` 的 `L1_CHECKS` / `L2_CHECKS` 里。

### 形状 B：`mode == "interaction"`（检查 AI 的回复内容）

```json
{
  "mode": "interaction",
  "case_type": "query",
  "l1": [...],
  "l2": [...],
  "summary": {...}
}
```

`case_type` 是 `reject` / `clarify` / `query` 三者之一。`l1` / `l2` 的具体内容因 case_type 而不同（见 `grade_l1_l2.grade_interaction`）。

### 形状 C：`mode == "case_output"`

这个 mode 下 `grade_l1_l2.py` 根本不跑。`grading-l1-l2.json` 文件不存在。`prepare_viewer.merge_grading` 知道这种情况，不会报 L1/L2 缺失的警告。

### 形状 D：`mode == "target_checks"`（用被测 skill 自己的 `evals/checks.sh`）

当被测 skill 提供了 `evals/checks.sh`，`run_iteration.py grade`（skill_production 模式）会跑这个脚本**代替**通用的 artifact grader，把脚本的 stdout 写成 `grading-l1-l2.json`。形状和形状 A 一样，但这里的检查项是 skill **自己在 `eval-criteria.md` 里声明的** `[code]` L1/L2 标准，而不是通用模板合规检查：

```json
{
  "mode": "target_checks",
  "skill_dir": "...",
  "l1": [{"id": "L1-1", "description": "script exists + executable", "passed": true, "evidence": "..."}],
  "l2": [{"id": "L2-1", "description": "exit 64 on <8-digit phone", "passed": true, "evidence": "..."}],
  "summary": {"l1_pass": 6, "l1_total": 6, "l2_pass": 8, "l2_total": 9, "total_pass": 14, "total": 15}
}
```

#### `evals/checks.sh` 的约定（这是 backlog#1 的修复方案）

通用 grader（`grade_l1_l2.py`）只知道 skill-creator 模板合规（YAML 格式、section 标题、内容长度）。它**不知道**某个 skill 在自己的 `eval-criteria.md` 里声明了什么自定义 L1/L2 标准（比如"脚本遇到小于 8 位的电话号码要 exit 64"、"通过 trap 把剪贴板恢复原状"）。所以每个有 `[code]` L1/L2 标准的 skill 自己提供 `evals/checks.sh`：

- **放哪里**：`<skill>/evals/checks.sh`
- **怎么调用**：`bash evals/checks.sh <skill_root_abs_path>` — grade 把被测 skill 的根目录绝对路径作为 `$1` 传进去
- **干什么**：跑这个 skill 在 `eval-criteria.md` 里自己声明的 `[code]` 检查（对 fixtures 跑脚本、grep 关键内容、断言 exit code）
- **输出**：把形状 D 的 JSON 打印到 stdout。空行（`\n\n`）之后的内容 grade 忽略（可以放人类可读的摘要）
- **exit code**：`0` = 全部通过，`1` = 有失败（两种都正常解析）。`>=2` = grade 当错误处理
- **只读 + 可回滚**：checks.sh **不能产生不可逆的副作用**（不发真实消息、不写 prod）。只跑安全的 dry-run 路径。`[llm]` 标准留给 L3 grader agent，checks.sh 只跑 `[code]` 的部分

---

## 4. `grading-l3.json` — grader 子 agent 的输出

grader 子 agent 按照 `agents/grader.md` 直接把结果写成这个文件。没有中间 markdown、没有额外 parse 步骤。格式：

```json
{
  "assertions": [
    {
      "id": "skill_md_exists",
      "criteria": "...",
      "score": 5,
      "passed": true,
      "evidence": "..."
    }
  ],
  "quality_review": {
    "functional_completeness": {"score": 5, "evidence": "..."},
    "correctness": {...},
    "craft": {...},
    "judgment": {...}
  },
  "eval_feedback": "<可选的文字说明>",
  "summary": {
    "passed": 7, "failed": 1, "total": 8,
    "pass_rate": 0.875, "mean_l3_score": 4.5
  }
}
```

---

## 5. `grading.json` — `prepare_viewer.merge_grading` 合并后的输出

把 L1/L2 和 L3 合并成 viewer 能直接展示的格式：

```json
{
  "expectations": [
    {"text": "L1-1: <evidence>", "passed": true, "evidence": "...", "level": 1},
    {"text": "L1-2: ...", "passed": true, "level": 1},
    {"text": "L2-1: ...", "passed": true, "level": 2},
    {"text": "<assertion_id>: <criteria>", "passed": true, "score": 5, "level": 3}
  ],
  "summary": {
    "passed": 14, "failed": 3, "total": 17, "pass_rate": 0.82,
    "by_level": {
      "1": {"passed": 3, "total": 4, "pass_rate": 0.75},
      "2": {"passed": 7, "total": 9, "pass_rate": 0.78},
      "3": {"passed": 4, "total": 4, "pass_rate": 1.0}
    }
  }
}
```

---

## 6. `comparison.json` — comparator 子 agent 的输出（单场盲评判决）

comparator（`agents/comparator.md`）对一对匿名输出 A/B 的判决。手动 A/B 对比时写在调用方指定的路径；version-compare 流程里固定写在 `version-compare/pairs/<case>/run-<r>/comparison.json`。

```json
{
  "winner": "A",
  "reasoning": "<为什么这边赢，引用可核对的证据>",
  "rubric": {
    "A": {
      "content": {"correctness": 5, "completeness": 5, "accuracy": 4},
      "structure": {"organization": 4, "formatting": 5, "usability": 4},
      "content_score": 4.7, "structure_score": 4.3, "overall_score": 9.0
    },
    "B": {"...": "同上结构"}
  }
}
```

`winner` 合法值：`"A"` | `"B"` | `"TIE"`（TIE 应当罕见）。rubric 内部维度可按任务调整，但 `winner` / `reasoning` / `rubric` 三个顶层字段必须在。

---

## 7. `metrics.json` — executor 子 agent 写（按 executor.md 规范）

```json
{
  "tool_calls": {"Read": 5, "Write": 2, "Bash": 8, ...},
  "total_tool_calls": 18,
  "total_steps": 6,
  "files_created": ["output.pdf"],
  "errors_encountered": 0,
  "duration_seconds": 156,
  "output_chars": 0,
  "transcript_chars": 0
}
```

`total_tool_calls` 是 benchmark 表格里"Tool Calls"那列的主要来源；`duration_seconds` 是"time_seconds"的备用来源。

---

## 8. `timing.json` — executor 写 + orchestrator 事后补填

```json
{
  "executor_duration_seconds": 156,
  "total_duration_seconds": 156,
  "total_tokens": 51064,
  "tool_uses": 13
}
```

`total_tokens` 是 benchmark 表格里"Tokens"那列的主要来源（`generate_benchmark.collect_runs` 的 `first_nonnull` 兜底链：metrics.tokens → metrics.total_tokens → timing.total_tokens）。

orchestrator 在 Agent tool 返回后手动补填 `total_tokens` + `tool_uses`（目前是手动步骤，v0.6 backlog 里有自动化计划）。

---

## 9. `benchmark.json` — `generate_benchmark.py` 的顶层输出

```json
{
  "metadata": {
    "skill_name": "<from --skill-name arg>",
    "skill_path": "SKILL.md",
    "executor_model": "claude-opus-4-20250514",
    "analyzer_model": "claude-opus-4-20250514",
    "timestamp": "2026-04-23T01:53:00",
    "iteration": 4,
    "evals_run": [1, 2],
    "runs_per_configuration": 1
  },
  "runs": [
    {
      "eval_id": 1, "eval_name": "happy_fixture",
      "configuration": "with_skill", "run_number": 1,
      "run_status": "completed",
      "result": {
        "pass_rate": 1.0, "passed": 8, "failed": 0, "total": 8,
        "l3_mean_score": 5.0,
        "time_seconds": 229, "tokens": 66500, "tool_calls": 16
      },
      "by_level": {...},
      "expectations": [...]
    }
  ],
  "run_summary": {
    "with_skill": {
      "pass_rate": {"mean": ..., "stddev": ..., "min": ..., "max": ...},
      "l3_mean_score": {...},
      "time_seconds": {...},
      "tokens": {...},
      "tool_calls": {...},
      "by_level": {...},
      "runs": <n>
    },
    "without_skill": {...}
  },
  "notes": ["..."]
}
```

---

## 10. `aggregate.json` — `aggregate_results.py` 的输出

```json
{
  "iteration": 4,
  "cases": [
    {
      "case_id": "happy_fixture",
      "runs_expected": 1,
      "runs_found": 1,
      "l1_l2": [],
      "l3_scores": {"<assertion_id>": [5, ...]},
      "l3_pass_rates": {"<assertion_id>": [1, ...]},
      "by_configuration": {"with_skill": {...}, "without_skill": {...}},
      "l3_summary": {
        "<assertion_id>": {"mean_score": 5.0, "stddev_score": 0.0, "pass_rate": 1.0, "scores": [5], "stable": true}
      }
    }
  ],
  "overall": {
    "mean_l3_score": 5.0,
    "unstable_assertions": [],
    "total_cases": 2,
    "cases_with_data": 2
  }
}
```

用来做多次 run 之间的稳定性分析（stddev 检测）。`stable` 的判定阈值：stddev_score < 0.5。

---

## 11. `feedback.json` — 用户写（viewer 文本框填写，Tier B 规划中）

```json
{
  "iteration": 4,
  "feedback": "free-form user prose",
  "per_case": {
    "<case_id>": "<case-specific feedback>"
  },
  "timestamp": "..."
}
```

viewer 目前还没实现写入（Tier B 才加）。`suggest.md` agent 将来应该读这个文件来产建议，而不是自己编。

---

## 12. `run_status.json` — 每次 run 的状态标记

```json
{"status": "completed"}
```

viewer 用它来判断"这个 run 目录里有数据"还是"空目录"。

---

## 13. `eval_metadata.json` — 每个 case 的 viewer 查询信息

```json
{
  "eval_id": 1,
  "name": "happy_fixture",
  "prompt": "...",
  "assertions": [],
  "runs_per_configuration": 1
}
```

`prepare_viewer.write_eval_metadata` 写出来。放在 `results/<case>/` 目录下（也可选放 `results/<case>/<config>/` 下）。viewer 用 `eval_id` / `name` / `prompt` 显示界面标签。

---

## 14. `version-compare.json` — version-compare 收尾（vc-aggregate）的输出

本轮（iteration N）vs 上一轮（N-1）with_skill 产物的头对头盲评汇总，放 iteration 顶层。
只在 iteration ≥ 2、上一轮目录存在、且 case 的 prompt 与上一轮完全一致时生成；prompt 改过的 case 进 `skipped_cases`。

WHY：L3 绝对分有打分噪音，两轮差 0.1 分分不清是真进步还是波动；匿名头对头对小幅进退更灵敏，也能抓到"总分涨了但个别 case 退步"。

```json
{
  "iteration": 10,
  "compared_to": 9,
  "skill_fingerprint": {
    "current": "<md5：skillPath 的 SKILL.md + references/**/*.md 内容拼接>",
    "previous": "<上一轮 version-compare.json 的 current；没有则 null>"
  },
  "comparable_cases": ["drawn_coffee_screenshot"],
  "skipped_cases": [{"id": "<case>", "reason": "prompt changed | 上一轮无产出"}],
  "pairs": [
    {
      "case": "drawn_coffee_screenshot", "run": 1,
      "dir": "pairs/drawn_coffee_screenshot/run-1",
      "seat_A": "previous",
      "winner_version": "current"
    }
  ],
  "tally": {"current_wins": 2, "previous_wins": 1, "ties": 0},
  "verdict": "current",
  "notes": ["seat_A 按 run 序号奇偶交替，平衡座位偏差"]
}
```

- `seat_A`：这一对里 A 座给了哪个版本（`previous` | `current`）。座位按 run 序号奇偶交替，平衡"裁判偏心某个座位"的风险。
- `winner_version`：把 comparator 的 `winner`（A/B/TIE）翻译回版本（`current` | `previous` | `tie`；判决文件缺失记 `missing`）。
- `verdict`：`current`（本轮赢多）| `previous`（上一轮赢多）| `tie`（全平）| `mixed`（打平且有分歧）。
- `skill_fingerprint`：记录"这轮盲评时 skill 长什么样"，让 iteration ↔ 版本的对应有账可查；下一轮 vc-setup 读 `current` 当自己的 `previous`。
- 谁写：`iterate-run.js` VersionCompare 阶段的 vc-aggregate 节点。谁读：orchestrator（Step 5 向用户汇报）、下一轮 vc-setup。

---

## 附录——"改一处，全生效"的承诺

**要给 eval-plan.json 加新字段**：在 `utils.normalize_eval_plan()` 里加默认值，然后在这里更新文档。脚本在规范化之后直接读 `case["new_field"]`。

**要加新的 case mode**：在 `utils.normalize_eval_plan()` 里让它能接受这个值；更新 `run_iteration.cmd_grade` 的分发逻辑；更新 `prepare_viewer.setup_outputs` 的输出处理；在上方第 1 节"合法值范围"里补充。

**要加新的 interaction_type**：在 `grade_l1_l2.grade_interaction` 里加分支；在这里更新合法值；如果 L3 的评分 rubric 因此改变，一并更新 `agents/grader.md`。

如果你发现自己在 3 个以上的脚本里改同一个字段——停下来，先在这里 + utils.py 里统一，再改脚本。

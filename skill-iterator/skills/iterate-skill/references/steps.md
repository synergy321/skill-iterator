# Iterate Steps

这是这 5 步的执行细节（含 Step 5.5 人工把关）。整体结构：按顺序跑（Sequential），Step 1 内部根据起点分三条路（Conditional），Step 5 跑完可以回到 Step 1 再来一轮（Iterative Loop）。

---

### Step 1: 判断起点

Input: 用户请求 + skill 目录（含 evals/）
Output: 本次 eval 方向 + eval plan

**WHY（这步为什么存在）**：这一步是为了搞清楚「这次从哪里开始跑」，避免重复工作——有上次的改进建议就接着做，有历史数据就分析趋势，全新的就从评分标准开始建计划。如果中途断了，从这步读已有文件就能恢复断点。

先检查 evals/workspace/ 目录：

IF 上一轮 iteration 有 suggestions.json →
  读取建议，确认本次优化方向
  基于建议更新 eval plan
ELSE IF 有历史 eval 数据但没有明确建议 →
  读取上次 eval 数据（evals/workspace/iteration-N/）
  分析结果，建议本次 eval 方向
  生成新的 eval plan
ELSE（全新，从未 eval 过）→
  读取 evals/eval-criteria.md（skill-creator Step 3 产出的 L1/L2/L3 定义）
  生成首次 eval plan

eval plan 写入 evals/workspace/iteration-N/eval-plan.json

**错误处理：**
IF eval-criteria.md 不存在 → 停止，引导用户用 skill-creator Step 3 生成
IF eval-criteria.md 内容无法解析（格式损坏）→ 报告具体错误，让用户修复后重试

### Step 2: 用户确认 plan

Input: eval plan
Output: 用户确认的 plan

**WHY（这步为什么存在）**：这一步是为了让用户亲眼确认「这次打算测什么、怎么测」——eval plan 定了才开跑，不然跑完发现方向错了，那些 token 白费了。

将 eval plan 输出给用户查看。用户确认后继续。

### Step 3: 跑执行链（Workflow）

Input: 用户确认的 eval plan
Output: results/ 原始数据 + L1/L2 + L3 grading + suggestions + benchmark + review.html

**WHY（这步为什么存在）**：这一步是为了让整条「执行 → 打分 → 给建议」的流水线一次性自动跑完，不靠人在中间手动串。
为什么不手动一步步 spawn：手动串要靠 orchestrator 记得每一棒、记得补 token，早晚会漏——把「记得做」变成系统属性（写进 workflow）才可靠。

orchestrator 读 eval-plan.json 拿 cases（id + prompt），然后调 Workflow 工具：

```
Workflow({
  scriptPath: "<pluginDir>/scripts/workflows/iterate-run.js",
  args: { skillPath, skillName, iteration, pluginDir, cases: [{id, prompt}, ...] }
})
```

（pluginDir = 本 plugin 根目录；skillPath = 目标 skill 目录）

workflow 内部确定性 DAG：

```
setup → executor 双跑(with_skill / without_skill, fan-out) → grade(L1/L2)
      → grader 双跑(L3, fan-out) → suggest → finalize(aggregate + benchmark + review.html)
```

**这个 workflow 设计上保证 100% 可跑通、中途断了可重入**（详见脚本头部注释）：
- 不强制 agent 按格式回传：agent 把结果落文件即可，不会因没按格式返回而整盘崩
- 每步幂等：产物已经存在就跳过，失败重跑能接着上次的继续
- 单点失败隔离：某个 executor/grader 挂了直接 filter 掉，不连累整条流水线
- token 诚实记录：executor 写 `total_tokens=null`（不写 0 假装成功）；`capture_timing.py` 已能解析 harness 的 `subagent_tokens`

**错误处理：**
IF workflow 某节点失败 → 该节点结果为 null，其余照常；重跑 workflow（幂等跳过已完成的）即可续跑
IF 整个 workflow 失败 → 看返回的 transcript dir 定位，修后用 `resumeFromRunId` 续跑
IF 环境不支持 Workflow → fallback 手动按 `agents/executor.md` → grade → `agents/grader.md` → `agents/suggest.md` → finalize 顺序串（旧流程，仅应急）

**只读不改（铁律）**：workflow 所有产物落 evals/workspace/iteration-N/，executor/grader/suggest 绝不改 target skill 文件。

### Step 4: 评分产物 + 可选叠加

Input: Step 3 workflow 的产物
Output: 确认产物齐全 + 可选叠加测试

**WHY（这步为什么存在）**：这一步是为了补 workflow 覆盖不到的两块空白：（1）eval-criteria.md 里写了「要跑代码检查」但 target 还没有 checks.sh，这些标准会被整层跳过；（2）如果 target skill 产出的是文档/模板，L1/L2/L3 只测「格式对不对」，不测「产出能不能真正用」——这两块得在这步单独补。

Step 3 的 workflow 已产出：grading-l1-l2（case_output 模式下为 SKIP）、grading-l3.json、
suggestions.json、benchmark.json。本步是可选补充：

1. **缺 checks.sh 时的提示**：
   IF eval-criteria.md 有 [code] L1/L2 标准但 target 无 evals/checks.sh
   → case_output 模式下打分会整层跳过这些 [code] 标准，把它们压给 L3 grader 靠肉眼评（高方差——同一题跑三次可能一次满分一次不及格）。
   提示 Travis 授权，据 [code] 条目生成 evals/checks.sh（一次性 eval setup，放 target/evals/；
   建测试脚本 ≠ 改功能代码，不违反只读原则；[llm] 类型的条目不进 checks.sh，留给 L3 grader 评）。
   ⚠️ workflow 只解决「串联靠人」的问题，不解决 L1/L2 对产文件型 skill 评不到的问题——这条要单独做。

2. （可选）A/B 对比：如果有两个版本的 skill（改前 vs 改后），spawn `../../agents/comparator.md` 做 blind 对比。

3. **如果目标 skill 是产出文件型** → 推荐叠加跑 [blind-test skill](../../blind-test/SKILL.md)。
   L1/L2/L3 测 "产出符不符合规范"，blind test 测 "产出能不能 work"——后者是产出文件型 skill 的 reason for being。

**错误处理：**
IF target 已有 evals/checks.sh 但跑挂（退出码 >=2）→ 看 checks.sh 报错，修后重跑 `../../scripts/run_iteration.py grade`；不阻塞本轮其他产物。
IF blind-test 叠加跑失败 → 见 blind-test skill 的 Troubleshooting，不影响本轮 L1/L2/L3 结论。

### Step 5: 打开 Eval Viewer 给用户看成绩（必做）

Input: 分层结果 + 建议
Output: 用户决定继续 / 停止

这一步不可跳过。
**WHY（这步为什么存在）**：光看数字很容易被误导——分数一样，但产出方向完全错的情况很常见。用户必须亲眼看到每道题的实际产出才能做判断，只报通过率不够。

1. workflow 的 finalize 节点已生成 evals/workspace/iteration-N/review.html（已含 prepare_viewer + generate_review）。
   IF 需交互式 viewer（feedback POST / 自动 reload）→ 跑 `python3 ../../scripts/run_iteration.py view evals/workspace/iteration-N/`
2. 打开 review.html（静态）或上面的交互式 viewer。
3. 浏览器看到：
   - L1/L2/L3 分层通过率
   - 每个 case 的 SKILL.md 产出
   - 最弱层级的根本原因分析
   - 下次改进建议（Suggestions tab）
4. 用户决定：
   IF 继续优化 → 进 Step 5.5 → 改进 skill → 回到 Step 1
   IF 满意 → 退出 Iterate

**错误处理：**
IF prepare_viewer.py 报错 → 用 `generate_review.py --static` 生成独立 HTML 直接打开
IF viewer 数据不完整（缺 grading.json）→ 跑 `prepare_viewer.py --minimal <iter_dir>` —— minimal mode 跳过 grading 合并，只把已有产出推进 viewer。**「用户能看到产出」比「metrics 完美」更重要**——grading pipeline 没跑完别让 viewer 也一起挂掉。

### Step 5.5: Review + Apply（人工把关）

Input: suggestions.json
Output: Travis 决定哪些建议要 apply / 跳过

**WHY（这步为什么存在）**：这一步是为了确保 target skill 的所有改动都经过 Travis 人工审核——本 skill 只负责评、不负责改，强制过人工这一关，跟 skill-creator 的架构设计保持一致（评卷的人不能直接改试卷）。

Travis 看 suggestions.json，对每条建议决定：
- **apply**：让 AI "按 suggestion #X 改 target skill"。AI 读 skill-creator 的修改指引，用 Edit/Write 改 target → PostToolUse hook 自动跑 quick_validate → 违规则 AI 自己修 → 直到 lint 通过
- **跳过**：在 suggestions.json 标记 status: skipped（Travis 决定，不影响其他 patch）

**绝不允许**：本 skill 直接 Edit/Write target 文件 / 自动调脚本改 target。所有改动必须 Travis 主动让 AI 走 skill-creator 流程。

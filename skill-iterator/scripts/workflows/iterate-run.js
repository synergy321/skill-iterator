// iterate-run.js — skill-iterator 内容迭代执行链（确定性 DAG）
//
// 由 iterate-skill 的 steps.md Step 3 调用。orchestrator 读完 eval-plan.json 后：
//   Workflow({
//     scriptPath: "<pluginDir>/scripts/workflows/iterate-run.js",
//     args: { skillPath, skillName, iteration, pluginDir, cases: [{id, prompt}, ...] }
//   })
//
// 设计目标 = 100% 可跑通 / 可重入（guardrail，不靠 orchestrator 记得）：
//   - 无强制 schema：agent 干活落文件即可，不会因没按格式返回而整盘崩
//   - 每步幂等：产物已在就跳过，失败重跑能接着跑
//   - 单点失败隔离：parallel 里某节点 throw → null，filter 掉，不连累整条
//   - token 诚实：executor 写 total_tokens=null（不写 0 假装）
//   - 防 drift：executor/grader/suggest 去读 agents/*.md 当权威指令，不内联复制
//   - read-only on target：所有产物落 evals/workspace/，绝不改 target skill 文件

export const meta = {
  name: 'iterate-run',
  description: 'skill-iterator 内容迭代执行链：setup → executor双跑(fan-out) → grade → grader双跑(fan-out) → suggest → finalize。read-only、栏杆全留、无schema、幂等可重入、单点隔离。',
  phases: [
    { title: 'Setup' },
    { title: 'Execute' },
    { title: 'GradeL1L2' },
    { title: 'GradeL3' },
    { title: 'Suggest' },
    { title: 'Finalize' },
  ],
}

// args 可能以 object 或 JSON 字符串两种形式到达（取决于 harness/调用方），两者都兼容
let _a = args
if (typeof _a === 'string') {
  try { _a = JSON.parse(_a) } catch (e) { throw new Error('iterate-run: args 是字符串但非合法 JSON — ' + e.message) }
}
const { skillPath, skillName, iteration, pluginDir, cases, runs, execModel } = _a || {}
if (!skillPath || !iteration || !pluginDir || !Array.isArray(cases) || cases.length === 0) {
  throw new Error('iterate-run 需要 args: { skillPath, skillName, iteration, pluginDir, cases:[{id,prompt}], runs? }（收到 args 类型=' + typeof args + '）')
}

const ITER = `${skillPath}/evals/workspace/iteration-${iteration}`
const SCRIPTS = `${pluginDir}/scripts`
const AGENTS = `${pluginDir}/agents`
const CRITERIA = `${skillPath}/evals/eval-criteria.md`
const CONFIGS = ['with_skill', 'without_skill']
const RUNS = Number.isInteger(runs) && runs > 0 ? runs : 1   // 每配置跑几次；>1 时下游 aggregate/benchmark 取中位数平掉单跑噪声
const A = 'general-purpose'
const M = 'sonnet'
const EXEC_M = (typeof execModel === 'string' && execModel) ? execModel : M   // executor 可单独换 model（效率 A/B）；grader 始终留 M(sonnet) 保证判分一致

// run 目录命名严格对齐 run_iteration.py 的 find_run_dirs：runs>1 且多 config → <case>/<cfg>/run-N
const runDir = (caseId, cfg, runIdx) => {
  if (RUNS > 1 && CONFIGS.length > 1) return `${ITER}/results/${caseId}/${cfg}/run-${runIdx}`
  if (RUNS > 1) return `${ITER}/results/${caseId}/run-${runIdx}`
  if (CONFIGS.length > 1) return `${ITER}/results/${caseId}/${cfg}`
  return `${ITER}/results/${caseId}`
}

function executorPrompt(c, cfg, runIdx) {
  const out = runDir(c.id, cfg, runIdx)
  return `你是 skill-iterator Executor 节点。先读 ${AGENTS}/executor.md 并严格按它执行（它是权威指令，不要凭记忆）。
参数：
- skill_path: ${skillPath}
- prompt: ${c.prompt}
- output_dir: ${out}
- configuration: ${cfg}
强调约束：
- 【幂等】先看 ${out}/ 里主产物（如 DESIGN.md / 对应产物）是否已存在且非空。是 → 不重新生成，读一眼确认完整，纯文字汇报 "reused existing"。否 → 走完整流程。
- 【read-only 铁律】只在 output_dir 内写，绝不修改 ${skillPath} 下任何 skill 文件。
- timing.json 的 total_tokens 写 null（harness 不透传 token，诚实写 null，绝不写 0 假装成功）。
完事纯文字简短汇报，不需要任何固定格式。`
}

function graderPrompt(c, cfg, runIdx) {
  const out = runDir(c.id, cfg, runIdx)
  return `你是 skill-iterator Grader 节点。先读 ${AGENTS}/grader.md 并严格按它执行。
参数：
- run_dir / outputs_dir: ${out}
- transcript: ${out}/transcript.md ; 产物在 ${out}/
- assertions: ${ITER}/eval-plan.json 里 id="${c.id}" 那个 case 的 assertions（id/criteria 逐字复制）
- eval_criteria_path (L3 rubric): ${CRITERIA}
强调约束：
- 【幂等】${out}/grading-l3.json 已存在且合法 → 读一眼直接汇报，不重评。
- 【read-only】只写 ${out}/grading-l3.json。
- 本 run 若无 grading-l1-l2.json（case_output 跳过代码评分），本该代码评的断言你也去真实产物核对；lint 无法独立重跑就看 transcript 自述并注明"未独立复核"。
完事纯文字汇报：config、pass_rate、mean_l3。`
}

const suggestPrompt = `你是 skill-iterator Suggest 节点。先读 ${AGENTS}/suggest.md 并严格按它执行。
参数：
- skill_path: ${skillPath}
- eval_criteria_path: ${CRITERIA}
- results_dir: ${ITER}/results
- previous_suggestions_path: ${skillPath}/evals/workspace/iteration-${iteration - 1}/suggestions.json（不存在则按 null 处理）
- iteration_number: ${iteration}
强调约束：
- 【硬约束 read-only】绝不修改 ${skillPath} 任何文件，只产 ${ITER}/suggestions.json。
- 【幂等】${ITER}/suggestions.json 已存在 → 读一眼直接汇报。
完事纯文字汇报：本轮一句话结论、最高优先级、do_not_change 数。`

// ---------- 确定性 DAG ----------
phase('Setup')
await agent(
  `跑 setup：python3 ${SCRIPTS}/run_iteration.py setup ${ITER}（读 eval-plan.json 建 results/ 目录）。纯文字汇报建了哪些 run 目录、有无报错。`,
  { label: 'setup', phase: 'Setup', agentType: A, model: M }
)
log('setup done')

phase('Execute')
const execTasks = []
for (const c of cases) for (const cfg of CONFIGS) for (let r = 1; r <= RUNS; r++) {
  const lbl = RUNS > 1 ? `exec:${c.id}:${cfg}:run${r}` : `exec:${c.id}:${cfg}`
  execTasks.push(() => agent(executorPrompt(c, cfg, r), { label: lbl, phase: 'Execute', agentType: A, model: EXEC_M }))
}
const execs = await parallel(execTasks)
log(`executors: ${execs.filter(Boolean).length}/${execTasks.length} ok (runs/config=${RUNS})`)

phase('GradeL1L2')
await agent(
  `跑 grade：python3 ${SCRIPTS}/run_iteration.py grade ${ITER}。case_output 模式会整层 SKIP（已知 design 行为），如实纯文字汇报每个 run 是 SKIP 还是评了。`,
  { label: 'grade-l1l2', phase: 'GradeL1L2', agentType: A, model: M }
)
log('grade L1/L2 done')

phase('GradeL3')
const gradeTasks = []
for (const c of cases) for (const cfg of CONFIGS) for (let r = 1; r <= RUNS; r++) {
  const lbl = RUNS > 1 ? `grade-l3:${c.id}:${cfg}:run${r}` : `grade-l3:${c.id}:${cfg}`
  gradeTasks.push(() => agent(graderPrompt(c, cfg, r), { label: lbl, phase: 'GradeL3', agentType: A, model: M }))
}
const grades = await parallel(gradeTasks)
log(`graders: ${grades.filter(Boolean).length}/${gradeTasks.length} ok`)

phase('Suggest')
const suggest = await agent(suggestPrompt, { label: 'suggest', phase: 'Suggest', agentType: A, model: M })

phase('Finalize')
const final = await agent(
  `跑 finalize：python3 ${SCRIPTS}/run_iteration.py finalize ${ITER} --skill-name "${skillName || 'Skill'}"（aggregate + benchmark + review.html）。纯文字汇报 benchmark 各 configuration 的 pass_rate / mean_l3 / delta + review.html 路径 + 有无报错。`,
  { label: 'finalize', phase: 'Finalize', agentType: A, model: M }
)

return {
  executors_ok: execs.filter(Boolean).length,
  executors_total: execTasks.length,
  graders_ok: grades.filter(Boolean).length,
  graders_total: gradeTasks.length,
  suggest: typeof suggest === 'string' ? suggest.slice(0, 300) : 'n/a',
  finalize: typeof final === 'string' ? final.slice(0, 500) : 'n/a',
}

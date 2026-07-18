// iterate-run.js — skill-iterator 内容迭代执行链（确定性 DAG）
//
// 由 iterate-skill 的 steps.md Step 3 调用。orchestrator 读完 eval-plan.json 后：
//   Workflow({
//     scriptPath: "<pluginDir>/scripts/workflows/iterate-run.js",
//     args: { skillPath, skillName, iteration, pluginDir, cases: [{id, prompt}, ...],
//             skipBaselineExec?: true }   // P1：prompt 与上一轮完全一致时复用 baseline，不重跑 without_skill
//   })
//
// 设计目标 = 100% 可跑通 / 可重入（guardrail，不靠 orchestrator 记得）：
//   - 无强制 schema：agent 干活落文件即可，不会因没按格式返回而整盘崩
//     （例外：VersionCompare 的 setup/aggregate 节点用 schema 回传 pair 清单来驱动 fan-out；
//      它们失败 → null → 整段 version-compare 跳过，不连累主链）
//   - 每步幂等：产物已在就跳过，失败重跑能接着跑
//   - 单点失败隔离：parallel 里某节点 throw → null，filter 掉，不连累整条
//   - token 诚实：executor 写 total_tokens=null（不写 0 假装）
//   - 防 drift：executor/grader/suggest 去读 agents/*.md 当权威指令，不内联复制
//   - read-only on target：所有产物落 evals/workspace/，绝不改 target skill 文件

export const meta = {
  name: 'iterate-run',
  description: 'skill-iterator 内容迭代执行链：setup → executor双跑(fan-out) → grade → grader双跑(fan-out) → suggest → version-compare(vs 上一轮盲评，可关) → finalize。read-only、栏杆全留、幂等可重入、单点隔离。',
  phases: [
    { title: 'Setup' },
    { title: 'Execute' },
    { title: 'GradeL1L2' },
    { title: 'GradeL3' },
    { title: 'Suggest' },
    { title: 'VersionCompare' },
    { title: 'Finalize' },
  ],
}

// args 可能以 object 或 JSON 字符串两种形式到达（取决于 harness/调用方），两者都兼容
let _a = args
if (typeof _a === 'string') {
  try { _a = JSON.parse(_a) } catch (e) { throw new Error('iterate-run: args 是字符串但非合法 JSON — ' + e.message) }
}
const { skillPath, skillName, iteration, pluginDir, cases, runs, execModel, versionCompare, skipBaselineExec } = _a || {}
if (!skillPath || !iteration || !pluginDir || !Array.isArray(cases) || cases.length === 0) {
  throw new Error('iterate-run 需要 args: { skillPath, skillName, iteration, pluginDir, cases:[{id,prompt}], runs? }（收到 args 类型=' + typeof args + '）')
}
// P1 baseline 复用：without_skill 跟 skill 版本无关，prompt 没变就不该重测（测量精度花在变量上，不花在常量上）。
// 只有 orchestrator 核对过「本轮 case prompt 与上一轮逐字一致 + 上一轮 without_skill 产物存在」才允许传 true。
const SKIP_BASELINE = skipBaselineExec === true
if (SKIP_BASELINE && !(Number(iteration) > 1)) {
  throw new Error('skipBaselineExec 需要 iteration > 1（第一轮没有可复用的 baseline）')
}

const ITER = `${skillPath}/evals/workspace/iteration-${iteration}`
const SCRIPTS = `${pluginDir}/scripts`
const AGENTS = `${pluginDir}/agents`
const CRITERIA = `${skillPath}/evals/eval-criteria.md`
const CONFIGS = ['with_skill', 'without_skill']
// SKIP_BASELINE 时执行/评分链只跑 with_skill；CONFIGS 本身不动——目录布局、下游 aggregate/benchmark 仍按双配置读
// （baseline 的 results+grading 由 Setup 节点从上一轮拷贝进来，产物集合和全量跑完全一致）
const EXEC_CONFIGS = SKIP_BASELINE ? ['with_skill'] : CONFIGS
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

// ---------- VersionCompare（本轮 vs 上一轮 with_skill 产物盲评）----------
// WHY：L3 绝对分有打分噪音，两轮差 0.1 分分不清真假进退；匿名头对头更灵敏，
// 还能抓"总分涨了但个别 case 退步"。schema 见 references/schemas.md §6/§14。
const PREV_ITER = `${skillPath}/evals/workspace/iteration-${iteration - 1}`

const VC_SETUP_SCHEMA = {
  type: 'object', required: ['comparable', 'pairs'],
  properties: {
    comparable: { type: 'boolean' },
    reason: { type: 'string' },
    pairs: { type: 'array', items: { type: 'object', required: ['case', 'run', 'dir'], properties: { case: { type: 'string' }, run: { type: 'integer' }, dir: { type: 'string' } } } },
  },
}
const VC_AGG_SCHEMA = {
  type: 'object', required: ['tally', 'verdict'],
  properties: { tally: { type: 'object' }, verdict: { type: 'string' }, pairs_judged: { type: 'integer' } },
}

const vcSetupPrompt = `你是 skill-iterator VersionCompare 布场节点。用 Bash 把下面的 python 脚本【原样】以 heredoc 执行（python3 <<'PYEOF' … PYEOF），一行都不要增删改；然后把脚本打印的最后一行 JSON 作为结构化结果返回。
脚本幂等（manifest 已存在会直接复用）；它只写 ${ITER}/version-compare/，绝不改 ${skillPath} 下任何 skill 文件。

import json, hashlib, shutil, sys
from pathlib import Path

SKILL = Path("${skillPath}")
CUR = Path("${ITER}")
PREV = Path("${PREV_ITER}")
VC = CUR / "version-compare"
META = {"grading-l1-l2.json","grading-l3.json","grading.json","metrics.json","timing.json","timing_start.txt","run_status.json","user_notes.md","eval_metadata.json","transcript.md","comparison.json"}

def out(o):
    print(json.dumps(o, ensure_ascii=False)); sys.exit(0)

def fingerprint():
    files = [SKILL / "SKILL.md"]
    refs = SKILL / "references"
    if refs.exists():
        files += sorted(refs.rglob("*.md"))
    h = hashlib.md5()
    for f in files:
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()

mp = VC / "manifest.json"
if mp.exists():
    m = json.loads(mp.read_text())
    ps = [{"case": p["case"], "run": p["run"], "dir": p["dir"]} for p in m.get("pairs", [])]
    out({"comparable": len(ps) > 0, "reason": "reused existing manifest", "pairs": ps})
if not PREV.exists():
    out({"comparable": False, "reason": "no previous iteration dir", "pairs": []})

def load_cases(d):
    return {c["id"]: c for c in json.loads((d / "eval-plan.json").read_text()).get("cases", [])}

def run_dirs(d, cid):
    base = d / "results" / cid
    ws = base / "with_skill"
    if ws.exists():
        rs = sorted(ws.glob("run-*"), key=lambda p: int(p.name.split("-")[1]))
        return rs if rs else [ws]
    rs = sorted(base.glob("run-*"), key=lambda p: int(p.name.split("-")[1]))
    if rs:
        return rs
    return [base] if base.exists() else []

def artifacts(rd):
    return [f for f in rd.iterdir() if f.name not in META and not f.name.startswith(".")]

cur_cases, prev_cases = load_cases(CUR), load_cases(PREV)
prev_fp = None
pvc = PREV / "version-compare.json"
if pvc.exists():
    prev_fp = json.loads(pvc.read_text()).get("skill_fingerprint", {}).get("current")

pairs, skipped = [], []
for cid, c in cur_cases.items():
    pc = prev_cases.get(cid)
    if not pc:
        skipped.append({"id": cid, "reason": "上一轮无此 case"}); continue
    if (pc.get("prompt") or "") != (c.get("prompt") or ""):
        skipped.append({"id": cid, "reason": "prompt changed"}); continue
    cr, pr = run_dirs(CUR, cid), run_dirs(PREV, cid)
    n = min(len(cr), len(pr))
    if n == 0:
        skipped.append({"id": cid, "reason": "上一轮或本轮无 with_skill 产出"}); continue
    cdir = VC / "pairs" / cid
    gt = c.get("source_ground_truth")
    if gt:
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "truth.md").write_text("# ground truth（eval-plan 携带的已知真值）\\n\\n" + json.dumps(gt, ensure_ascii=False, indent=2))
    for i in range(n):
        r = i + 1
        seat_a_prev = (r % 2 == 1)
        a_src = pr[i] if seat_a_prev else cr[i]
        b_src = cr[i] if seat_a_prev else pr[i]
        pd = cdir / f"run-{r}"
        ok = True
        for label, src in (("A", a_src), ("B", b_src)):
            arts = artifacts(src)
            if not arts:
                ok = False; break
            dst = pd / label
            if dst.exists():
                shutil.rmtree(dst)
            dst.mkdir(parents=True, exist_ok=True)
            for f in arts:
                if f.is_dir():
                    shutil.copytree(f, dst / f.name)
                else:
                    shutil.copy2(f, dst / f.name)
        if not ok:
            skipped.append({"id": cid, "reason": f"run-{r} 无可比产物"}); continue
        pairs.append({"case": cid, "run": r, "dir": f"pairs/{cid}/run-{r}", "seat_A": "previous" if seat_a_prev else "current"})

VC.mkdir(parents=True, exist_ok=True)
mp.write_text(json.dumps({"iteration": ${iteration}, "compared_to": ${iteration - 1}, "skill_fingerprint": {"current": fingerprint(), "previous": prev_fp}, "pairs": pairs, "skipped_cases": skipped}, ensure_ascii=False, indent=1))
out({"comparable": len(pairs) > 0, "reason": "" if pairs else "no comparable pairs", "pairs": [{"case": p["case"], "run": p["run"], "dir": p["dir"]} for p in pairs]})`

function vcJudgePrompt(p) {
  const pd = `${ITER}/version-compare/${p.dir}`
  return `你是 skill-iterator Blind Comparator 节点。先读 ${AGENTS}/comparator.md 并严格按它执行（它是权威指令，不要凭记忆）。
参数：
- output_a_path: ${pd}/A/
- output_b_path: ${pd}/B/
- eval_prompt: 读 ${ITER}/eval-plan.json 里 id="${p.case}" 那个 case 的 prompt。prompt 里引用的输入文件如已不存在，注明"输入文件缺失"，改按产出内容与任务要求判。
- 参考真值（如存在）: ${ITER}/version-compare/pairs/${p.case}/truth.md —— 用它客观核对谁更接近事实；语义命中即可，估计值不必逐字相同。
- 结果写到: ${pd}/comparison.json（格式见 ${pluginDir}/references/schemas.md §6：winner/reasoning/rubric）
强调约束：
- 【盲评铁律】只读本 pair 目录（A/ B/ truth.md）和 eval-plan.json 里该 case，绝不读 results/ 或其他 iteration/pair 目录，不猜测两份产物的来历。
- 【幂等】${pd}/comparison.json 已存在且合法 → 读一眼直接汇报，不重判。
- 【果断】TIE 应当罕见。
完事纯文字汇报：winner + 一句最硬的证据。`
}

const vcAggPrompt = `你是 skill-iterator VersionCompare 汇总节点。用 Bash 把下面的 python 脚本【原样】以 heredoc 执行（python3 <<'PYEOF' … PYEOF），一行都不要增删改；然后把脚本打印的最后一行 JSON 作为结构化结果返回。
它只读 version-compare/ 下的判决、只写 ${ITER}/version-compare.json（纯汇总，重跑安全）。

import json
from pathlib import Path
CUR = Path("${ITER}")
VC = CUR / "version-compare"
m = json.loads((VC / "manifest.json").read_text())
tally = {"current_wins": 0, "previous_wins": 0, "ties": 0}
prs, notes = [], ["seat_A 按 run 序号奇偶交替，平衡座位偏差"]
for p in m.get("pairs", []):
    cj = VC / p["dir"] / "comparison.json"
    wv = "missing"
    if cj.exists():
        w = json.loads(cj.read_text()).get("winner")
        if w == "TIE":
            wv = "tie"; tally["ties"] += 1
        elif w in ("A", "B"):
            other = "current" if p["seat_A"] == "previous" else "previous"
            wv = p["seat_A"] if w == "A" else other
            tally[wv + "_wins"] += 1
        else:
            notes.append(p["dir"] + ": winner 非法值 " + repr(w))
    else:
        notes.append(p["dir"] + ": comparison.json 缺失（judge 失败）")
    q = dict(p); q["winner_version"] = wv; prs.append(q)
if tally["current_wins"] > tally["previous_wins"]:
    verdict = "current"
elif tally["previous_wins"] > tally["current_wins"]:
    verdict = "previous"
elif prs and tally["ties"] == len(prs):
    verdict = "tie"
else:
    verdict = "mixed"
outdoc = {"iteration": m["iteration"], "compared_to": m["compared_to"], "skill_fingerprint": m["skill_fingerprint"], "comparable_cases": sorted({p["case"] for p in m.get("pairs", [])}), "skipped_cases": m.get("skipped_cases", []), "pairs": prs, "tally": tally, "verdict": verdict, "notes": notes}
(CUR / "version-compare.json").write_text(json.dumps(outdoc, ensure_ascii=False, indent=1))
print(json.dumps({"tally": tally, "verdict": verdict, "pairs_judged": len([p for p in prs if p["winner_version"] != "missing"])}, ensure_ascii=False))`

// ---------- 确定性 DAG ----------
phase('Setup')
const baselineCopyBlock = SKIP_BASELINE ? `
然后（P1 baseline 复用）用 Bash 把下面的 python 脚本【原样】以 heredoc 执行（python3 <<'PYEOF' … PYEOF），一行都不要增删改，并把它打印的 JSON 原样附在汇报里。
它幂等（有 .baseline-reused 标记就跳过）、只写 ${ITER}/results/ 下的 without_skill 目录，绝不碰 skill 文件和上一轮目录：

import json, shutil
from pathlib import Path
CUR = Path("${ITER}")
PREV = Path("${PREV_ITER}")
copied, missing = [], []
for cid in ${JSON.stringify(cases.map(c => c.id))}:
    src = PREV / "results" / cid / "without_skill"
    dst = CUR / "results" / cid / "without_skill"
    if not src.exists():
        missing.append(cid); continue
    marker = dst / ".baseline-reused"
    if marker.exists():
        copied.append(cid + " (already)"); continue
    shutil.copytree(src, dst, dirs_exist_ok=True)
    marker.write_text(json.dumps({"reused_from": str(src)}, ensure_ascii=False))
    copied.append(cid)
print(json.dumps({"baseline_copied": copied, "baseline_missing": missing}, ensure_ascii=False))
` : ''
await agent(
  `跑 setup：python3 ${SCRIPTS}/run_iteration.py setup ${ITER}（读 eval-plan.json 建 results/ 目录）。${baselineCopyBlock}纯文字汇报建了哪些 run 目录、有无报错${SKIP_BASELINE ? '、baseline 拷贝结果（missing 非空必须原文写出）' : ''}。`,
  { label: 'setup', phase: 'Setup', agentType: A, model: M }
)
log(SKIP_BASELINE ? 'setup done（baseline 复用自 iteration-' + (iteration - 1) + '，without_skill 不重跑）' : 'setup done')

phase('Execute')
const execTasks = []
for (const c of cases) for (const cfg of EXEC_CONFIGS) for (let r = 1; r <= RUNS; r++) {
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
for (const c of cases) for (const cfg of EXEC_CONFIGS) for (let r = 1; r <= RUNS; r++) {
  const lbl = RUNS > 1 ? `grade-l3:${c.id}:${cfg}:run${r}` : `grade-l3:${c.id}:${cfg}`
  gradeTasks.push(() => agent(graderPrompt(c, cfg, r), { label: lbl, phase: 'GradeL3', agentType: A, model: M }))
}
const grades = await parallel(gradeTasks)
log(`graders: ${grades.filter(Boolean).length}/${gradeTasks.length} ok`)

phase('Suggest')
const suggest = await agent(suggestPrompt, { label: 'suggest', phase: 'Suggest', agentType: A, model: M })

phase('VersionCompare')
let vcResult = 'skipped'
if (versionCompare === false) {
  log('version-compare: skipped（versionCompare=false）')
  vcResult = 'skipped (disabled)'
} else if (!(Number(iteration) > 1)) {
  log('version-compare: skipped（iteration 1，无上一轮可比）')
  vcResult = 'skipped (iteration 1)'
} else {
  const vcSetup = await agent(vcSetupPrompt, { label: 'vc-setup', phase: 'VersionCompare', agentType: A, model: M, schema: VC_SETUP_SCHEMA })
  if (!vcSetup || !vcSetup.comparable || !Array.isArray(vcSetup.pairs) || vcSetup.pairs.length === 0) {
    const why = vcSetup ? (vcSetup.reason || 'no comparable pairs') : 'vc-setup failed'
    log(`version-compare: skipped（${why}）`)
    vcResult = `skipped (${why})`
  } else {
    log(`version-compare: ${vcSetup.pairs.length} 对匿名产物进场盲评（vs iteration-${iteration - 1}）`)
    const judges = await parallel(vcSetup.pairs.map(p => () =>
      agent(vcJudgePrompt(p), { label: `vc-judge:${p.case}:run${p.run}`, phase: 'VersionCompare', agentType: A, model: M })))
    log(`vc judges: ${judges.filter(Boolean).length}/${vcSetup.pairs.length} ok`)
    const agg = await agent(vcAggPrompt, { label: 'vc-aggregate', phase: 'VersionCompare', agentType: A, model: M, schema: VC_AGG_SCHEMA })
    vcResult = agg || 'aggregate failed（判决仍在 version-compare/pairs/ 各 comparison.json，可手动汇总）'
  }
}

phase('Finalize')
const final = await agent(
  `跑 finalize：python3 ${SCRIPTS}/run_iteration.py finalize ${ITER} --skill-name "${skillName || 'Skill'}"（aggregate + benchmark + review.html）。纯文字汇报 benchmark 各 configuration 的 pass_rate / mean_l3 / delta + review.html 路径 + 有无报错。`,
  { label: 'finalize', phase: 'Finalize', agentType: A, model: M }
)

return {
  baseline: SKIP_BASELINE ? `reused from iteration-${iteration - 1}（without_skill 未重跑）` : 'executed',
  executors_ok: execs.filter(Boolean).length,
  executors_total: execTasks.length,
  graders_ok: grades.filter(Boolean).length,
  graders_total: gradeTasks.length,
  suggest: typeof suggest === 'string' ? suggest.slice(0, 300) : 'n/a',
  version_compare: vcResult,
  finalize: typeof final === 'string' ? final.slice(0, 500) : 'n/a',
}

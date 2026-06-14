# Trigger Steps

调 skill description 触发准确率，共 4 步。走法：顺序执行，Step 4 不过就回 Step 2 重来（循环直到通过）。

---

### Step 1: 读取触发问题

Input: iterate-skill 跑出来的评测报告（标注了哪些词/场景触发太少或触发太多）
Output: 修改清单（哪些触发词要加、哪些要减）

**WHY（这步为什么存在）**：这一步是为了搞清楚"到底哪里出了问题"——不先提取具体信号，后面改 description 就是瞎改，没有方向。

读评测报告，整理两类问题：
- 触发太少（under-trigger）→ description 里缺了哪些词/场景，需要补
- 触发太多（over-trigger）→ description 里哪些词覆盖范围太广，需要删或收窄

如果没有评测报告，请用户直接说清楚：哪些情况本该触发却没触发、哪些情况不该触发却触发了。

### Step 2: 修改 Description

Input: 修改清单 + 当前 YAML description
Output: 改好的 YAML description（写入 SKILL.md）

**WHY（这步为什么存在）**：这一步是实际动手改——description 是 skill 唯一的触发机制，加词减词都要精准。改完立即存盘，防断网丢失。

在 description 里加词或减词。修改后立即保存到磁盘。
**注意**：改文件走 skill-creator 流程。让 AI Edit target SKILL.md → PostToolUse hook 自动 lint。

### Step 3: 触发测试

Input: 改好的 description + 20 个测试 prompt（10 个 should-trigger + 10 个 should-NOT-trigger）
Output: 触发准确率测试结果 → evals/workspace/trigger-test/results.json

**WHY（这步为什么存在）**：这一步是验证改动有没有用——改了不测等于不知道改对了还是改坏了。20 道题（10 道应触发 + 10 道不该触发）是能得出可信结论的最小测试量。

"不该触发"的 10 道题必须是边界题：要拿**看起来很像、但其实不该触发**的问题，不能拿明显无关的凑数。比如 skill 是"帮你设计网页"，测"帮我设计一个 logo"才有意义——测"帮我写斐波那契"毫无用处。

→ 调用 `../../scripts/run_eval.py`（这是 run_eval.py 的正确使用场景 — 它专门测试触发准确率）

机制：脚本在 `.claude/commands/` 临时注入假 command（含 target 的 description），用 `claude -p --output-format stream-json` 跑 query，监听 `content_block_start` event 早期检测 tool_use。命中 Skill/Read = 触发，其他 tool = 没触发。10 worker 并发。

### Step 4: 用户确认

Input: 触发测试结果
Output: 用户决定通过 / 再调

**WHY（这步为什么存在）**：这一步是让人来判断——自动化只看通过率数字，但具体哪道题误触发、哪道题漏触发，只有你看了才能决定是否还需要进一步调。

把测试结果摆出来让用户看：
- 哪些 should-trigger 通过了、哪些没有
- 哪些 should-NOT-trigger 正确拒绝了、哪些误触发了

通过条件：
- should-trigger 通过率 ≥ 90%
- should-NOT-trigger 正确拒绝率 ≥ 90%

IF 通过 → 退出 Trigger mode
IF 不通过 → 回到 Step 2 继续调整

**错误处理：**
IF 反复 5 轮仍不通过 → 说明 description 设计本身有问题（触发边界本来就画不清楚），建议跳出 trigger-tune，重新想清楚这个 skill 的核心定位是什么

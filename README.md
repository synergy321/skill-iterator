# skill-iterator

> A Claude Code plugin that puts a skill to work, scores it across three levels, and hands back a concrete list of improvements — without ever touching the skill it is testing.

## What it does

Writing a skill is easy. Knowing whether it actually *helps* is not. skill-iterator answers that question empirically:

- **Run it for real** — make the skill perform an actual task, not a hypothetical one.
- **Score in three layers** — L1: did it produce anything? · L2: is the output correct? · L3: is it any good?
- **Run every task twice** — once with the skill, once without — so you can isolate what the skill itself contributed.
- **Get an actionable scorecard** — every suggestion names a file, a change, and a reason. No filler.

## Four commands

The plugin splits by user intent — pick the one that matches what you want to know:

- **`/skill-iterator`** — umbrella router; start here if you are not sure which flow you need.
- **`/iterate-skill`** — L1/L2/L3 content scoring + improvement suggestions over multiple iterations.
- **`/trigger-tune`** — optimize a skill's description so it fires at the right time (trigger-rate tuning).
- **`/blind-test`** — end-to-end output test with anti-fixture-leak guards.

## Design principles

- **Read-only on the target.** It never mutates the skill under test — it only emits a `suggestions.json`. Applying changes is a separate, deliberate step.
- **With / without double-run.** The core invariant: you cannot claim a skill helps unless you have measured the same task without it.
- **Three-layer lint.** Schema and structure are validated by a PostToolUse hook, a git pre-commit gate, and a manual validator.

## Install

This repo is a Claude Code marketplace containing one plugin. From inside Claude Code:

```
# 1. add the marketplace
/plugin marketplace add synergy321/skill-iterator

# 2. install the plugin
/plugin install skill-iterator@skill-iterator-plugin
```

| | |
|---|---|
| plugin | `skill-iterator` |
| marketplace | `skill-iterator-plugin` |

Skills, agents, and the lint hook are auto-discovered — no extra configuration.

## Requirements & honest caveats

- **Read-only evaluation works out of the box.** Scoring, blind-test, and the suggestion scorecard run with just Claude Code + Python 3.
- **Applying suggestions expects an external editing workflow.** Several skills route the "apply changes" step to `skill-creator`, an authoring tool that is **not bundled** with this plugin. Without it you still get every finding — you just apply the edits with your own workflow.
- **Batch mode needs an API key.** `run_loop.py` drives the Anthropic SDK directly and reads `ANTHROPIC_API_KEY` from the environment; the agent-spawn path does not.
- **Skill prompts and output are in Chinese.** The evaluation methodology is language-agnostic, but the user-facing text is Chinese.

## License

MIT © 2026 Travis Chong. Use it, fork it, ship it.

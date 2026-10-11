# Planning backends

| Backend | How | When |
|---|---|---|
| `rule_based` (default) | offline keyword interpretation -> validated plan + templated storyboard | always available; good structure and pacing, generic wording |
| `file` | `--plan-file plan.json` | bespoke creative direction written by you or by Claude Code |

Claude Code is the interactive controller, not an embedded runtime API. The recommended
high-quality loop:

```
python -m app.main plan --prompt "..." --out drafts/keyboard
# Ask Claude Code to rewrite drafts/keyboard/plan.json + storyboard.json (scenes, prompts, narration)
python -m app.main create --prompt "..." --plan-file drafts/keyboard/plan.json --no-run
# copy the edited storyboard.json into projects/<id>/ to override the generated one, then:
python -m app.main resume --project <id>
```

Every plan and storyboard is validated with Pydantic. Storyboard scene durations must render to
exactly the requested length once transition overlaps are subtracted, or validation fails.

An optional LLM API planning backend for unattended runs is **not implemented**. It would be a
paid API and would sit behind the same budget gate.

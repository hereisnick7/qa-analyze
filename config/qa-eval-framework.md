# QA Agent — Evaluation Framework

Professional evaluation framework for measuring and tracking `qa-impact-agent` output quality.
Update after each task closes. Review KPIs quarterly.

---

## Benchmark Task Set

Five representative task types for regression-testing prompt changes.
Use historical tasks from your own product with known outcomes (which bugs were found/missed).

| ID | Type | Domain | Mode | Why chosen |
|---|---|---|---|---|
| BT-01 | <e.g. analytics event tracking> | Low | Light | Baseline — minimum viable output |
| BT-02 | <e.g. UI display change> | Medium | Standard | Most common task type |
| BT-03 | <e.g. eligibility/rule change> | Critical | Deep | Core domain, highest stakes |
| BT-04 | <e.g. payment flow bug fix> | High | Deep | Financial/critical, regression surface |
| BT-05 | <e.g. session management refactor> | High | Standard/Deep | Multi-area, hidden deps heavy |

**Prompt regression gate:** before any prompt change, run agent against all 5 BTs.
No KPI may regress by >5% vs last baseline. If it does — revert or fix before merging.

---

## KPIs and Targets

| KPI | Definition | Method | Target | Current |
|---|---|---|---|---|
| **AC Coverage Rate** | % of AC items traceable to ≥1 TC | Count AC items → map to TCs with AC Reference field | ≥ 85% | — |
| **Edge Case Coverage** | % of identified edge cases in analysis that have ≥1 TC | Count edge cases → count corresponding TCs | ≥ 70% | — |
| **Hallucination Rate** | % of [HYPOTHESIS] items confirmed false after testing | Track: total HYPOTHESIS items vs proven-wrong ones | ≤ 15% | — |
| **Risk Accuracy** | Risk level matches actual bug severity | Compare agent risk → actual bugs found in task | ≥ 75% | — |
| **Reviewer Correction Rate** | % of TCs changed or removed by human reviewer | Log changes during review | ≤ 20% | — |
| **Execution Success Rate** | % of TCs executable without clarification by middle QA | First-run tracking by tester | ≥ 80% | — |
| **Traceability Score** | % of TCs with valid AC Reference field | Count "not in AC" vs total TCs | 100% | — |
| **Hidden Dep Accuracy** | % of hidden dependencies that were actually relevant | Retrospective after testing completes | ≥ 60% | — |
| **Duplicate TC Rate** | % of TC pairs covering identical scenario | Manual review by senior QA | ≤ 5% | — |
| **RBAC Coverage** | ≥1 RBAC negative TC per access-gated flow | Manual check: count gates in diff → count RBAC TCs | 100% | — |
| **False Completeness Rate** | % of tasks where output looked complete but missed known bugs | Retrospective | ≤ 10% | — |
| **Usefulness Score** | "Would I generate this manually without the agent?" (1-5) | Post-task self-rating by QA | ≥ 4.0 | — |

---

## Evaluation Process

### Per-task (after testing closes)

```
1. Open tasks/qa/{KEY}/analysis.md
2. Count: AC items in Jira → TCs with AC Reference for that item
   → fill AC Coverage Rate for this task
3. Review [HYPOTHESIS] items → mark each: confirmed / refuted / not verified
   → fill Hallucination Rate contribution
4. Compare agent Risk Level → actual severity of bugs found
   → fill Risk Accuracy
5. Fill Usefulness Score (1-5):
   5 = "saved me an hour, would not have caught this without agent"
   4 = "useful starting point, added ~30% on top"
   3 = "correct but I would have found this anyway"
   2 = "mostly obvious, some wrong items I had to remove"
   1 = "generated noise, spent more time reviewing than it saved"
6. Add row to Metrics Log below
```

### Per-prompt-change (before merging any agent prompt edit)

```
1. Run /qa-analyze against BT-01 through BT-05
2. Capture: Risk Level, P0 count, HYPOTHESIS count, required sections present
3. Compare vs previous baseline (last row in Baseline Log below)
4. Gate: no KPI regression >5% — if failed, do not merge
5. If passed: update Baseline Log
```

---

## Metrics Log

Track per-task measurements. `/qa-report` step 3.5 appends a row automatically
at the end of every `--result=pass|fail|partial|bug` run, reading back the QA
comment you edit post-test (checkboxes/notes/pruned items) instead of asking
from scratch — see `.claude/skills/qa-report/SKILL.md` → step 3.5.

| Date | Task | Risk set | Risk correct? | P0 count | P0 bugs found | HYPOTHESIS total | HYPOTHESIS false | Hidden deps total | Hidden deps relevant | Usefulness |
|---|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — | — |

---

## Baseline Log

Track agent output baseline for prompt regression testing.

| Date | Prompt version | BT-01 risk | BT-02 risk | BT-03 risk | BT-04 risk | BT-05 risk | Notes |
|---|---|---|---|---|---|---|---|
| — | v1.0 | — | — | — | — | — | Initial baseline — not yet measured |

---

## Review Cadence

| Cadence | Action |
|---|---|
| Per task | Fill Metrics Log row |
| Monthly | Review KPIs vs targets; note trends |
| Quarterly | Prompt improvement cycle if ≥2 KPIs miss target; update Historically Unstable Areas review dates |
| Per prompt change | Run benchmark set, update Baseline Log |

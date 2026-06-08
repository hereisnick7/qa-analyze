# /qa-analyze — QA Impact Agent v1.2

> Claude Code skill + sub-agent for backend-aware QA impact analysis.
> Reads Jira + GitLab MR diff, maps business logic impact, produces a focused test scope — and optionally generates full step-by-step test cases.

---

## What it does

Instead of a QA engineer manually reading a Jira ticket and guessing what to test — the agent does this in ~3 minutes by reading the **actual code diff**, not just the ticket description.

1. Finds the MR in GitLab by Jira task key
2. Reads the diff for high-risk files
3. Compares what Jira claims vs what the MR actually implements
4. Builds an impact map: `[FACT]` / `[HYPOTHESIS]` / `[UNKNOWN]`
5. Finds hidden dependencies a QA engineer reading Jira alone would miss
6. Assesses risk by domain rules, not diff size
7. Produces a focused test scope: P0 (must) / P1 (recommended) / P2 (optional) — hard caps of 4/4/3
8. On request: generates full step-by-step test cases with preconditions, steps, expected results
9. Posts test cases to Jira as a comment

---

## Usage

```
/qa-analyze DEV-2795
/qa-analyze DEV-2795 --mr=frontend-repo/1148   # skip MR discovery, use known MR
```

After analysis, the agent asks:
> "Расписать полные тест-кейсы с шагами? (P0 / P0+P1 / все)"

---

## Analysis modes

| Mode | When | What |
|---|---|---|
| **Light** | Analytics, copy changes, config-only | File list only, P0 scope |
| **Standard** | Normal feature work | Full analysis, targeted diff for risky files |
| **Deep** | Auth, payments, KYC, antifraud, bonuses | Full diff, extended dependency tracing |

Deep mode requires explicit confirmation — the agent proposes it when the task title contains high-risk keywords.

---

## Seed Mode — how the agent learns

After each analysis, the agent surfaces 1–3 questions whose answers would improve future analyses. The orchestrator asks them, you confirm, and the answer is saved to `config/qa-agent-context.md`.

Example rule saved after a task:
```
[2024-01-15] GGR is stored in cents. GGR_THRESHOLD=35000 = $350 effective threshold.
             Context: TASK-123
```

The agent reads the full knowledge base before every analysis. Over time it learns your product's domain: which areas are historically unstable, what technical quirks exist, what decisions were made and why.

---

## File structure

```
.claude/
  agents/
    qa-impact-agent.md        ← sub-agent system prompt (the "brain")
  skills/
    qa-analyze/
      SKILL.md                ← /qa-analyze skill entry point (orchestrator)
    qa-report/
      SKILL.md                ← /qa-report skill (post-test documentation)
config/
  qa-agent-context.md         ← product knowledge base (fill in for your project)
```

---

## Setup

### Prerequisites

- [Claude Code](https://claude.ai/code) CLI
- `workflow` CLI configured with Jira and GitLab access
  (or adapt the Bash commands in `qa-impact-agent.md` to your own CLI)

### Install

1. Copy `.claude/` into your project's root (or your `~/.claude/` for global use)
2. Fill in `config/qa-agent-context.md` with your product details:
   - Product name, Jira project key, GitLab repo names
   - Test accounts
   - Domain risk classification (adjust to your business domain)
3. Run `/qa-analyze YOUR-TASK-KEY` in Claude Code

### Adapting to your stack

The agent uses these CLI commands (defined in `qa-impact-agent.md`):
```bash
workflow jira-task <KEY>          # fetch Jira task
workflow mrs <project> --state=all # list MRs
workflow mr <project> <id>         # MR metadata
workflow mr-changes <project> <id> # changed files
workflow mr-diff <project> <id>    # diff
workflow mr-notes <project> <id>   # MR comments
```

Replace these with your own Jira/GitLab CLI wrappers or direct API calls.

---

## Version history

| Version | Changes |
|---|---|
| v1.0 | Base analysis: MR discovery, Impact Map, test scope |
| v1.1 | Light/Standard/Deep modes; FACT/HYPOTHESIS/UNKNOWN labels; Seed Mode; hard P0/P1/P2 caps |
| v1.2 | Step 10: agent offers full step-by-step test cases after analysis; one-command Jira post |

---

## License

MIT

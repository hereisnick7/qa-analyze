---
name: qa-analyze
description: >
  Backend-aware QA impact analysis: reads Jira task + MR, discovers what actually
  changed, maps business logic impact, identifies hidden dependencies, assesses
  domain risk, produces intelligent test scope. Test cases only on explicit request.
  Use when: "разбери задачу DEV-XXXX", "что тестировать", "qa-analyze DEV-XXXX",
  "проанализируй задачу для теста", "impact analysis DEV-XXXX".
---

# /qa-analyze

Entry point for QA impact analysis. Coordinates `qa-impact-agent` with a full context pack.

## Usage

```
/qa-analyze DEV-XXXX                        # full analysis, mode pre-assessed from title
/qa-analyze DEV-XXXX --mr=betzo-backend/419 # skip Discovery, use known MR directly
```

The agent auto-detects `[FE]` / `[BE]` from the Jira title (searches only
relevant projects). Deep mode and ambiguous cases require user confirmation before launch.

`--mr=<project>/<id>` — continue mode: skip Step 0 (MR Discovery) and use the
provided MR directly. Use after a previous analysis already identified the MR.

## Algorithm

### 1. Parse input

Extract Jira key from args. No key → ask: "Укажи ключ задачи, например DEV-2579."
Extract `--mr=<project>/<id>` if present (continue mode — skip Discovery).

### 2. Quick title check — mode pre-assessment and MR link detection

Before launching the agent, fetch the Jira task (one fast call):

```bash
rtk summary workflow jira-task {KEY}
```

**2a. Check for direct MR link (fast path).** Scan the task description and comments for a GitLab MR URL (pattern: `merge_requests/\d+` or `!<number>`). If found — extract `<project>/<mr_id>` and set the `--mr` flag; the agent will skip Step 0 Discovery entirely. This path will activate automatically once the team adds MR links to Jira tasks.

**2b. Mode pre-assessment.** Read only the **Summary** field. Apply this logic:

**→ Ask confirmation before Deep** when the title contains any of:
`cashier`, `bonus`, `deposit`, `withdraw`, `payment`, `wallet`, `balance`,
`auth`, `login`, `register`, `kyc`, `verification`, `promo`, `coupon`,
`antifraud`, `multiaccount`, `subscription` (financial/security flows)

Ask: "Задача затрагивает [найденная зона]. Предлагаю **Deep** анализ — полный диф всех файлов, расширенный поиск зависимостей. Подтвердить?"

**→ Ask confirmation for ambiguous cases** when:
- Title is vague / large scope ("рефакторинг", "обновление", "переработка", no clear domain signal)
- Title has multiple unrelated areas mentioned

Ask: "Не могу однозначно определить глубину. Предлагаю **Standard** — подтвердить или выбрать другой режим? (Light / Standard / Deep)"

**→ Proceed immediately with Standard** when:
- Normal feature work, clear limited scope, no high-risk keywords

**→ Proceed immediately with Light** when:
- Title clearly indicates: analytics events, copy/text changes, config-only, minor UI

### 3. Launch agent with confirmed mode

```python
Agent(subagent_type="qa-impact-agent", prompt="""
Perform QA impact analysis.

Jira key: {KEY}
Analysis mode: {MODE}
Jira project: DEV
GitLab projects to search for MR: betzo, betzo-admin, betzo-backend, betzo-wallets, betzo-antifraud
{If --mr flag provided: "Skip Step 0. Use MR: <project>/<id> directly."}

Start by reading: config/qa-agent-context.md

Then follow the full qa-impact-agent workflow:
  Step 0: MR Discovery — read [FE]/[BE] tag, case-insensitive grep (skip if --mr provided)
  Step 1: Jira Analysis — read comments carefully; fetch up to 3 linked issues
  Step 2: Fast Scan — if actual files reveal higher risk than the given mode, flag it in output
  Step 3: Deep Scan if mode=Deep or triggered by critical file paths
  Step 4: Jira vs MR Comparison
  Step 5: Impact Map with [FACT] / [HYPOTHESIS] / [UNKNOWN] labels
  Step 6: Hidden Dependencies
  Step 7: Risk Assessment using domain rules from context file
  Step 8: Test Scope P0 / P1 / P2 (caps: P0 max 4, P1 max 4, P2 max 3)
  Step 9: Seed Mode — output questions only, orchestrator handles answers and file write
  Step 10: Test Cases — DO NOT generate unless user explicitly requests

Produce output in the standard format starting with Executive Summary.
State the analysis mode at the top: `Analysis mode: {MODE}`
""")
```

### 4. After agent output — validate required sections

Before presenting output to user, check that all required sections are present:

Required: `## 📋 Суть`, `## Риск`, `## 🗺️ Impact Map`, `## ✅ Тест-скоуп`

If any required section is missing:
Notify user: "⚠️ Анализ неполный: отсутствует [section]. Повторить или продолжить?"
- Retry → re-launch agent with same params
- Continue → present partial output with warning

### 4b. After agent output — Mode Escalation handling

If the agent's output contains `MODE_ESCALATION_RECOMMENDED`:
Ask: "Агент обнаружил Critical/High файлы в [MODE] режиме.
Переключить на **Deep** — полный диф и расширенный поиск зависимостей? (да / нет)"

- Yes → re-launch agent with `Analysis mode: Deep`
- No → proceed with current analysis; note in output: "mode escalation declined"

### 4c. After agent output — Confidence LOW handling

If the agent's output contains `⚠️ CONFIDENCE: LOW`:
Ask the user: "Агент нашёл MR !<id> по содержимому, не по ключу в ветке.
Это правильный MR для <KEY>? (да / нет / укажи другой)"

- User confirms → proceed, analysis is valid
- User says no → ask for correct MR link, re-launch with `--mr=` flag
- No response → do not present the analysis as actionable

### 5. After agent output — Seed Mode handling

If the agent's output contains a `## Seed Mode` section with questions:

1. Write PENDING entries to `config/qa-agent-context.md` under `## Seed Mode: Pending Questions`:
   ```
   PENDING [YYYY-MM-DD] <question>. Triggered by: <KEY>
   ```
2. Ask the user each question directly in chat.
3. For each answer:
   - Confirmed → append to `## Seed Mode Rules`: `- [YYYY-MM-DD] <rule>. Context: <KEY>`
     and update PENDING → remove or mark DECLINED
   - Declined → mark `DECLINED [YYYY-MM-DD] <question>`
4. Confirm: "Сохранено в базу знаний агента."

If no Seed Mode section → skip.

### 6. Save analysis to file

After presenting output to user, silently save the full analysis:
```bash
mkdir -p tasks/qa/{KEY}
# Write analysis to file — enables /qa-report to read P0 items without conversational context
```
Then write the agent's full output to `tasks/qa/{KEY}/analysis.md`.

Also append one row to the metrics table in `config/qa-agent-context.md` under `## Agent Quality Metrics`:
```
| {TODAY} | {KEY} | {Risk level from output} | — | {P0 count} | — | — | {MODE} |
```

### 7. After agent output — Jira questions offer

If the agent's `## Unknowns & Questions` section contains **Developer questions**:
Ask once: "Запостить вопросы к разработчику в Jira комментарием?"

If yes:
```bash
workflow jira-comment {KEY} "Вопросы к разработчику по задаче {KEY}:
<Developer questions from analysis>"
```

### 8. After agent output — Test Cases offer

After presenting the full analysis, always ask:

> "Расписать полные тест-кейсы с шагами? (P0 / P0+P1 / все)"

Determine scope from user response:
- "да" / "P0" → только P0 items
- "P0+P1" → P0 + P1
- "все" / "all" → P0 + P1 + P2

If yes, re-invoke the agent in test-case-only mode:

```python
Agent(subagent_type="qa-impact-agent", prompt="""
Generate test cases for {KEY}.

Task: {KEY}
MR: {project}/{mr_id}
Test scope: {P0 / P0+P1 / all}

Read config/qa-agent-context.md first.
Read tasks/qa/{KEY}/analysis.md — use it as the full analysis context instead of re-fetching Jira/MR.

Execute Step 10 only. Do NOT re-run Steps 0–9.

Apply all Test Design Techniques:
- BVA for every numeric threshold found in the analysis
- Decision Table for multi-condition logic (AND/OR)
- At least 1 negative scenario per P0 flow
- Data Integrity TC for Critical financial flows

Use concrete test accounts from config/qa-agent-context.md where available.
""")
```

If no → skip.

## Rules

- Never skip Step 0 unless `--mr` is provided
- Never auto-generate test cases
- Deep mode always requires user confirmation before launch
- Ambiguous cases always require user confirmation before launch
- Seed Mode answers are written by the orchestrator, not the agent
- One `/qa-analyze` call = one full analysis session

## Output Contract

The agent produces the complete analysis output. Relay it directly to the user.

Required sections (empty section → `none`):
`## 📋 Суть`, `## Риск`, `## 🔧 Что изменено`, `## 🗺️ Impact Map`, `## ✅ Тест-скоуп`

Optional sections (include only when applicable):
`## 🔗 Скрытые зависимости`, `## ❓ Вопросы`, `## ⚠️ Требует внимания`, `## 🌱 Seed Mode`, `## Test Cases`

## Fail-fast

```
BLOCKED_KEY_MISSING
No Jira key was provided
Needed from user: task key in format DEV-XXXX
```

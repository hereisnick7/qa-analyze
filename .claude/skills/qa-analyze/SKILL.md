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
/qa-analyze DEV-XXXX --retest               # lightweight re-check of a fix, reuses prior analysis.md
```

The agent auto-detects `[FE]` / `[BE]` from the Jira title (searches only
relevant projects). Deep mode and ambiguous cases require user confirmation before launch.

`--mr=<project>/<id>` — continue mode: skip Step 0 (MR Discovery) and use the
provided MR directly. Use after a previous analysis already identified the MR.

`--retest` — for tasks that get tested more than once (fix → re-test → fix
again). Requires `tasks/qa/{KEY}/analysis.md` from a prior run. Missing →
"Нет прошлого анализа для {KEY}. Обычный /qa-analyze {KEY} для первого прогона."
See step 2c and step 3.

## Algorithm

### 1. Parse input

Extract Jira key from args. No key → ask: "Укажи ключ задачи, например DEV-2579."
Extract `--mr=<project>/<id>` if present (continue mode — skip Discovery).

**Auto-detect retest — no flag required.** Before anything else, check:
```bash
test -f tasks/qa/{KEY}/analysis.md && echo exists
```
If it exists AND the user didn't explicitly ask for a fresh full analysis
(phrases like "разбери заново", "полный анализ", "с нуля"), treat this as a
retest trigger — the user coming back to a task that already has an analysis
is itself the signal, not just the literal `--retest` flag. Ask once:

> "Для {KEY} уже есть анализ от {date из analysis.md}. Прогнать retest (проверить дельту с прошлого раза) вместо полного анализа? (да / нет, разобрать заново)"

"да" → proceed as `--retest` (step 2c). "нет" → proceed as a normal fresh run
(this will overwrite the prior analysis — see step 6). The explicit `--retest`
flag still works directly without this question, for scripted/fast use.

### 2. Quick title check — mode pre-assessment and MR link detection

Before launching the agent, fetch the Jira task (one fast call):

```bash
rtk summary workflow jira-task {KEY}
```

**2a. Check for direct MR link (fast path).** Scan the task description and comments for a GitLab MR URL (pattern: `merge_requests/\d+` or `!<number>`). If found — extract `<project>/<mr_id>` and set the `--mr` flag; the agent will skip Step 0 Discovery entirely. This path will activate automatically once the team adds MR links to Jira tasks.

**2b. Mode pre-assessment.** Read only the **Summary** field. Apply this logic:

**→ Proceed automatically with Deep** when the title contains any of:
`cashier`, `bonus`, `deposit`, `withdraw`, `payment`, `wallet`, `balance`,
`auth`, `login`, `register`, `kyc`, `verification`, `promo`, `coupon`,
`antifraud`, `multiaccount`, `subscription` (financial/security flows)

No confirmation — this is a deterministic keyword match against
`config/qa-agent-context.md` → Domain Risk Classification, not a judgment
call. State it and proceed:

> "Задача затрагивает [найденная зона] → запускаю **Deep** анализ (полный диф всех файлов, расширенный поиск зависимостей)."

This list grows over time via `/qa-report`'s metrics-feedback step: when a
completed task's risk turns out to have been underestimated and its title
matched none of these keywords, the orchestrator asks whether to add the
missing term here. Keep the list flat, lowercase, comma-separated — no
sub-categories.

*(2026-07-03: this branch used to ask for confirmation before launching Deep.
Removed once the keyword match itself proved reliable in practice — Deep here
means "the domain rules already say Critical/High", so the check adds a
round-trip without adding judgment. The mid-analysis Mode Escalation check in
step 4b is unaffected — that one fires on a real-time discovery, not a title
match, and still asks.)*

**→ Ask confirmation for ambiguous cases** when:
- Title is vague / large scope ("рефакторинг", "обновление", "переработка", no clear domain signal)
- Title has multiple unrelated areas mentioned

Ask: "Не могу однозначно определить глубину. Предлагаю **Standard** — подтвердить или выбрать другой режим? (Light / Standard / Deep)"

**→ Proceed immediately with Standard** when:
- Normal feature work, clear limited scope, no high-risk keywords

**→ Proceed immediately with Light** when:
- Title clearly indicates: analytics events, copy/text changes, config-only, minor UI

### 2c. Retest mode (`--retest`)

Check `tasks/qa/{KEY}/analysis.md` exists — required, it is the context this
mode reuses. Missing → stop, tell the user to run a normal `/qa-analyze {KEY}`
first.

Skip mode pre-assessment (2b) — reuse the MR project/id already recorded in
`analysis.md` (or `--mr` if also passed). Proceed straight to step 3 with the
retest prompt addition.

### 3. Launch agent with confirmed mode

```python
Agent(subagent_type="qa-impact-agent", prompt="""
Perform QA impact analysis.

Jira key: {KEY}
Analysis mode: {MODE}
Jira project: DEV
GitLab projects to search for MR: betzo, betzo-admin, betzo-backend, betzo-wallets, betzo-antifraud
{If --mr flag provided: "Skip Step 0. Use MR: <project>/<id> directly."}
{If --retest: "Retest mode — re-analysis vs a prior review, same pattern as
DEV-2784. Read tasks/qa/{KEY}/analysis.md first — it holds the prior analysis
of this same MR, including its date. Produce a FULL standard-format output
(not a separate delta block), but frame it explicitly as a re-analysis:
Executive Summary states 'Re-analysis of MR !{id}, delta vs {prior date}' plus
why (new commits pushed, dev note, etc.); the 🔧 Что изменено section header
becomes 'Что изменено (delta vs {prior date})' and lists only what changed
since then; carry forward anything from the prior Impact Map/Test Scope that
is still valid without re-deriving it from scratch, and clearly mark what's
newly changed. This is the output that replaces the file (see step 6) — it
should stand alone as the current analysis, not require reading the old file
to make sense."}

Start by reading: config/qa-agent-context.md

Then follow the full qa-impact-agent workflow:
  Step 0: MR Discovery — read [FE]/[BE] tag, case-insensitive grep (skip if --mr provided)
  Step 1: Jira Analysis — full comment history (jira-comments); download & read text attachments (.md/.txt); fetch up to 3 linked issues
  Step 2: Fast Scan — if actual files reveal higher risk than the given mode, flag it in output
  Step 3: Deep Scan if mode=Deep or triggered by critical file paths
  Step 4: Jira vs MR Comparison
  Step 5: Impact Map with [FACT] / [HYPOTHESIS] / [UNKNOWN] labels
  Step 6: Hidden Dependencies
  Step 7: Risk Assessment using domain rules from context file
  Step 8: Test Scope P0 / P1 / P2 (no fixed count — scope purely by risk, per Rule #7)
  Step 9: Seed Mode — output questions only, orchestrator handles answers and file write
  Step 10: Test Cases — DO NOT generate unless user explicitly requests

Produce output in the standard format starting with Executive Summary.
State the analysis mode at the top: `Analysis mode: {MODE}`
""")
```

### 4. After agent output — validate required sections

Before presenting output to user, check that all required sections listed in
Output Contract (below) are present.

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

Normal run → write the agent's full output to `tasks/qa/{KEY}/analysis.md`,
overwriting any prior content.

`--retest` run → **also overwrites** `tasks/qa/{KEY}/analysis.md` with the new
full output. *(2026-07-03: originally designed to append a separate `##
Retest` block; switched to overwrite after checking DEV-2784 — the one real
precedent for re-analysis that existed before this feature — which replaced
the file entirely and framed the new content as "delta vs {prior date}" inline.
Matching a pattern that already worked beats inventing a new one.)* History
isn't lost — the fix-history angle instead lives in the file's own "Re-analysis
of MR !{id}, delta vs {date}" framing, and in git if/when this folder is
committed.

### 7. After agent output — Test Cases offer

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

Do NOT include specific test account credentials in the output.
""")
```

If no → skip.

### 8. After test cases — Jira comment offer

After test cases are generated (or if user asks to post a comment directly), offer:

> "Запостить комментарий в Jira? (суть + тест-кейсы)"

**Default comment = Суть + тест-кейсы only.**

Then ask separately:

> "Добавить вопросы к разработчику в комментарий?"

Compose the comment based on answers:
- Суть + тест-кейсы — **always** (if posting)
- Вопросы к разработчику — **only if user confirms**

#### Comment format — EXACT TEMPLATE (do not deviate)

**CRITICAL:** Use `##` for headers (Markdown/ADF), NOT `h2.` (Jira wiki markup).
`h2.` renders as plain text in Jira Cloud. `##` renders as a real heading.
Use `---` (3 dashes) as separator. NOT `----` (4 dashes).
Read this template carefully before writing the comment file.

**NEVER include Риск or Precheck sections in the comment. Суть + тест-кейсы only.**

```
## Суть

<2–3 sentences from analysis>

MR: !{id} · {project} · {branch}

---

## P0 — Тест-кейсы

1️⃣ **TC-01 — <name>**

1. <METHOD> <endpoint> `<request body if any>` → <expected result>
2. <step> → <expected>
3. <step> → <expected>

---

2️⃣ **TC-02 — <name>**

1. <step> → <expected>
2. <step> → <expected>

---

## P1 — Тест-кейсы

3️⃣ **TC-03 — <name>**

1. <step> → <expected>

---

## Вопросы к разработчику   ← only if user confirmed
```

**Format rules:**
- `##` headers, `---` separators — BOTH between sections and between individual TCs
- TC anchor: `1️⃣ **TC-01 — Name**` — emoji number + `**bold name**` (double asterisks)
- Each step on its own numbered line; expected result inline via `→`
- Request bodies inline as backtick code: `` `{"key": "val"}` ``
- NO Риск section, NO Precheck section — ever
- Empty line after every `---` separator and after every TC header before steps

Post via:
```bash
workflow jira-comment {KEY} --from-file=/tmp/qa_comment_{KEY}.txt
```

Write to a temp file first to avoid shell-escaping issues and to allow guard pre-check.

## Rules

- Never skip Step 0 unless `--mr` is provided
- Never auto-generate test cases
- Deep mode triggered by an explicit domain keyword match proceeds automatically — no confirmation, state the detected zone and launch
- Deep mode triggered mid-analysis (Mode Escalation, step 4b) still requires confirmation — that's a real-time discovery, not a keyword match
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

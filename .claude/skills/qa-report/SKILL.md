---
name: qa-report
description: >
  Post-test documentation for a Jira task: record test result, generate a structured
  bug report, offer to comment in Jira, offer to transition task status.
  Use when: "задача прошла", "нашёл баг", "qa-report DEV-XXXX", "зафиксируй результат",
  "оформи баг", "переведи задачу", "результат тестирования DEV-XXXX".
---

# /qa-report

Post-test flow. Closes the testing loop: result → optional bug report → Jira comment → status transition.

## Usage

```
/qa-report DEV-XXXX --result=pass
/qa-report DEV-XXXX --result=fail
/qa-report DEV-XXXX --result=partial
/qa-report DEV-XXXX --result=bug --title="краткое описание бага"
```

`--result` values:
- `pass` — всё протестировано, всё работает
- `fail` — задача не прошла тестирование
- `partial` — часть прошла, часть нет (уточнить что именно)
- `bug` — найден баг, нужен баг-репорт

## Algorithm

### 1. Parse input

Extract Jira key and `--result` flag. If result not provided — ask:
"Какой результат? (pass / fail / partial / bug)"

### 2. Fetch task context

```bash
rtk summary workflow jira-task {KEY}
workflow jira-transitions {KEY}
```

Read: Summary, current status, available transitions.

Also check for saved analysis:
```bash
cat tasks/qa/{KEY}/analysis.md 2>/dev/null || true
```
If file exists — read P0 items from it for the test summary. If not — ask user.
Also note, for step 3.5: read the `<!-- qa-meta -->` footer at the end of
`analysis.md` for `risk`, `mode`, `p0_count`/`p1_count`/`p2_count` — that
footer is the only part of the file this step should parse (see
`qa-impact-agent.md` → Output Format for why). Older analyses from before
2026-07-03 won't have this footer — for those, fall back to the prose
(`` `{MODE}` · MR ... `` line and `## {emoji} Риск: {level}` heading), and note
in the metrics row that the source was legacy-parsed, not footer-parsed.

### 3. Handle by result type

---

#### result=pass

Generate test summary:
```
✅ DEV-{KEY} — тестирование завершено

Протестировано: [перечислить P0 пункты из анализа если известны, или попросить уточнить]
Стенд: [если известен из контекста]
Дата: {TODAY}
Замечаний нет.
```

Ask: "Запостить в Jira комментарием?"
If yes:
```bash
workflow jira-comment {KEY} "<summary text>"
```

Ask: "Перевести задачу? Доступные переходы: [list from jira-transitions]"
If yes:
```bash
workflow jira-transition {KEY} "<target>"
```

---

#### result=fail

Ask: "Что именно не работает? Опиши коротко."

Generate fail summary:
```
❌ DEV-{KEY} — тестирование не пройдено

Причина: {user description}
Стенд: [если известен]
Дата: {TODAY}
Требуется: возврат на доработку
```

Ask: "Запостить в Jira + перевести в In Progress?"

---

#### result=partial

Ask: "Что прошло, что нет?"

Generate partial summary:
```
⚠️ DEV-{KEY} — частичное тестирование

Прошло: {passed items}
Не прошло / не тестировалось: {remaining items}
Дата: {TODAY}
```

Ask: "Запостить в Jira?"

---

#### result=bug

Generate structured bug report. Ask for details if `--title` only was provided.

**Bug Report Format:**

```markdown
## 🐛 Bug: {title}

**Task:** DEV-{KEY}
**Severity:** {Critical / High / Medium / Low} ← suggest based on domain risk rules
**Priority:** {Blocker / High / Medium / Low}
**Reproducibility:** {Always / Intermittent / Once}
**Stend:** {where found}
**Build/MR:** {MR ID if known}
**Date:** {TODAY}

### Steps to Reproduce
1.
2.
3.

### Expected Result
{what should happen}

### Actual Result
{what actually happens}

### Technical Evidence
- Console errors: {paste or "none"}
- Network response: {relevant API call + status code, or "not checked"}
- Account used: {email + geo}
- Browser / device: {browser + version}
- Screenshots: [attach if available]
```

**Severity suggestion logic:**
- Bug in Critical area (cashier, bonus, deposit, auth) → Critical/High
- Bug in High area (balance, withdrawal, KYC) → High
- Bug in Medium/Low area → Medium/Low
- Read domain rules from `config/qa-agent-context.md`

After generating draft — show to user, ask to confirm or edit.

Then ask:
- "Добавить как комментарий к DEV-{KEY}?"

If adding as comment:
```bash
workflow jira-comment {KEY} "<bug report text>"
```

---

### 3.5 Metrics feedback → `config/qa-eval-framework.md` (Metrics Log)

*(2026-07-03: this used to write to `config/qa-agent-context.md` → `Agent
Quality Metrics`. Consolidated into `qa-eval-framework.md` → `Metrics Log`
instead — that's the richer, pre-existing KPI system (12 KPIs, benchmark set,
regression gate) that was sitting unused; keeping two half-filled tables for
the same purpose was the actual problem, not which one to pick.
`qa-agent-context.md` → `Agent Quality Metrics` is now a pointer, not written
to again — its historical rows stay as an archive.)*

Runs once per `/qa-report` call, for every `--result` value, right after the
result summary is generated and before Jira posting/status transition. Skip
silently only when `tasks/qa/{KEY}/analysis.md` does not exist (nothing to grade).

**Primary source: read back the posted Jira comment, not a fresh Q&A.** Nikita's
actual workflow is to edit the QA comment directly after testing — tick
✅/☑️/⚠️/⏩️ markers per step, add short notes, delete TCs that turned out
irrelevant. That edited comment is the real record; re-derive metrics from it
instead of re-asking what it already answers.

1. Fetch comments and find the QA one:
   ```bash
   workflow jira-comments {KEY}
   ```
   Match the comment containing `## Суть` / `P0 — Тест-кейсы` (posted by
   `/qa-analyze` step 8). If none found (never posted, or posted outside this
   flow) — fall back to asking the risk/bug-scope questions directly, same as
   before 2026-07-03.

2. Read `tasks/qa/{KEY}/analysis.md`'s `<!-- qa-meta -->` footer for the
   original `risk`, `mode`, `p0_count`/`p1_count`/`p2_count`.

3. Parse the fetched comment against the current marker legend (confirmed with
   Nikita 2026-07-03 — re-confirm if a new marker shows up that isn't here):
   - `✅` on a TC heading — that TC passed fully
   - `☑️` on a step — that step confirmed OK
   - `⚠️` on a step, usually followed by a free-text note — an issue or
     environment limitation was found there; the note is the detail
   - `⏩️` on a step — skipped / not verified this round
   - A TC present in `analysis.md`'s original P0/P1/P2 list but **absent**
     from the fetched comment — pruned as noise by Nikita

   Derive:
   - `Bug scope` — the priority tier (P0/P1/P2/outside) of any TC carrying a
     `⚠️` marker with a note describing an actual defect (not an environment
     note like "стенд не работает"); `none` if all markers are ✅/☑️.
   - `P0 caught bugs` — count of P0 TCs with a real-defect `⚠️`.
   - `Pruned count` — how many of the original P0/P1/P2 items are missing from
     the comment entirely. This is the real anchoring-calibration signal now
     that Step 8 has no fixed cap — a domain that's consistently pruned hard
     needs a narrower Step 8 selection, not a wider one.

4. Still ask directly (markers don't encode this):
   > "Риск-уровень **{Risk level set}** — по итогу: подтверждён / занижен / завышен?"
   - "занижен"/"завышен" → follow-up "какой уровень был бы верным?" →
     `Risk level correct?` = `no (was {level})`.

5. Append one row to the `## Metrics Log` table in `config/qa-eval-framework.md`:

   | Date | Task | Risk set | Risk correct? | P0 count | P0 bugs found | HYPOTHESIS total | HYPOTHESIS false | Hidden deps total | Hidden deps relevant | Usefulness |
   |---|---|---|---|---|---|---|---|---|---|---|
   | {TODAY} | {KEY} | {risk} | {yes / no (was X)} | {p0_count from footer} | {count from step 3} | — | — | — | — | — |

   Leave HYPOTHESIS/Hidden-deps/Usefulness columns as `—` for now (separate,
   not yet automated — see idea backlog). Also note `Pruned count` from step 3
   as a one-line addendum under the table row, not a formal column yet:
   `{TODAY} {KEY}: pruned {N} of {p0+p1+p2 total} scope items`.

6. **Keyword self-tuning signal.** Only if step 4 resolved to `no (was {level})`
   (risk underestimated): check whether the Jira task title already contained
   one of the Deep-trigger keywords in `.claude/skills/qa-analyze/SKILL.md` →
   step "2b. Mode pre-assessment". If not, ask:

   > "Риск был занижен, а в заголовке не было ни одного Deep-триггер-слова.
   > Стоит добавить слово из темы задачи в список триггеров? Если да — какое?
   > (или «нет»)"

   On a word → append it (lowercase, comma-separated) to that list. Confirm:
   "Добавлено в список Deep-триггеров: {word}." On "нет" → skip, no write.

---

### 4. Status transition

Always offer at the end:
```bash
workflow jira-transitions {KEY}
```
Show available transitions, ask which to apply:
```bash
workflow jira-transition {KEY} "<target status>"
```

## Rules

- Never auto-transition without explicit user confirmation
- Bug severity must reference a domain risk rule, not be invented
- Bug report is a draft — always show to user before posting
- JIRA_WRITE_ENABLED must be true for comments and transitions

## Fail-fast

```
BLOCKED_JIRA_WRITE_DISABLED
JIRA_WRITE_ENABLED is not set to true in config/jira/.env
Needed from user: enable Jira write or handle manually
```

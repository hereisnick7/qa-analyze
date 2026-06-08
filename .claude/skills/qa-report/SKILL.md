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

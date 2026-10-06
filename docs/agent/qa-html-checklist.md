# QA HTML checklist delivery

Alternative delivery format for `qa-impact-agent` test cases (Step 10): an
interactive HTML file (`<details>` cards per TC/SC, live checkboxes with
`localStorage` progress, P0/P1/P2/BLOCKED filters) instead of — never
*replacing* — the normal Jira-comment text delivery. Approved as the base
format for test-case delivery when a real hands-on test pass is expected.

## When this applies

**Explicit request only** — same gate as Step 10 itself. Trigger phrases:
"сделай html чек-лист", "выдай тест-план в html", "html тест-кейсы". Never the
default; a plain Jira comment (Step 8's normal flow) stays the default delivery
for test cases.

## Template + fill script

`.claude/skills/qa-analyze/assets/qa-checklist-template.html` — CSS/JS is
generic and byte-for-byte reusable across tasks. It is never regenerated from
scratch and never hand-edited by the agent — `fill_checklist.py` (next to it,
same `assets/` folder) reads the template, extracts its `<style>`/`<script>`
blocks programmatically, and stamps the TC/SC/checklist data into the
remaining placeholder sections. The agent's job is only to produce the JSON
data (schema below), never to write or copy any HTML/CSS/JS itself — that
keeps output tokens proportional to the actual test-case content instead of
the ~26KB of markup/styling around it (see
prompt-bloat discipline: keep the heavy
agent file lean, push mechanical/occasional logic elsewhere).

Self-tests: `.claude/skills/qa-analyze/assets/tests/test_fill_checklist.py`
(`python3 -m pytest .claude/skills/qa-analyze/assets/tests/test_fill_checklist.py -v`
— not part of the main `python -m pytest` sweep, this lives outside
`pyproject.toml`'s `testpaths`). Covers: balanced tags, byte-for-byte
`<style>`/`<script>` reuse, HTML-escaping of user text, missing-field
fail-fast, and the empty-data edge case.

**Checkbox/comment state is localStorage-only until exported.** While the user
is testing, ticks and the free-text comment live in the browser's
`localStorage`, keyed per task — they never travel with the file itself.
Re-attaching or re-sharing the same file elsewhere (e.g. to Jira) carries none
of it; discovered live (2026-09-15) when the user believed an
attached checklist carried their marks and it didn't. The template's "⬇
Скачать с отметками" button bakes current state directly into the markup
(`checked` attributes, textarea contents) and downloads a self-contained copy
— that exported file is what should be read back (by a human or an agent), not
the original delivered attachment.

**The template's leading instruction comment is dropped automatically** by
`fill_checklist.py` — replaced with a short one-line provenance comment. This
is handled by the script, not a manual agent step. (Why it matters: the
instruction comment's own prose contains literal text like `<style>` and
`<details` describing the tags — confirmed live on the first real run
(2026-09-07), when a naive `grep`/regex tag-count against a file still
carrying this comment reported a false mismatch. `fill_checklist.py`'s own
`verify_balanced()` always strips HTML comments before counting, for the same
reason.)

**The Step 8.5 Чек-лист section is mandatory in the HTML output too** — it has
its own placeholder section in the template, right after Summary. Skipping it
is a contract violation, not a style choice.

## Process (qa-impact-agent, on explicit HTML request)

1. Produce Step 10 test cases exactly as normal (same reasoning, same TC-NN /
   SC-NN content, same Step 8.5 Чек-лист) — the HTML request changes delivery
   format only, never the analysis itself.
2. Transcribe that same content into a JSON file at
   `tasks/qa/<KEY>/checklist-data.json`, matching the schema below field for
   field — this is a mechanical transcription of what Step 8.5/Step 10 already
   produced, not new analysis or new wording.
3. Run the fill script:
   ```bash
   python3 .claude/skills/qa-analyze/assets/fill_checklist.py \
     --data tasks/qa/<KEY>/checklist-data.json \
     --output tasks/qa/<KEY>/checklist.html
   ```
   The script fills the template, verifies balanced `<details>` tags and that
   no `{{placeholder}}` was left unfilled, and exits non-zero with a message on
   either failure — a bad/incomplete data file is a fail-fast error here, never
   a page handed back silently broken. On failure, fix the JSON (not the
   template) and re-run.
4. Report the file path back to the orchestrator/user. Does not open a
   browser, does not touch Jira itself — delivery is a separate, explicit step
   (below).

### Data schema (`checklist-data.json`)

Required top-level: `task_key`, `title`, `checklist`. Everything else optional
(omit a key or pass an empty list — the corresponding section is dropped, e.g.
no `blockers`/`open_questions`/`dev_po_questions` at all removes the whole
Блокеры section).

```json
{
  "task_key": "PAY-101",
  "title": "короткое название задачи",
  "sub_meta": "Deep · MR backend!512 · собрано 2026-09-15",
  "summary_rows": [["Test Cases", "12 (P0: 5, P1: 4, P2: 3)"]],
  "summary_key_point": "одна мысль, что меняет план",
  "checklist": {
    "code_touches": "...", "essence": "...",
    "items": [{"text": "...", "note": "← подозреваемый баг (optional)"}]
  },
  "blockers": ["что и почему"],
  "open_questions": ["вопрос, не тест-кейс"],
  "dev_po_questions": [{"who": "Dev", "text": "...", "ref": "TC-03"}],
  "test_cases": [{
    "id": "TC-01", "type": "Positive", "title": "...",
    "priority": "P0", "blocked": "false", "blocked_reason": "(if blocked/partial)",
    "ac_ref": "...", "category": "...", "covers": "[FACT] file.py:88 — ...",
    "intro": "...", "accounts": ["..."], "preconditions": ["..."],
    "steps": [{"action": "...", "expected": "...", "note": "(optional)"}],
    "where": "...", "cleanup": "..."
  }],
  "scenarios": [{
    "id": "SC-01", "title": "...", "priority": "P0", "blocked": "false",
    "ac_ref": "...", "category": "...", "shared": "общий таймер/стенд",
    "intro": "...",
    "instances": [{"label": "A", "path": "...", "desc": "...", "covers": "..."}],
    "steps": [{"tags": ["A", "B"], "action": "...", "expected": "...", "note": "(optional)"}],
    "where": "...", "cleanup": "..."
  }],
  "appendices": [{"title": "...", "content": "..."}],
  "sources": "analysis.md, MR ...",
  "jira_note": "если Jira не трогалась — сказать явно"
}
```

`type` for a TC is free text (`Positive`/`Negative`/`RBAC`/`API` per Step 10's
own format) — only `Positive`/`Negative`/`Regression` get a colored badge from
the template's CSS today; `RBAC`/`API` render as a plain badge (cosmetic gap,
not a rendering error — extend `.badge.*` in the template's `<style>` if this
needs closing later).

## Delivery to Jira

Jira comments do **not** render arbitrary HTML/CSS/JS (sanitized) — the file
must go out as an **attachment**, never pasted into a comment body.

- `workflow jira-attach <KEY> <file_path> --dry-run` first — show the user the
  file name/size, per the standard "preview before write" rule
  ([`jira-policy.md`](jira-policy.md)).
- On confirmation, `workflow jira-attach <KEY> <file_path>` (requires
  `JIRA_WRITE_ENABLED=true` + explicit user request, same guard as every other
  Jira write).
- Still post the normal Step 8/SKILL.md text comment (Суть + тест-кейсы) —
  the attachment is an addition for hands-on testing, not a replacement for
  the Jira-native summary other stakeholders read inline.

## Non-goals

- Not wired into the autonomous tail — like `code-tester`, this only runs on
  direct request.
- The fill script (`fill_checklist.py`, added 2026-09-15) only renders —
  it never invents content. Any TC/SC/checklist text not already produced by
  Step 8.5/Step 10 does not belong in the JSON data; garbage in, garbage out
  applies same as before.

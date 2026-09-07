# QA HTML checklist delivery

Alternative delivery format for `qa-impact-agent` test cases (Step 10): an
interactive HTML file (`<details>` cards per TC/SC, live checkboxes with
`localStorage` progress, P0/P1/P2/BLOCKED filters) instead of — never
*replacing* — the normal Jira-comment text delivery.

## When this applies

**Explicit request only** — same gate as Step 10 itself. Trigger phrases:
"сделай html чек-лист", "выдай тест-план в html", "html тест-кейсы". Never the
default; a plain Jira comment (the normal flow) stays the default delivery
for test cases.

## Template

`.claude/skills/qa-analyze/assets/qa-checklist-template.html` — CSS/JS is
generic and byte-for-byte reusable across tasks; do not regenerate it from
scratch. The file's own leading HTML comment documents exactly how to fill it
(copy the `TC EXAMPLE` / `SC EXAMPLE` / appendix blocks, keep every `details`
tag balanced, never touch the `<style>`/`<script>` blocks). `qa-impact-agent`
reads this file and follows its embedded instructions — the fill-in mechanics
live there, not duplicated in the agent's own prompt.

**Drop the template's leading instruction comment from the filled output** —
replace it with a short one-line provenance comment (source analysis file,
template path). Confirmed live on the first real run: the instruction comment
itself contains literal text like `<style>` and `<details` (it's describing
the tags in prose) — a naive `grep`/regex tag-count run against a file that
still has this comment, or against the template itself for a CSS/JS diff,
reports false mismatches. Always strip HTML comments first before counting or
diffing:
```python
import re
clean = re.sub(r'<!--.*?-->', '', html, flags=re.S)
```

**The "Чек-лист" section is mandatory in the HTML output too** — it has its
own placeholder section in the template, right after Summary. Skipping it is
a contract violation, not a style choice.

## Process (qa-impact-agent, on explicit HTML request)

1. Produce the test cases exactly as normal (same reasoning, same TC-NN /
   SC-NN content, same Чек-лист) — the HTML request changes delivery format
   only, never the analysis itself.
2. Read the template, fill Summary / Чек-лист / Блокеры (if any) / TC and SC
   cards / appendices per its inline instructions.
3. Write the filled file to a local path (e.g. `tasks/qa/<KEY>/checklist.html`
   or wherever your workflow keeps per-task working files).
4. **Verify before handing back** — unbalanced tags silently break the page.
   Strip comments first (see above) or the leading provenance comment can
   itself skew the count:
   ```bash
   python3 -c "
   import re
   c = open('checklist.html', encoding='utf-8').read()
   c = re.sub(r'<!--.*?-->', '', c, flags=re.S)
   opens = len(re.findall(r'<details', c))
   closes = len(re.findall(r'</details>', c))
   assert opens == closes, f'unbalanced details tags: {opens} open vs {closes} close'
   "
   ```
5. Report the file path back to the user. Does not open a browser, does not
   touch Jira itself — delivery is a separate, explicit step (below).

## Delivery to Jira

Jira comments do **not** render arbitrary HTML/CSS/JS (sanitized) — the file
must go out as an **attachment**, never pasted into a comment body.

- `workflow jira-attach <KEY> <file_path> --dry-run` first — always preview
  (filename/size) before any write.
- On confirmation, `workflow jira-attach <KEY> <file_path>` (requires
  `JIRA_WRITE_ENABLED=true`, same guard as every other Jira write command in
  this package).
- Still post the normal text comment (Суть + тест-кейсы) — the attachment is
  an addition for hands-on testing, not a replacement for the Jira-native
  summary other stakeholders read inline.

## Non-goals

- Not automatic — like full test-case generation, this only runs on direct
  request.
- No mechanical markdown→HTML render script exists. Today the agent fills the
  template directly by reading and editing it — acceptable at moderate
  volume, revisit if this becomes frequent enough to need automation.

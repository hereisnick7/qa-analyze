#!/usr/bin/env python3
"""Deterministic filler for qa-checklist-template.html.

Takes structured JSON (the same TC-NN/SC-NN/Step-8.5 content qa-impact-agent
already produces as plain text for Step 10) and stamps it into the static
template. The template's <style>/<script> blocks are extracted from the
template file and reused byte-for-byte — never retyped — so the only tokens
the calling agent spends are the actual test-case data, not HTML/CSS/JS
boilerplate. See docs/agent/qa-html-checklist.md.

Usage:
    python3 fill_checklist.py --data checklist-data.json --output checklist.html
    python3 fill_checklist.py --data checklist-data.json --output checklist.html --template other-template.html

Exits non-zero (with a message) on missing required fields or a broken
template (unbalanced <details> tags after fill) — fail fast, never hand back
a silently-broken page.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "qa-checklist-template.html"

REQUIRED_TOP_LEVEL = ("task_key", "title", "checklist")


def esc(value):
    return html.escape(str(value), quote=False)


def replace_section(text, section_id, inner_html):
    pattern = re.compile(r'<section id="%s">.*?</section>' % re.escape(section_id), re.S)
    replacement = '<section id="%s">\n%s\n</section>' % (section_id, inner_html)
    new_text, n = pattern.subn(lambda m: replacement, text, count=1)
    if n != 1:
        raise ValueError("template missing section id=%r" % section_id)
    return new_text


def remove_section(text, section_id):
    pattern = re.compile(r'\s*<section id="%s">.*?</section>' % re.escape(section_id), re.S)
    new_text, n = pattern.subn("", text, count=1)
    if n != 1:
        raise ValueError("template missing section id=%r" % section_id)
    return new_text


# ---------------------------------------------------------------------------
# Renderers — each returns a fragment matching the template's own hand-authored
# patterns (see the template's leading comment / TC EXAMPLE / SC EXAMPLE).
# ---------------------------------------------------------------------------

def render_step(step):
    action = esc(step.get("action", ""))
    expected = esc(step.get("expected", ""))
    tags = step.get("tags")
    prefix = "[%s] " % ",".join(tags) if tags else ""
    note = step.get("note")
    note_html = '\n<div class="note">↳ %s</div>' % esc(note) if note else ""
    return (
        '<li><label><input type="checkbox"> %s%s → %s</label>%s</li>'
        % (prefix, action, expected, note_html)
    )


def render_bullets(items):
    if not items:
        return "<li>—</li>"
    return "\n".join("<li>%s</li>" % esc(item) for item in items)


def render_blocked_banner(entity):
    blocked = entity.get("blocked", "false")
    if blocked not in ("true", "partial"):
        return ""
    reason = entity.get("blocked_reason", "")
    label = "🚫 BLOCKED" if blocked == "true" else "🚫 PARTIALLY BLOCKED"
    return '<div class="blocked-banner">%s: %s</div>\n' % (label, esc(reason))


def render_tc(tc):
    tc_id = esc(tc["id"])
    tc_type = tc.get("type", "Positive")
    type_class = tc_type.lower()
    priority = tc.get("priority", "P1")
    priority_class = priority.lower()
    blocked = tc.get("blocked", "false")
    accounts = render_bullets(tc.get("accounts"))
    preconditions = render_bullets(tc.get("preconditions"))
    steps = "\n".join(render_step(s) for s in tc.get("steps", []))
    return """<details class="tc" data-priority="{priority}" data-blocked="{blocked}">
<summary><span class="badge {priority_class}">{priority}</span><span class="badge {type_class}">{tc_type}</span><span class="tcid">{tc_id}</span><span class="tctitle">{title}</span><span class="tcprogress"></span><span class="arrow">▶</span></summary>
<div class="tc-body">
{blocked_banner}<p class="meta"><strong>AC:</strong> {ac_ref} · <strong>Category:</strong> {category}</p>
<p class="covers"><strong>Covers:</strong> {covers}</p>
<p class="intro">{intro}</p>
<div class="accounts"><strong>Аккаунты/данные:</strong><ul>{accounts}</ul></div>
<div class="preconditions"><strong>Предусловия:</strong><ul>{preconditions}</ul></div>
<ol class="steps">
{steps}
</ol>
<p class="where"><strong>Где проверять:</strong> {where}.</p>
<p class="cleanup"><strong>Cleanup:</strong> {cleanup}.</p>
<div class="comment-block"><label>💬 Комментарий (заполняется во время прогона):</label><textarea placeholder="Свободный текст — наблюдение, факт, отклонение от ожидания, ссылка на баг…"></textarea></div>
</div>
</details>""".format(
        priority=esc(priority),
        priority_class=priority_class,
        blocked=esc(blocked),
        type_class=type_class,
        tc_type=esc(tc_type),
        tc_id=tc_id,
        title=esc(tc.get("title", "")),
        blocked_banner=render_blocked_banner(tc),
        ac_ref=esc(tc.get("ac_ref", "")),
        category=esc(tc.get("category", "")),
        covers=esc(tc.get("covers", "")),
        intro=esc(tc.get("intro", "")),
        accounts=accounts,
        preconditions=preconditions,
        steps=steps,
        where=esc(tc.get("where", "")),
        cleanup=esc(tc.get("cleanup", "")),
    )


def render_instance(instance):
    return '<div class="instance-block"><h4>Instance %s — %s</h4>\n<p style="font-size:12.5px;">%s<br>Covers: %s</p></div>' % (
        esc(instance.get("label", "")),
        esc(instance.get("path", "")),
        esc(instance.get("desc", "")),
        esc(instance.get("covers", "")),
    )


def render_sc(sc):
    sc_id = esc(sc["id"])
    priority = sc.get("priority", "P1")
    priority_class = priority.lower()
    blocked = sc.get("blocked", "false")
    instances = "\n\n".join(render_instance(i) for i in sc.get("instances", []))
    steps = "\n".join(render_step(s) for s in sc.get("steps", []))
    return """<details class="tc" data-priority="{priority}" data-blocked="{blocked}">
<summary><span class="badge {priority_class}">{priority}</span><span class="badge scenario">Scenario</span><span class="tcid">{sc_id}</span><span class="tctitle">{title}</span><span class="tcprogress"></span><span class="arrow">▶</span></summary>
<div class="tc-body">
{blocked_banner}<p class="meta"><strong>Type:</strong> Scenario — батчит {n} инстансов, делящих {shared} · <strong>AC:</strong> {ac_ref} · <strong>Category:</strong> {category}</p>
<p class="intro">{intro}</p>

{instances}

<ol class="steps">
{steps}
</ol>
<p class="where"><strong>Где проверять:</strong> {where}.</p>
<p class="cleanup"><strong>Cleanup:</strong> {cleanup}.</p>
<div class="comment-block"><label>💬 Комментарий (заполняется во время прогона):</label><textarea placeholder="Свободный текст — наблюдение, факт, отклонение от ожидания, ссылка на баг…"></textarea></div>
</div>
</details>""".format(
        priority=esc(priority),
        priority_class=priority_class,
        blocked=esc(blocked),
        sc_id=sc_id,
        title=esc(sc.get("title", "")),
        blocked_banner=render_blocked_banner(sc),
        n=len(sc.get("instances", [])),
        shared=esc(sc.get("shared", "")),
        ac_ref=esc(sc.get("ac_ref", "")),
        category=esc(sc.get("category", "")),
        intro=esc(sc.get("intro", "")),
        instances=instances,
        steps=steps,
        where=esc(sc.get("where", "")),
        cleanup=esc(sc.get("cleanup", "")),
    )


def cards_for_priority(data, priority):
    cards = []
    for tc in data.get("test_cases", []):
        if tc.get("priority") == priority:
            cards.append(render_tc(tc))
    for sc in data.get("scenarios", []):
        if sc.get("priority") == priority:
            cards.append(render_sc(sc))
    if not cards:
        return "<!-- no %s test cases/scenarios -->" % priority
    return "\n\n".join(cards)


def render_checklist_item(item):
    text = esc(item.get("text", ""))
    note = item.get("note")
    note_html = '\n<div class="note">↳ %s</div>' % esc(note) if note else ""
    return '<li><label><input type="checkbox"> %s</label>%s</li>' % (text, note_html)


def render_summary_rows(rows):
    if not rows:
        return "<tr><td>—</td><td>—</td></tr>"
    return "\n".join(
        "<tr><td>%s</td><td>%s</td></tr>" % (esc(k), esc(v)) for k, v in rows
    )


def render_open_question(text):
    return '<div class="card open-q"><strong>❓ OPEN QUESTION — не тест-кейс, не баг:</strong> %s</div>' % esc(text)


def render_dev_po_question(q):
    return "<li><strong>%s:</strong> %s%s</li>" % (
        esc(q.get("who", "")),
        esc(q.get("text", "")),
        " (%s)" % esc(q["ref"]) if q.get("ref") else "",
    )


def render_appendix(appendix):
    return '<details class="appendix">\n<summary>Приложение — %s</summary>\n<div class="inner">\n<p>%s</p>\n</div>\n</details>' % (
        esc(appendix.get("title", "")),
        appendix.get("content", ""),  # allowed to carry pre-approved inline HTML (tables etc.)
    )


# ---------------------------------------------------------------------------


def build(data, template_text):
    missing = [k for k in REQUIRED_TOP_LEVEL if k not in data]
    if missing:
        raise ValueError("missing required top-level field(s): %s" % ", ".join(missing))

    task_key = data["task_key"]
    title = data["title"]

    text = template_text

    # 1. Drop the big leading instructional comment, replace with a one-line
    #    provenance note (docs/agent/qa-html-checklist.md: "Drop the template's
    #    leading instruction comment from the filled output").
    provenance = "<!-- Filled by fill_checklist.py from checklist data for %s — do not hand-edit; regenerate from source data instead. -->" % esc(task_key)
    text, n = re.subn(r"^<!--.*?-->\s*", provenance + "\n", text, count=1, flags=re.S)
    if n != 1:
        raise ValueError("template missing leading instruction comment")

    # 2. <title>
    text, n = re.subn(
        r"<title>.*?</title>",
        "<title>%s — %s, тест-план</title>" % (esc(task_key), esc(title)),
        text,
        count=1,
    )
    if n != 1:
        raise ValueError("template missing <title>")

    # 3. header h1 + sub
    text, n = re.subn(
        r"<h1>.*?</h1>",
        "<h1>%s — %s, тест-план</h1>" % (esc(task_key), esc(title)),
        text,
        count=1,
    )
    if n != 1:
        raise ValueError("template missing header h1")

    text, n = re.subn(
        r'<div class="sub">.*?</div>',
        '<div class="sub">%s</div>' % esc(data.get("sub_meta", "")),
        text,
        count=1,
    )
    if n != 1:
        raise ValueError("template missing header .sub")

    # 4. Summary section
    summary_inner = (
        '<div class="card">\n<table class="plain">\n<tr><th>Показатель</th><th>Значение</th></tr>\n%s\n</table>\n<p style="margin-top:12px;font-size:13px;"><strong>Ключевое:</strong> %s</p>\n</div>'
        % (render_summary_rows(data.get("summary_rows")), esc(data.get("summary_key_point", "")))
    )
    text = replace_section(text, "summary", '<h2 class="section-title">Сводка</h2>\n' + summary_inner)

    # 5. Step 8.5 checklist — mandatory
    checklist = data["checklist"]
    items = "\n".join(render_checklist_item(i) for i in checklist.get("items", []))
    checklist_inner = (
        '<h2 class="section-title">☑️ Чек-лист (быстрый прогон)</h2>\n'
        '<div class="card">\n<p class="meta"><strong>Код трогает:</strong> %s</p>\n'
        '<p class="meta"><strong>Суть задачи:</strong> %s</p>\n'
        '<ol class="steps" style="list-style:none;margin:10px 0 0;padding:0;">\n%s\n</ol>\n</div>'
        % (esc(checklist.get("code_touches", "")), esc(checklist.get("essence", "")), items)
    )
    text = replace_section(text, "checklist85", checklist_inner)

    # 6. Blockers / open questions / dev-po questions — optional, drop section if all empty
    blockers = data.get("blockers") or []
    open_questions = data.get("open_questions") or []
    dev_po_questions = data.get("dev_po_questions") or []
    if not (blockers or open_questions or dev_po_questions):
        text = remove_section(text, "blockers")
    else:
        parts = ['<h2 class="section-title">Блокеры и открытые вопросы</h2>']
        if blockers:
            banners = "\n".join('<div class="blocked-banner">🚫 BLOCKED: %s</div>' % esc(b) for b in blockers)
            parts.append('<div class="card">\n%s\n</div>' % banners)
        for q in open_questions:
            parts.append(render_open_question(q))
        if dev_po_questions:
            dev_po_items = "\n".join(render_dev_po_question(q) for q in dev_po_questions)
            parts.append(
                '<details class="appendix">\n<summary>Вопросы → Dev / PO</summary>\n<div class="inner">\n<ul>\n%s\n</ul>\n</div>\n</details>'
                % dev_po_items
            )
        text = replace_section(text, "blockers", "\n".join(parts))

    # 7. P0/P1/P2 sections
    text = replace_section(
        text, "p0-section", '<h2 class="section-title">P0 — обязательно</h2>\n\n%s' % cards_for_priority(data, "P0")
    )
    text = replace_section(
        text, "p1-section", '<h2 class="section-title">P1 — рекомендовано</h2>\n\n%s' % cards_for_priority(data, "P1")
    )
    text = replace_section(
        text, "p2-section", '<h2 class="section-title">P2 — опционально</h2>\n\n%s' % cards_for_priority(data, "P2")
    )

    # 8. Appendices (evidence, resolved TBDs) — standalone example block before </main>
    appendix_pattern = re.compile(
        r'<details class="appendix">\s*<summary>Приложение — \{\{название\}\}</summary>.*?</details>\s*(?=</main>)',
        re.S,
    )
    appendices = data.get("appendices") or []
    replacement = ("\n\n".join(render_appendix(a) for a in appendices) + "\n\n") if appendices else ""
    text, n = appendix_pattern.subn(replacement, text, count=1)
    if n != 1:
        raise ValueError("template missing appendix example block")

    # 9. Footer sources line
    text, n = re.subn(
        r"\{\{пути к analysis\.md / трекеру / другим файлам\}\}",
        esc(data.get("sources", "")),
        text,
        count=1,
    )
    if n != 1:
        raise ValueError("template missing footer sources placeholder")
    text, n = re.subn(
        r"\{\{если Jira не трогалась — сказать явно\}\}",
        esc(data.get("jira_note", "")),
        text,
        count=1,
    )
    if n != 1:
        raise ValueError("template missing footer jira-note placeholder")

    # 10. localStorage key — only dynamic piece of the otherwise byte-for-byte script block
    text, n = re.subn(
        r"'\{\{TASK_KEY\}\}-checklist-v1'",
        "'%s-checklist-v1'" % esc(task_key),
        text,
        count=1,
    )
    if n != 1:
        raise ValueError("template missing STORAGE_KEY placeholder")

    return text


def verify_balanced(text):
    clean = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    opens = len(re.findall(r"<details", clean))
    closes = len(re.findall(r"</details>", clean))
    if opens != closes:
        raise ValueError("unbalanced <details> tags: %d open vs %d close" % (opens, closes))
    leftover = re.findall(r"\{\{[^}]*\}\}", clean)
    if leftover:
        raise ValueError("unfilled {{placeholder}}(s) remain: %s" % ", ".join(sorted(set(leftover))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, help="path to checklist data JSON")
    parser.add_argument("--output", required=True, help="path to write the filled HTML")
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="template path (default: qa-checklist-template.html next to this script)")
    args = parser.parse_args(argv)

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    template_text = Path(args.template).read_text(encoding="utf-8")

    html_out = build(data, template_text)
    verify_balanced(html_out)

    Path(args.output).write_text(html_out, encoding="utf-8")
    print("wrote %s (%d bytes)" % (args.output, len(html_out.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Generate index.html from README.md using template.html.

README.md is the single source of truth for course content. Design/layout lives
in template.html. This script is run by .github/workflows/build-site.yml on every
push that touches README.md, and commits the regenerated index.html back to main.

Standard library only. Fails loudly (non-zero exit) if an expected section is
missing, so a broken README never publishes a broken page.
"""

from __future__ import annotations

import datetime
import html
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
TEMPLATE = ROOT / "template.html"
OUTPUT = ROOT / "index.html"

THAI_RE = re.compile(r"[฀-๿]")
PDF_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
    '<path d="M14 2v6h6"/></svg>'
)


def die(msg: str) -> "None":
    sys.stderr.write(f"build_site: {msg}\n")
    raise SystemExit(1)


def is_thai(text: str) -> bool:
    return bool(THAI_RE.search(text))


def th_class(text: str) -> str:
    return ' class="th"' if is_thai(text) else ""


def inline_md(text: str) -> str:
    """Convert a small subset of inline markdown to HTML, escaping the rest."""
    # Protect links first: [label](url)
    links: list[str] = []

    def stash_link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        safe_url = html.escape(url, quote=True)
        safe_label = html.escape(label)
        ext = ' target="_blank" rel="noopener"' if url.startswith("http") else ""
        links.append(f'<a href="{safe_url}"{ext}>{safe_label}</a>')
        return f"\x00{len(links) - 1}\x00"

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", stash_link, text)
    text = html.escape(text)
    # Bare URLs
    text = re.sub(
        r"(?<!\x00)(https?://[^\s<]+)",
        lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        text,
    )
    # Bold
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # Restore links
    text = re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], text)
    return text


def split_sections(md: str) -> tuple[str, dict[str, str]]:
    """Return (preamble, {section_title: body}) splitting on '## ' headings."""
    parts = re.split(r"^## +(.+?)\s*$", md, flags=re.MULTILINE)
    preamble = parts[0]
    sections: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        sections[parts[i].strip()] = parts[i + 1]
    return preamble, sections


def find_section(sections: dict[str, str], *needles: str) -> tuple[str, str]:
    for title, body in sections.items():
        low = title.lower()
        if all(n.lower() in low for n in needles):
            return title, body
    die(f"section matching {needles!r} not found in README.md")


def blocks(body: str) -> list[list[str]]:
    """Split a section body into blank-line-separated blocks of non-empty lines."""
    out: list[list[str]] = []
    cur: list[str] = []
    for line in body.splitlines():
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def parse_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    rows = [
        [c.strip() for c in ln.strip().strip("|").split("|")]
        for ln in lines
        if ln.lstrip().startswith("|")
    ]
    rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
    if len(rows) < 2:
        die("expected a markdown table with a header and at least one row")
    return rows[0], rows[1:]


def is_table_block(block: list[str]) -> bool:
    return sum(1 for ln in block if ln.lstrip().startswith("|")) >= 2


def render_table(header: list[str], body_rows: list[list[str]], cls: str = "") -> str:
    cls_attr = f' class="{cls}"' if cls else ""
    head = "".join(f"<th{th_class(h)}>{inline_md(h)}</th>" for h in header)
    body = ""
    for row in body_rows:
        cells = "".join(f"<td{th_class(c)}>{inline_md(c)}</td>" for c in row)
        body += f"          <tr>{cells}</tr>\n"
    return (
        f'        <table{cls_attr}>\n'
        f'          <thead><tr>{head}</tr></thead>\n'
        f'          <tbody>\n{body}          </tbody>\n'
        f'        </table>'
    )


def paragraphs(bodys: list[list[str]], indent: str = "      ") -> str:
    out = []
    for block in bodys:
        text = " ".join(block)
        out.append(f'{indent}<p{th_class(text)}>{inline_md(text)}</p>')
    return "\n".join(out)


def build_description(sections: dict[str, str]) -> str:
    _, body = find_section(sections, "course description")
    return paragraphs(blocks(body))


def build_schedule(sections: dict[str, str]) -> str:
    _, body = find_section(sections, "semester")
    for block in blocks(body):
        if is_table_block(block):
            return render_table(*parse_table(block))
    die("no schedule table under the Semester section")


def build_clos(sections: dict[str, str]) -> str:
    _, body = find_section(sections, "learning outcome")
    items = []
    for line in body.splitlines():
        m = re.match(r"\s*\d+\.\s*(CLO\d+)\s+(.*\S)\s*$", line)
        if m:
            tag, text = m.group(1), m.group(2)
            items.append(
                f'        <li><span class="clo-tag">{tag}</span>'
                f'<span{th_class(text)}>{inline_md(text)}</span></li>'
            )
    if not items:
        die("no 'N. CLOx ...' items found under Course Learning Outcomes")
    return "\n".join(items)


def build_chapters(sections: dict[str, str]) -> str:
    _, body = find_section(sections, "class materials")
    header = None
    rows = []
    for block in blocks(body):
        if is_table_block(block):
            header, rows = parse_table(block)
            break
    if not rows:
        die("no materials table under Class Materials")
    cards = []
    for row in rows:
        if len(row) < 3:
            die(f"materials row has fewer than 3 columns: {row!r}")
        badge, title, material = row[0], row[1], row[2]
        links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", material)
        if not links:
            die(f"no links in materials cell: {material!r}")
        btns = "\n".join(
            f'          <a class="doc-link" href="{html.escape(url, quote=True)}" '
            f'target="_blank" rel="noopener">{PDF_ICON}{html.escape(label)}</a>'
            for label, url in links
        )
        cards.append(
            f'      <article class="chapter">\n'
            f'        <span class="chapter__badge">{html.escape(badge)}</span>\n'
            f'        <div class="chapter__title{(" th" if is_thai(title) else "")}">{inline_md(title)}</div>\n'
            f'        <div class="chapter__links">\n{btns}\n        </div>\n'
            f'      </article>'
        )
    return "\n".join(cards)


def build_grading(sections: dict[str, str]) -> str:
    _, body = find_section(sections, "grading")
    pre: list[list[str]] = []
    table_html = None
    post: list[list[str]] = []
    for block in blocks(body):
        if is_table_block(block):
            table_html = render_table(*parse_table(block), cls="grades")
        elif table_html is None:
            pre.append(block)
        else:
            post.append(block)
    if table_html is None:
        die("no grading table under Grading")
    card1 = (
        '    <div class="card">\n'
        + (paragraphs(pre) + "\n" if pre else "")
        + '      <div class="table-scroll">\n'
        + table_html
        + "\n      </div>\n"
        + (paragraphs(post[:1]) + "\n" if post[:1] else "")
        + "    </div>"
    )
    blocks_out = [card1]
    if len(post) > 1:
        blocks_out.append(
            '    <div class="card prose">\n' + paragraphs(post[1:]) + "\n    </div>"
        )
    return "\n".join(blocks_out)


def build_references(sections: dict[str, str]) -> str:
    _, body = find_section(sections, "reference")
    items = []
    for line in body.splitlines():
        m = re.match(r"\s*[-*]\s+(.*\S)\s*$", line)
        if m:
            text = m.group(1)
            items.append(f'        <li{th_class(text)}>{inline_md(text)}</li>')
    if not items:
        die("no list items under References")
    return "\n".join(items)


def main() -> None:
    for path in (README, TEMPLATE):
        if not path.exists():
            die(f"missing {path.name}")
    md = README.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    preamble, sections = split_sections(md)

    h1 = re.search(r"^#\s+(.+?)\s*$", preamble, flags=re.MULTILINE)
    if not h1:
        die("no top-level '# ' heading in README.md")
    title_en = h1.group(1).strip()

    code_m = re.search(r"(RMUTT\s+\d+).*?\(([^)]+)\)", preamble)
    if not code_m:
        die("could not find 'RMUTT <code> ... (<thai name>)' line in README.md")
    course_code = re.sub(r"\s+", " ", code_m.group(1)).strip()
    title_th = code_m.group(2).strip()

    lect_m = re.search(r"Lecturer:\s*(.+?)\s*\(([^)]+)\)", preamble)
    if not lect_m:
        die("could not find 'Lecturer: <name> (<email>)' line in README.md")
    lecturer_name = lect_m.group(1).strip()
    lecturer_email = lect_m.group(2).strip()

    semester_title, _ = find_section(sections, "semester")

    values = {
        "TITLE_EN": html.escape(title_en),
        "TITLE_TH": html.escape(title_th),
        "COURSE_CODE": html.escape(course_code),
        "SEMESTER": html.escape(semester_title),
        "LECTURER_NAME": html.escape(lecturer_name),
        "LECTURER_EMAIL": html.escape(lecturer_email, quote=True),
        "DESCRIPTION_PARAS": build_description(sections),
        "SCHEDULE_TABLE": build_schedule(sections),
        "CLO_ITEMS": build_clos(sections),
        "CHAPTER_CARDS": build_chapters(sections),
        "GRADING_BLOCK": build_grading(sections),
        "REFERENCES_ITEMS": build_references(sections),
        "BUILD_DATE": datetime.date.today().isoformat(),
    }

    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", val)

    leftover = re.findall(r"\{\{([A-Z_]+)\}\}", out)
    if leftover:
        die(f"unfilled placeholders remain: {sorted(set(leftover))}")

    OUTPUT.write_text(out, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(out)} bytes)")


if __name__ == "__main__":
    main()

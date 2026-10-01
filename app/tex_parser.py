"""Locate the editable parts of a LaTeX resume: name, bullets and skills lines.

Everything is tracked by character span in the original source so tailoring can
replace just those spans and leave the rest of the file byte-identical.
"""
import re
from dataclasses import dataclass, field

from .latex_utils import inline_text, latex_to_text, mask_comments, match_brace


@dataclass
class Bullet:
    id: str
    start: int  # span of the bullet's content in the source
    end: int
    section: str
    text: str  # plain-text rendering


@dataclass
class SkillLine:
    id: str
    start: int  # span of the item list (e.g. "Java, Python, SQL")
    end: int
    section: str
    label: str
    text: str


@dataclass
class ParsedResume:
    source: str
    first_name: str
    last_name: str
    bullets: list[Bullet] = field(default_factory=list)
    skills: list[SkillLine] = field(default_factory=list)
    body_start: int = 0

    def bullet(self, bid: str) -> Bullet | None:
        return next((b for b in self.bullets if b.id == bid), None)

    def skill(self, sid: str) -> SkillLine | None:
        return next((s for s in self.skills if s.id == sid), None)

    def plain_text(self) -> str:
        return latex_to_text(self.source[self.body_start:])


_SECTION_RE = re.compile(r"\\(?:section|cvsection|resumeSection)\*?\s*\{")
_SKILL_SECTION_RE = re.compile(r"skill|technolog|tools|competenc|proficienc", re.I)
_RESUME_ITEM_RE = re.compile(r"\\resumeItem(?![A-Za-z])\s*\{")
_ITEM_RE = re.compile(r"\\item(?![A-Za-z])(\s*\[[^\]]*\])?")
_ITEM_STOP_RE = re.compile(r"\\(?:item|end|begin|resume[A-Za-z]*)(?![A-Za-z])")
_TEXTBF_RE = re.compile(r"\\textbf\s*\{")


def parse_resume(src: str) -> ParsedResume:
    masked = mask_comments(src)
    m = re.search(r"\\begin\{document\}", masked)
    body_start = m.end() if m else 0

    sections = _find_sections(src, masked, body_start)

    def section_at(pos: int) -> str:
        name = ""
        for spos, title in sections:
            if spos <= pos:
                name = title
            else:
                break
        return name

    def is_skills(pos: int) -> bool:
        return bool(_SKILL_SECTION_RE.search(section_at(pos)))

    first, last = _find_name(src, masked, body_start)
    bullets = _find_bullets(src, masked, body_start, section_at, is_skills)
    skills = _find_skills(src, masked, body_start, sections, section_at)
    return ParsedResume(src, first, last, bullets, skills, body_start)


def _find_sections(src, masked, body_start):
    out = []
    for m in _SECTION_RE.finditer(masked, body_start):
        close = match_brace(masked, m.end() - 1)
        if close == -1:
            continue
        out.append((m.start(), inline_text(src[m.end():close])))
    return out


def _find_name(src, masked, body_start) -> tuple[str, str]:
    # moderncv style \name{First}{Last}, or \name{Full Name}
    m = re.search(r"\\name\s*\{", masked)
    if m:
        close = match_brace(masked, m.end() - 1)
        if close != -1:
            first = inline_text(src[m.end():close])
            rest = masked[close + 1:close + 3].lstrip()
            if rest.startswith("{"):
                o2 = masked.index("{", close + 1)
                c2 = match_brace(masked, o2)
                if c2 != -1:
                    return first, inline_text(src[o2 + 1:c2])
            return _split_name(first)

    # Jake's template: \textbf{\Huge \scshape First Last}
    head = masked[body_start:body_start + 3000]
    m = re.search(r"\\(?:Huge|huge|LARGE|Large)(?![A-Za-z])", head)
    if m:
        abs_pos = body_start + m.start()
        open_idx = masked.rfind("{", body_start, abs_pos)
        if open_idx != -1:
            close = match_brace(masked, open_idx)
            if close > abs_pos:
                name = inline_text(src[open_idx + 1:close])
                if name:
                    return _split_name(name)

    m = re.search(r"\\author\s*\{", masked)
    if m:
        close = match_brace(masked, m.end() - 1)
        if close != -1:
            return _split_name(inline_text(src[m.end():close]))

    for line in latex_to_text(src[body_start:]).splitlines():
        if line.strip():
            return _split_name(line.split("|")[0])
    return "", ""


def _split_name(name: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _find_bullets(src, masked, body_start, section_at, is_skills) -> list[Bullet]:
    spans: list[tuple[int, int]] = []

    for m in _RESUME_ITEM_RE.finditer(masked, body_start):
        close = match_brace(masked, m.end() - 1)
        if close != -1:
            spans.append((m.end(), close))

    for m in _ITEM_RE.finditer(masked, body_start):
        pos = m.end()
        while pos < len(masked) and masked[pos] in " \t":
            pos += 1
        if pos < len(masked) and masked[pos] == "{":
            close = match_brace(masked, pos)
            if close == -1:
                continue
            start, end = pos + 1, close
        else:
            stop = _ITEM_STOP_RE.search(masked, pos)
            start, end = pos, stop.start() if stop else len(masked)
            while end > start and masked[end - 1] in " \t\r\n":
                end -= 1
        spans.append((start, end))

    spans.sort()
    bullets: list[Bullet] = []
    last_end = -1
    for start, end in spans:
        if start < last_end:  # nested inside an earlier bullet
            continue
        raw = masked[start:end].strip()
        if not raw or raw.startswith("\\resume") or is_skills(start):
            continue
        text = inline_text(src[start:end])
        if len(text.split()) < 3:
            continue
        bullets.append(Bullet(f"b{len(bullets) + 1}", start, end, section_at(start), text))
        last_end = end
    return bullets


def _find_skills(src, masked, body_start, sections, section_at) -> list[SkillLine]:
    ranges = []
    for i, (spos, title) in enumerate(sections):
        if _SKILL_SECTION_RE.search(title):
            end = sections[i + 1][0] if i + 1 < len(sections) else len(masked)
            ranges.append((spos, end))

    out: list[SkillLine] = []
    for rstart, rend in ranges:
        for m in _TEXTBF_RE.finditer(masked, rstart, rend):
            lclose = match_brace(masked, m.end() - 1)
            if lclose == -1 or lclose > rend:
                continue
            label = inline_text(src[m.end():lclose]).rstrip(":").strip()
            if not label:
                continue
            pos = lclose + 1
            while pos < rend and masked[pos] in " \t":
                pos += 1
            if pos < rend and masked[pos] == "{":
                # \textbf{Languages}{: Java, Python}
                close = match_brace(masked, pos)
                if close == -1:
                    continue
                start, end = pos + 1, close
            else:
                # \textbf{Languages}: Java, Python \\
                start, end = pos, _line_end(masked, pos, rend)
            while start < end and masked[start] in ": \t":
                start += 1
            while end > start and masked[end - 1] in " \t\r\n":
                end -= 1
            text = inline_text(src[start:end])
            if not text:
                continue
            out.append(SkillLine(f"s{len(out) + 1}", start, end, section_at(start), label, text))
    return out


def _line_end(masked: str, pos: int, limit: int) -> int:
    """End of a skills line: `\\\\`, newline, or an unmatched `}`."""
    depth = 0
    i = pos
    while i < limit:
        c = masked[i]
        if c == "\\":
            if masked.startswith("\\\\", i) and depth == 0:
                return i
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            if depth == 0:
                return i
            depth -= 1
        elif c == "\n" and depth == 0:
            return i
        i += 1
    return limit

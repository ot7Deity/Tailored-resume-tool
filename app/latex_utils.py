"""LaTeX escaping and LaTeX -> plain-text conversion."""
import re

_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_ESCAPE_RE = re.compile("|".join(re.escape(k) for k in _ESCAPES))
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def escape_latex(text: str) -> str:
    """Escape plain text for LaTeX. `**bold**` becomes \\textbf{bold}."""
    out = []
    pos = 0
    for m in _BOLD_RE.finditer(text):
        out.append(_escape_plain(text[pos:m.start()]))
        out.append(r"\textbf{" + _escape_plain(m.group(1)) + "}")
        pos = m.end()
    out.append(_escape_plain(text[pos:]))
    return "".join(out)


def _escape_plain(text: str) -> str:
    return _ESCAPE_RE.sub(lambda m: _ESCAPES[m.group(0)], text)


def mask_comments(src: str) -> str:
    """Replace LaTeX comments with spaces, keeping every character offset intact."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "%":
            j = src.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def match_brace(src: str, open_idx: int) -> int:
    """Index of the `}` matching the `{` at open_idx, or -1."""
    depth = 0
    i, n = open_idx, len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


_DROP_WITH_ARG = re.compile(
    r"\\(?:vspace|hspace|vskip|hskip|setlength|addtolength|fontsize|color|"
    r"titlespacing|titleformat|raisebox|includegraphics)\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})*"
)
_BEGIN_END = re.compile(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*\]|\{[^{}]*\})*")
_HREF = re.compile(r"\\href\{[^{}]*\}")
_SPECIALS = re.compile(r"\\([%&$#_{}])")
_COMMAND = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?")


def latex_to_text(src: str) -> str:
    """Rough plain-text rendering of LaTeX, good enough for keyword matching."""
    s = mask_comments(src)
    s = s.replace(r"\textbackslash{}", "\x00").replace(r"\textasciitilde{}", "\x01")
    s = s.replace(r"\textasciicircum{}", "\x02")
    s = _HREF.sub("", s)
    s = _BEGIN_END.sub(" ", s)
    s = _DROP_WITH_ARG.sub(" ", s)
    s = s.replace("\\\\", "\n")
    s = _SPECIALS.sub(r"\1", s)
    s = s.replace("$|$", "|").replace("---", "—").replace("--", "–")
    s = _COMMAND.sub(" ", s)
    s = s.replace("{", "").replace("}", "").replace("~", " ").replace("$", "")
    s = s.replace("\x00", "\\").replace("\x01", "~").replace("\x02", "^")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def inline_text(src: str) -> str:
    """latex_to_text collapsed onto a single line."""
    return re.sub(r"\s+", " ", latex_to_text(src)).strip()

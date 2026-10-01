# Tailored Resume Tool

A local web app that keeps your master LaTeX resume and tailors it to a job description for ATS.

It works in four steps:

1. **Master resume.** Upload or paste your `.tex` file and edit it any time. Every save backs up the previous version to `data/backups/`.
2. **Paste a job description.** Claude pulls out the keywords an ATS screens for. It then rewrites only a few bullets, at most about 10% of bullets and about 10% of words. Each rewritten bullet uses the Google **XYZ** format: *Accomplished X, as measured by Y, by doing Z*.
3. **Preview page.** This shows:
   - the ATS score of the master vs. the JD and of the tailored version vs. the JD;
   - the reasons for the difference and which version aligns better;
   - a before/after view of each changed bullet, with a ⚠ badge on any AI-estimated metric;
   - the compiled PDF.

   You can accept or reject each change, or type in your real numbers, before you download.
4. **Download.** You get `Last_First_Company_Spring2028.pdf`.

The tool never regenerates your document. It only swaps out the text of the chosen bullets, and appends keywords to existing skills lines. Everything else in your `.tex` stays byte-for-byte identical.

## Setup (Windows)

```powershell
pip install -r requirements.txt
copy .env.example .env      # then put your ANTHROPIC_API_KEY in .env
```

**Tectonic (LaTeX compiler).** The app looks for Tectonic in this order:
1. `TECTONIC_PATH` in `.env`
2. `tools\tectonic.exe`
3. your `PATH`

To install it, download `tectonic-*-x86_64-pc-windows-msvc.zip` from https://github.com/tectonic-typesetting/tectonic/releases and extract `tectonic.exe` into `tools\`. The first compile downloads the LaTeX packages it needs, which takes about a minute.

If your resume uses a custom `.cls`/`.sty` file or images, put them in `data\assets\`. They are copied next to the `.tex` at compile time.

## Run

```powershell
python -m uvicorn app.main:app --reload
```

Then open http://localhost:8000:
1. On **Master resume**, upload your `.tex`. Check that the bullets and skills were detected.
2. On **Tailor to a job**, paste the JD.
3. Review the preview, then click **Download PDF**.

## How scoring works

Both versions are scored with the same deterministic formula against the same JD keywords, so the numbers are directly comparable:

| Component | Weight | What it measures |
|---|---|---|
| Keyword coverage | 60% | Importance-weighted share of JD keywords found (with variants, e.g. AWS / Amazon Web Services) |
| Must-have hard skills | 20% | Share of required tools and skills present |
| Bullet quality | 20% | Bullets with a metric, a strong action verb and XYZ structure |

Claude writes a plain-English explanation of the gap on top of this, with suggestions. Changing edits on the preview page re-scores right away. Click *Refresh AI analysis* to regenerate the explanation.

## Configuration (`.env`)

| Variable | Default | |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-5` | Model for analysis, tailoring and explanation |
| `CLAUDE_FALLBACKS` | `1` | Server-side refusal fallback |
| `TERM_LABEL` | `Spring2028` | Last part of the filename |
| `MAX_CHANGE_RATIO` | `0.10` | Max share of words changed |
| `MAX_BULLET_RATIO` | `0.10` | Max share of bullets rewritten |
| `TECTONIC_PATH` | – | Path to `tectonic.exe` |

## Supported resume formats

The tool is tuned for Jake's Resume–style templates:
- bullets written as `\resumeItem{...}`, or plain `\item ...` inside itemize;
- skills lines written as `\textbf{Label}{: a, b, c}` or `\textbf{Label}: a, b, c \\`;
- the name taken from `\textbf{\Huge ...}`, `\name{}` or `\author{}`. You can override it on the Master page.

The Master page lists exactly what was detected.

Templates like Jake's use pdfTeX-only commands (`\input{glyphtounicode}`, `\pdfgentounicode=1`). These are automatically guarded in the copy that gets compiled. Your files are not changed.

## Tests

```powershell
python -m pytest
```

Claude and Tectonic are mocked in the API tests.

## Layout

```
app/        FastAPI backend (parser, tailoring, scoring, compile, storage)
static/     HTML/CSS/JS frontend (master editor, tailor, preview)
data/       your master, backups, assets and tailored runs (gitignored)
examples/   sample Jake's-template resume
tests/
```

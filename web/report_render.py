"""Render a BeWER envelope into Canal-style report data.

Tympany evaluates with bewer in-process (see ``tympany.bewer_eval``) and stores a
small JSON *envelope*. The report we show used to be a plain op-by-op list; this
module turns the same envelope into the data the Canal HTML report renders:

  * a Summary block (dataset metrics + corpus counts), and
  * per-example word-aligned Ref./Gen. lines, colour-coded by op type, wrapped to
    a fixed width, with medical key terms boxed inline — exactly how Corti Canal
    lays out alignments.

No bewer re-evaluation happens here; we only reshape the stored ops (and, for
highlighting, match the stored ``key_terms`` against the aligned words). The HTML
fragments are built with explicit escaping and marked safe, so the template can
emit them as-is under the report's ``script-src 'none'`` CSP.
"""

from __future__ import annotations

from html import escape

from markupsafe import Markup

# Canal's alignment palette (see the colour-coded report it generates).
COLOR_CORRECT = "#212529"   # MATCH — generated word matches the reference
COLOR_MISSPELL = "#c46f01"  # SUBSTITUTE — word present but wrong
COLOR_EXTRA = "#17a2b8"     # INSERT — extra word in the generated text
COLOR_MISSING = "#dc3545"   # DELETE — word missing from the generated text
COLOR_PADDING = "#ededed"   # alignment padding (no underlying word)

# Characters per alignment line before wrapping to a new numbered line. Chosen to
# match Canal's ~1000px monospace column.
WRAP_WIDTH = 100

_KEYWORD_OPEN = '<span class="keyword-box">'
_KEYWORD_CLOSE = "</span>"


def _word_span(word: str, color: str) -> str:
    return f'<span style="color: {color};">{escape(word)}</span>'


def _pad_span(width: int) -> str:
    if width <= 0:
        return ""
    return f'<span style="background-color: {COLOR_PADDING};">{"&nbsp;" * width}</span>'


def _column(op: dict) -> dict | None:
    """Reshape one alignment op into a render column for both rows.

    Each op occupies one column of equal width on the Ref. and Gen. rows; the
    shorter side is padded so the rows stay aligned word-for-word. Returns the
    rendered cell HTML plus the underlying words (for key-term matching).
    """
    op_type = (op.get("type") or "").upper()
    ref = op.get("ref") or ""
    hyp = op.get("hyp") or ""
    width = max(len(ref), len(hyp))

    if op_type == "MATCH":
        ref_color = gen_color = COLOR_CORRECT
    elif op_type == "SUBSTITUTE":
        ref_color = gen_color = COLOR_MISSPELL
    elif op_type == "DELETE":
        ref_color, gen_color = COLOR_MISSING, None
    elif op_type == "INSERT":
        ref_color, gen_color = None, COLOR_EXTRA
    else:
        return None

    ref_html = (_word_span(ref, ref_color) + _pad_span(width - len(ref))) if ref else _pad_span(width)
    gen_html = (_word_span(hyp, gen_color) + _pad_span(width - len(hyp))) if hyp else _pad_span(width)

    return {
        "ref_word": ref, "gen_word": hyp,
        "ref_html": ref_html, "gen_html": gen_html,
        "width": width,
    }


def _term_tokens(terms: list[str]) -> list[tuple[str, ...]]:
    """Normalise raw key terms into token tuples (legacy string-match fallback)."""
    out: list[tuple[str, ...]] = []
    for term in terms:
        toks = tuple(t for t in (term or "").lower().split())
        if toks:
            out.append(toks)
    return out


def _match_spans(words: list[str], terms: list[tuple[str, ...]]) -> list[tuple[int, int]]:
    """Find non-overlapping key-term occurrences in ``words`` (lowercased).

    Returns ``[start, stop)`` word-index spans, taking the longest term at each
    position so multi-word terms win over single words. Only used for envelopes
    that predate bewer-provided key-term slices.
    """
    if not terms:
        return []
    by_len: dict[int, set[tuple[str, ...]]] = {}
    for toks in terms:
        by_len.setdefault(len(toks), set()).add(toks)
    max_len = max(by_len)

    spans: list[tuple[int, int]] = []
    i, n = 0, len(words)
    while i < n:
        for length in range(min(max_len, n - i), 0, -1):
            if tuple(words[i:i + length]) in by_len.get(length, ()):
                spans.append((i, i + length))
                i += length
                break
        else:
            i += 1
    return spans


def _box_edges(
    columns: list[dict], side: str, spans: list, fallback_terms: list[tuple[str, ...]],
) -> tuple[set[int], set[int]]:
    """Column indices where a keyword box opens / closes for one row.

    ``spans`` are bewer-provided ``[start, stop)`` token-index slices (preferred,
    consistent with MTR). When the envelope predates them (``spans is None``), fall
    back to matching ``fallback_terms`` against the row's words. Either way, term
    spans are mapped from word-index to render-column index (skipping padding).
    """
    word_key = f"{side}_word"
    word_cols = [idx for idx, c in enumerate(columns) if c[word_key]]
    if spans is None:
        words = [columns[idx][word_key].lower() for idx in word_cols]
        spans = _match_spans(words, fallback_terms)

    opens: set[int] = set()
    closes: set[int] = set()
    n = len(word_cols)
    for start, stop in spans:
        if 0 <= start < n and 0 < stop <= n:
            opens.add(word_cols[start])
            closes.add(word_cols[stop - 1])
    return opens, closes


def _render_row(
    line_groups: list[list[int]], columns: list[dict], side: str,
    opens: set[int], closes: set[int],
) -> list[Markup]:
    """Render one row (ref/gen) into per-line HTML, boxing matched key terms."""
    html_key = f"{side}_html"
    lines: list[Markup] = []
    box_open = False
    for group in line_groups:
        parts: list[str] = []
        if box_open:  # a multi-word term carried over from the previous line
            parts.append(_KEYWORD_OPEN)
        for idx in group:
            if idx in opens and not box_open:
                parts.append(_KEYWORD_OPEN)
                box_open = True
            parts.append(columns[idx][html_key])
            if idx in closes and box_open:
                parts.append(_KEYWORD_CLOSE)
                box_open = False
            parts.append("&nbsp;")
        if box_open:
            parts.append(_KEYWORD_CLOSE)  # close at the line edge; reopen next line
        lines.append(Markup("".join(parts)))
    return lines


def _wrap(columns: list[dict]) -> list[list[int]]:
    """Group column indices into lines no wider than ``WRAP_WIDTH``."""
    groups: list[list[int]] = []
    current: list[int] = []
    width = 0
    for idx, col in enumerate(columns):
        col_width = col["width"]
        if current and width + col_width + 1 > WRAP_WIDTH:
            groups.append(current)
            current = []
            width = 0
        current.append(idx)
        width += col_width + 1
    if current:
        groups.append(current)
    return groups


def _example_lines(ex: dict, fallback_terms: list[tuple[str, ...]]) -> list[dict]:
    """Build numbered {num, ref, gen} alignment lines for one example."""
    columns = [col for col in (_column(op) for op in ex.get("ops") or []) if col is not None]
    if not columns:
        return []
    line_groups = _wrap(columns)

    # Prefer bewer's located key-term slices; fall back to string matching for
    # envelopes written before those slices existed (``None`` signals "missing").
    ref_spans = ex.get("ref_key_terms")
    hyp_spans = ex.get("hyp_key_terms")
    ref_opens, ref_closes = _box_edges(columns, "ref", ref_spans, fallback_terms)
    gen_opens, gen_closes = _box_edges(columns, "gen", hyp_spans, fallback_terms)
    ref_lines = _render_row(line_groups, columns, "ref", ref_opens, ref_closes)
    gen_lines = _render_row(line_groups, columns, "gen", gen_opens, gen_closes)

    return [
        {"num": i + 1, "ref": ref, "gen": gen}
        for i, (ref, gen) in enumerate(zip(ref_lines, gen_lines))
    ]


def _comma(n: int) -> str:
    return f"{n:,}"


def canal_view(envelope: dict) -> dict:
    """Reshape a bewer envelope into the Canal report template context.

    Returns ``summary`` (dataset metrics + corpus counts), ``examples`` (each with
    pre-rendered alignment ``lines``), plus flags driving the legend/metadata.
    """
    metrics = envelope.get("metrics") or {}
    settings = envelope.get("settings") or {}
    raw_examples = envelope.get("examples") or []
    key_terms = settings.get("key_terms") or []
    fallback_terms = _term_tokens(key_terms)

    examples = []
    ref_words = ref_chars = gen_words = gen_chars = 0
    for ex in raw_examples:
        ops = ex.get("ops") or []
        for op in ops:
            op_type = (op.get("type") or "").upper()
            ref, hyp = op.get("ref") or "", op.get("hyp") or ""
            if op_type in ("MATCH", "SUBSTITUTE", "DELETE"):
                ref_words += 1
                ref_chars += len(ref)
            if op_type in ("MATCH", "SUBSTITUTE", "INSERT"):
                gen_words += 1
                gen_chars += len(hyp)
        examples.append({
            "example": ex.get("example"),
            "lines": _example_lines(ex, fallback_terms),
        })

    mtr = metrics.get("mtr")
    summary = {
        "wer": metrics.get("wer") or "—",
        "cer": metrics.get("cer") or "—",
        "mtr": mtr,
        "num_examples": _comma(len(raw_examples)),
        "ref_words": _comma(ref_words),
        "ref_chars": _comma(ref_chars),
        "gen_words": _comma(gen_words),
        "gen_chars": _comma(gen_chars),
    }

    return {
        "summary": summary,
        "examples": examples,
        "show_mtr": bool(mtr),
        # Show the Medical Term legend whenever a key-term list was supplied.
        "medical_terms": bool(key_terms),
        "normalization": bool(settings.get("normalization", True)),
    }

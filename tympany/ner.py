"""Optional local medical-entity NER — ALPHA / EXPLORATORY.

The default medical-entity path in Tympany is the Corti LLM second pass
(`tympany.corti` / `tympany.llm`). This module is an *opt-in* augmentation of
the rule-based first pass: when enabled it runs a local NER model once per
example and feeds the detected medical entities back into the categorizer, so
substitutions touching a drug/device/condition are flagged as high-risk even
before the LLM sees them.

It is disabled unless ``TYMPANY_NER`` names a backend. The backend libraries are
*not* declared dependencies (they would drag torch + hundreds of packages into
the lockfile); install them manually only if you want to experiment. If the
requested backend is not installed it degrades to "off" with a one-time
warning — the app keeps working on the LLM-only default.

    TYMPANY_NER=gliner    # zero-shot, multilingual (en/fr/de/da) — needs torch
                          #   pip install gliner
    TYMPANY_NER=scispacy  # English-only biomedical model
                          #   pip install scispacy && pip install <en_core_sci_sm wheel>
    TYMPANY_NER=          # (default) off — LLM second pass only

Both backends are exploratory: model choice, labels and thresholds are not yet
tuned, and the heavy model downloads matter for self-hosted deployments. Treat
results as a hint, not ground truth.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from typing import Optional, Protocol

# Entity labels GLiNER is asked to find (zero-shot). Ignored by scispaCy, which
# uses its model's own biomedical entity types.
_GLINER_LABELS = [
    "medication",
    "medical condition",
    "medical device",
    "procedure",
    "anatomy",
    "lab test",
]
_GLINER_MODEL = "urchade/gliner_multi-v2.1"
_SCISPACY_MODEL = "en_core_sci_sm"

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


class _Provider(Protocol):
    def detect(self, text: str) -> set[str]: ...


# --- backend loading (lazy, import-guarded) ------------------------------

_lock = threading.Lock()
_provider: Optional[_Provider] = None
_resolved = False  # True once we've attempted to load (success or give-up)


def _warn(msg: str) -> None:
    print(f"  Tympany NER: {msg}", file=sys.stderr)


class _GlinerProvider:
    def __init__(self) -> None:
        from gliner import GLiNER  # type: ignore

        self._model = GLiNER.from_pretrained(_GLINER_MODEL)

    def detect(self, text: str) -> set[str]:
        spans = self._model.predict_entities(text, _GLINER_LABELS)
        return {e["text"] for e in spans if e.get("text")}


class _ScispacyProvider:
    def __init__(self) -> None:
        import spacy  # type: ignore

        self._nlp = spacy.load(_SCISPACY_MODEL)

    def detect(self, text: str) -> set[str]:
        return {ent.text for ent in self._nlp(text).ents if ent.text}


_BACKENDS = {"gliner": _GlinerProvider, "scispacy": _ScispacyProvider}


def _load_provider() -> Optional[_Provider]:
    """Resolve the configured backend once. Returns None when off/unavailable."""
    global _provider, _resolved
    if _resolved:
        return _provider
    with _lock:
        if _resolved:
            return _provider
        backend = os.environ.get("TYMPANY_NER", "").strip().lower()
        if backend:
            factory = _BACKENDS.get(backend)
            if factory is None:
                _warn(f"unknown TYMPANY_NER={backend!r}; expected one of "
                      f"{sorted(_BACKENDS)} — local NER disabled")
            else:
                try:
                    _provider = factory()  # loads the model (may download)
                    _warn(f"{backend} backend loaded (ALPHA)")
                except Exception as exc:  # ImportError or model-load failure
                    _warn(f"could not load {backend} backend ({exc}); "
                          "falling back to LLM-only — local NER disabled")
        _resolved = True
        return _provider


def ner_enabled() -> bool:
    """True when a local NER backend is configured and successfully loaded."""
    return _load_provider() is not None


def detect_entities(text: str) -> frozenset[str]:
    """Lowercased medical-entity tokens detected in ``text`` (empty if off).

    Multi-word spans are split into individual word tokens so they match the
    single-token diff groups the categorizer compares against.
    """
    provider = _load_provider()
    if provider is None or not text.strip():
        return frozenset()
    try:
        spans = provider.detect(text)
    except Exception as exc:  # never let NER break classification
        _warn(f"detection failed ({exc}); skipping for this example")
        return frozenset()
    tokens: set[str] = set()
    for span in spans:
        for word in _WORD_RE.findall(span.lower()):
            if len(word) >= 3:  # skip stop-tokens like "of", "in"
                tokens.add(word)
    return frozenset(tokens)


def _reset_for_tests() -> None:
    """Test hook: forget the resolved backend so env changes take effect."""
    global _provider, _resolved
    with _lock:
        _provider = None
        _resolved = False

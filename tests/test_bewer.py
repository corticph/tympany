"""Tests for the bewer JSON envelope → Sample/DiffGroup mapping.

`tests/fixtures/bewer_sample.json` is a captured bewer envelope (from
`bewer_eval.run_bewer`). These map/metric tests run against it with no bewer
import — fast and deterministic. A separate, skippable test exercises the live
`run_bewer` when bewer is installed.

Regenerate the fixture:
    poetry run python -c "import json; from tympany.bewer_eval import run_bewer; \
json.dump(run_bewer([('the patient has hypertension today','the patient has hypotension'), \
('blood pressure was elevated','blood pressure was elevated')], medical_terms=['hypertension','blood pressure']), \
open('tests/fixtures/bewer_sample.json','w'), indent=1)"
"""

import json
import re
from pathlib import Path

import pytest

from tympany.categorize import categorize_group
from tympany.parser import from_bewer, metrics_from_bewer, reference_corpus

ENVELOPE = json.loads((Path(__file__).parent / "fixtures" / "bewer_sample.json").read_text())
_PCT = re.compile(r"\d+(\.\d+)?%")


def test_from_bewer_builds_samples_and_diffs():
    samples = from_bewer(ENVELOPE)
    assert len(samples) == 2
    ex1 = samples[0]
    assert " ".join(t.text for t in ex1.ref_tokens) == "the patient has hypertension today"
    # the substitution/deletion run is captured as a diff group with hypotension on the gen side
    assert ex1.diffs and "hypotension" in ex1.diffs[0].pred
    assert samples[1].diffs == ()  # identical example → no diffs


def test_from_bewer_diffs_feed_categorizer():
    samples = from_bewer(ENVELOPE)
    edits = [e for d in samples[0].diffs for e in categorize_group(d)]
    assert edits
    assert edits[0].classification in {
        "formatting_error", "replacement_candidate", "context_dependent", "misrecognition",
    }


def test_metrics_from_bewer():
    m = metrics_from_bewer(ENVELOPE)
    assert _PCT.fullmatch(m["wer"]) and _PCT.fullmatch(m["cer"]) and _PCT.fullmatch(m["mtr"])
    assert m["normalization"] is True


def test_reference_corpus_from_bewer_samples():
    samples = from_bewer(ENVELOPE)
    assert reference_corpus(samples) == "the patient has hypertension today\nblood pressure was elevated"


def test_run_bewer_live():
    bewer = pytest.importorskip("bewer")  # noqa: F841 — skip if not installed
    from tympany.bewer_eval import run_bewer
    env = run_bewer(
        [("the patient has hypertension", "the patient has hypotension")],
        medical_terms=["hypertension"],
    )
    assert _PCT.fullmatch(env["metrics"]["wer"])
    assert env["examples"][0]["ops"]  # alignment ops present


def test_run_bewer_live_honors_normalization_flag():
    bewer = pytest.importorskip("bewer")  # noqa: F841 — skip if not installed
    from tympany.bewer_eval import run_bewer

    rows = [("Hello, Café.", "hello cafe")]
    normalized = run_bewer(rows, normalization=True)
    raw = run_bewer(rows, normalization=False)

    assert normalized["settings"]["normalization"] is True
    assert raw["settings"]["normalization"] is False
    assert normalized["metrics"]["wer"] == "0.00%"
    assert raw["metrics"]["wer"] == "100.00%"
    assert raw["examples"][0]["ops"]  # ops present

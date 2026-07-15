# Corti Tympany

Tympany works with [BeWER](https://github.com/corticph/bewer) evaluation and analysis framework to create, analyze, and classify speech recognition errors. 

- BeWER uses [ErrorAlign](https://pypi.org/project/error-align/), a **text-to-text alignment algorithm for speech recognition error analysis** based on input reference/generated transcripts. 
- Tympany then **analyzes BeWER reports** to classify every reference/generated text difference by type and clinical risk. 

**Purpose:** Identify which STT errors are true misrecognitions versus formatting differences or fixable shorthand that should be handled with a command or replacement rule, and lets you recalculate WER with such items excluded.

> **Deployment model:** Tympany is a **single-tenant** application — you run your own instance and it holds only your data. The email entered at login is a lightweight per-instance label to scope history; **it is not authentication**. Run it locally, or host it for your own team behind your own access control (VPN/SSO proxy). Do not expose a single shared instance to multiple untrusted parties. See [Deployment & hardening](#deployment--hardening) and [Data handling & privacy](#data-handling--privacy).

> **Evaluation engine:** Tympany uses the [bewer](https://github.com/corticph/bewer) library (which also powers [Corti Canal](https://github.com/corticph/corti-canal)) directly, in-process — no external CLI. bewer is alpha; its version is pinned in `pyproject.toml`.

---

## Quick start (run from GitHub)

If you just want to run Tympany, you do not need to clone the repo or install
Poetry. Use [`uvx`](https://docs.astral.sh/uv/) (the Python equivalent of
`npx`) to run it directly from this public GitHub repository:

```sh
uvx --from git+https://github.com/corticph/tympany tympany

# pin a specific tag for a reproducible version
uvx --from git+https://github.com/corticph/tympany@v0.3.0 tympany
```

The unpinned form tracks the latest default-branch state; the `@v...` form pins
a specific tagged version and is the better choice for repeatable installs.

The command above starts the local web app and opens it in your browser. On
first run it prompts for Corti credentials if they aren't already in your
environment or a local `.env` (and offers to save them). Credentials are
**optional** — without them the rule-based classification still works; they
only enable the optional LLM second pass.

> Needs a Python 3.11–3.14 interpreter available (an upper bound `bewer`
> requires). `uv` fetches a compatible one automatically if you don't have it.
> The first run downloads dependencies and may take a minute; `uv` caches them,
> so subsequent runs are fast.

Common flags:

```
-p, --port <n>          Port to listen on (default 8000, or $PORT)
    --host <addr>       Bind address (default 127.0.0.1)
    --no-open           Don't open the browser automatically
    --data-dir <path>   Where to store data (else $TYMPANY_DATA_DIR, else a
                        per-user OS data directory)
    --dev               Enable auto-reload (development)
    --no-prompt         Never prompt for credentials
    --client-id / --client-secret / --tenant / --environment
                        Corti credentials (else the matching CORTI_* env vars)
-h, --help              Show help
```

Your analyses are written to a **per-user data directory on your own machine**
(e.g. `~/Library/Application Support/tympany` on macOS,
`~/.local/share/tympany` on Linux) — never inside the installed package.
Because transcripts can contain **PHI/PII**, this data is local-only by design;
see [Data handling & privacy](#data-handling--privacy).

Prefer a clone and Poetry for development or source changes — see
[Running from source](#running-from-source-development).

---

## What you can do

- **Create a BeWER report from text** — paste or upload reference/generated transcripts and Tympany runs bewer for you. *Generate report* produces the report (JSON) to view/download; *Generate & analyze* also runs the full classification.
- **Analyze a BeWER report** — upload an existing BeWER report (JSON) and classify every difference.
- **Medical Term Recall (MTR)** — supply a medical-terms list and bewer additionally reports how many of those terms the generated text captured. Save lists for reuse, or **extract** them automatically from a BeWER report.
- **Review & iterate** — reclassify, exclude, flag, edit descriptions (all autosaved), then **Re-run** to measure the impact of excluded errors. Export results or flagged rows.

The top nav has three sections: **Home** (landing page with these entry points plus how-to and WER best practices), **Create** (create a report, generated-report history, saved term lists), and **Analyze** (upload, how-it-works, analysis history).

---

## Error classifications

| Classification | Risk | Description |
|---|---|---|
| **Formatting error** | Low | Difference in written form for numbers, dates, or years |
| **Replacement candidate** | Low | Abbreviation or shorthand fixable with a replacement rule |
| **Context-dependent** | Medium | Spelling variant or compound boundary — meaning likely preserved, but needs review |
| **Misrecognition** | High | Large edit distance errors that may have clinical significance or alter intended meaning |

---

## Supported languages

The rule-based classifier has word lists and abbreviation tables for:

| Language | Code | Coverage |
|---|---|---|
| English | `en` | Full — cardinals, ordinals, medical abbreviations, medications |
| French | `fr` | Cardinals, ordinals, unit abbreviations, formatting markers |
| German | `de` | Cardinals, ordinals, unit abbreviations, formatting markers |
| Swiss German | `de-CH` | Cardinals and ordinals (ß→ss variants); otherwise identical to German |
| Danish | `da` | Cardinals, ordinals, unit abbreviations, formatting markers |

**Numbers:** All digit ↔ number-word matching in the rule-based pass is backed by [num2words](https://pypi.org/project/num2words/): a digit is matched against its spelled-out form in any supported language, single-token or compound, regardless of tokenisation — `four → 4`, `seventy-five → 75`, French `soixante quinze → 75`, German `zweitausendzwanzig → 2020`, ordinals like `vingt et unième → 21`. The static word lists above are a fast path for the common single-token case and add language variants num2words doesn't emit (e.g. Swiss German `ß→ss` spellings).

To add a language, add its number words to `tympany/data.py` and include them in the `ALL_CARDINALS` / `ALL_ORDINALS` unions.

---

## Running from source (development)

**Requirements:** Python 3.11+ and [Poetry](https://python-poetry.org/docs/#installation).

If you want a local checkout for development or to run from source:

```sh
git clone https://github.com/corticph/tympany
cd tympany
./setup.sh      # poetry install + seeds .env from .env.example
./start.sh      # serves on http://127.0.0.1:8000
```

For live-reload during development:

```sh
TYMPANY_DEV=1 ./start.sh
```

Open [http://localhost:8000](http://localhost:8000) and enter your email at `/login` (no password — it scopes your history on this instance).

`setup.sh` and `start.sh` are thin wrappers around Poetry for source checkouts.
The equivalent direct commands are:

```sh
poetry install
poetry run tympany-serve          # the web server (honours HOST/PORT/TYMPANY_DEV/WEB_CONCURRENCY)
poetry run uvicorn web.app:app    # or run uvicorn directly
```

Report generation, analysis, and re-run all use the `bewer` library, installed automatically by `poetry install` — there is no external CLI to set up.

---

## Deployment & hardening

Tympany is meant to be self-hosted single-tenant. Before hosting it for your team:

1. **Set `SECRET_KEY`.** Required. It signs session cookies. If unset, the app falls back to a random *ephemeral* key (logged as a warning) — logins won't survive a restart and it breaks multi-worker setups. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. **Terminate TLS and set `TYMPANY_HTTPS=1`** so the session cookie is marked Secure. Run behind a reverse proxy (nginx/Caddy) that provides HTTPS.
3. **Run without `--reload`** — the default for `./start.sh` / `poetry run tympany-serve`. Scale with `WEB_CONCURRENCY=N` workers; `HOST=0.0.0.0` and `PORT` are configurable. Multi-worker requires `SECRET_KEY` to be set (shared signing key).
4. **Persist data outside the repo:** set `TYMPANY_DATA_DIR=/var/lib/tympany` (or similar) on a persistent, access-restricted disk.
5. **Put it behind your own access control** (VPN, SSO proxy, IP allow-list). The email login is not authentication.
6. **Health check:** `GET /health` returns `{"status":"ok","bewer":true}` with no auth and no data access.

Baseline response hardening is built in: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a `script-src 'none'` CSP on served BeWER reports. Request limits are configurable (see env table). There is no rate limiting — put the instance behind a proxy that provides it if exposed.

---

## Data handling & privacy

STT transcripts can contain **PHI/PII**. Tympany stores them **unencrypted on disk** under `TYMPANY_DATA_DIR` (default: a per-user OS data directory such as `~/Library/Application Support/tympany` on macOS or `~/.local/share/tympany` on Linux): analysis JSON, generated BeWER reports (JSON), the input CSVs, and saved term lists.

- Keep the data directory on an **encrypted, access-restricted** volume.
- Define a **retention policy** — nothing is auto-expired. Deleting an analysis or generated report removes its files (JSON + sidecars). Saved term lists are deleted from the UI.
- Transcript text is sent to Corti only if you enable the **LLM second pass** (the eligible ref/gen pairs). Without `CORTI_*` credentials, no data leaves the host (bewer runs in-process).
- Because this is single-tenant, the operator controls residency and access; choose a host region appropriate to your data.

---

## Environment variables

For local development, put these in `.env` (a template is in `.env.example`). For deployment, set them in the environment.

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | **Deployment** | Random string signing session cookies (≥32 chars). Ephemeral random fallback in dev only. |
| `TYMPANY_HTTPS` | Deployment | `1`/`true` to mark the session cookie Secure (set when behind TLS). |
| `TYMPANY_DATA_DIR` | Optional | Where history, reports, and term lists are stored (default: a per-user OS data dir, e.g. `~/Library/Application Support/tympany`). Point at a persistent path for deployments. |
| `TYMPANY_MAX_UPLOAD_MB` | Optional | Max upload size for reports/CSVs/term files (default `25`). |
| `TYMPANY_MAX_ROWS` | Optional | Max examples per report (default `5000`). |
| `TYMPANY_MAX_TERMS` | Optional | Max terms per medical-terms list (default `20000`). |
| `TYMPANY_MAX_EXTRACT_CHARS` | Optional | Max reference characters sent to the term-extractor agent (default `24000`). |
| `TYMPANY_NER` | Optional (ALPHA) | Mount a local medical-entity NER backend: `gliner` or `scispacy`. Off by default — backends are not bundled (see [Optional — local medical-entity NER](#optional--local-medical-entity-ner-alpha)). |
| `CORTI_CLIENT_ID` | For LLM pass | Corti OAuth2 client id (from the Corti Console). |
| `CORTI_CLIENT_SECRET` | For LLM pass | Corti OAuth2 client secret. |
| `CORTI_TENANT` | For LLM pass | Corti tenant name. |
| `CORTI_ENVIRONMENT` | For LLM pass | Corti environment: `eu` or `us`. |

Run flags for `./start.sh`: `HOST`, `PORT`, `WEB_CONCURRENCY`, and `TYMPANY_DEV=1` (enables `--reload`).

---

## Creating BeWER reports

On the **Create** page:

- **Input** — paste reference and generated text (one example per line, paired by position) or upload a CSV with `ref`/`gen` columns.
- **Medical terms** *(optional)* — paste, upload a `.txt` (one term per line), or pick a saved list to compute Medical Term Recall.
- **Normalization** — toggle text normalization before scoring.
- **Generate report** runs bewer and keeps you on the page to view/download the report (JSON) and iterate; each generation is saved under **Generated BeWER reports**.
- **Generate & analyze** runs bewer *and* the full Tympany classification, landing you on the results page.

When no name is given, reports are named `report-<YYYYMMDD>-<HHMMSS>.bewer.json` in your local time.

Generated reports can later be analyzed, viewed, downloaded (HTML, json, or source CSV), or deleted from the home page.

---

## Medical Term Recall & saved term lists

Supplying a medical-terms file makes bewer compute **Medical Term Recall (MTR)** — the share of those terms the generated text captured. Unlike WER/CER, **higher MTR is better**; the results page colors an improved MTR green after a re-run. MTR appears as its own metric block on results and its own column in the history tables (only when terms were used).

**Saved term lists** are reusable, per-instance vocabularies. From the Create page you can create (**+ New list**), edit, duplicate, or delete them. Editing or renaming a list affects **future** reports only — already-generated reports kept their own copy of the terms, so a re-run recomputes MTR against the same vocabulary.

**Extract terms from a report.** In the term-list editor you can upload a BeWER report and Tympany will pull medical terms from its **reference** (ground-truth) text — never the generated side — and add any new ones to the list (de-duplicated). This uses a dedicated Corti agent and is available only when `CORTI_*` credentials are configured.

---

## Reviewing & exporting

On the results page each error is one editable row: reclassify it, edit its description, **Exclude** it (drops it from counts and from a re-run), or **Flag** it. Edits autosave to your history.

- **Export results** — every row in its current edited state.
- **Export flags** — only flagged rows, with `ref_context`/`gen_context` columns holding the full reference/generated text of each flagged error's example.
- For reports created in Tympany: **Download BeWER report** (the input report JSON) and **Download CSV** (the ref/gen source). The input and re-run reports can also be viewed inline from the links above the metrics.

---

## Re-run

The results page shows **WER** and **CER** from the report alongside an **Updated** figure. **Re-run** reconstructs the dataset with every **excluded** error corrected back to the reference, re-evaluates it with bewer (reusing the same medical terms for MTR), and reports the resulting WER/CER/MTR — measuring the impact of removing those errors. The regenerated report (JSON) is downloadable and the original → updated figures appear in the History table.

> **Comparability:** **Original** figures come from the initial evaluation; **Updated** figures are recomputed by re-evaluating the reconstructed text with bewer (same medical terms). Treat the difference as the *delta from removing excluded errors*, not two independently-baselined numbers.

---

## How the analysis works

### Step 1 — Evaluation & alignment

Tympany evaluates the (reference, generated) pairs with the `bewer` library, which returns WER/CER (and Medical Term Recall when a terms list is supplied) plus a per-token **alignment** of operations — `MATCH`, `SUBSTITUTE`, `DELETE`, `INSERT`. That alignment is serialised to a stable JSON envelope and mapped (`parser.from_bewer`) into contiguous **diff groups** — each one discrete error — exactly the shape the classifier consumes. (Uploading an existing report imports the same JSON.)

### Step 2 — Rule-based classification (always runs)

Each diff group passes through an ordered rule chain in `tympany/categorize.py`; first match wins:

| Rule | What it catches |
|---|---|
| `rule_number_format` | Digit ↔ number word in any supported language, including multi-token compounds (via num2words) |
| `rule_date_format` | Zero-padded date component ↔ spoken form (`04` ↔ `zero four`) |
| `rule_year_format` | Four-digit year ↔ two-digit short form or spelled-out |
| `rule_abbreviation` | Known abbreviation ↔ expansion (`bp` ↔ `blood pressure`) |
| `rule_roman_numeral` | Roman numeral ↔ spoken equivalent (`ii` ↔ `deux`) |
| `rule_ordinal_format` | Digit ↔ ordinal word, including compounds (`3` ↔ `troisième`, `21` ↔ `vingt et unième`) |
| `rule_compound_boundary` | Concatenated tokens near-identical — compound split or merge |
| `rule_medication_or_device` | One side matches/closely resembles a known drug/device name |
| `rule_latin_greek_spelling` | Latin/Greek spelling variant (`ae`↔`e`, `c`↔`k`, etc.) |
| `rule_spelling_close` | Levenshtein distance ≤ 2 or similarity ≥ 0.8 |
| `rule_other` | Fallback — `misrecognition` |

Each edit also gets token ratio, character distance, and similarity score appended to its detail (shown as a tooltip on the Risk badge).

### Step 3 — LLM second pass (optional)

The **uncertain errors** pass: only `misrecognition` and `context_dependent` edits are sent to the LLM; low-risk ones are settled. The LLM receives the ref/gen pair and a structured prompt of all four categories and returns a JSON override. Most useful for abbreviations not in `data.py`, institution-specific shorthand, and judging whether a medical-entity difference is dangerous (a different drug/dose) or benign (a spelling variant of the same entity). This is the **default** medical-entity path.

> **Caveat:** a real error the rule chain mis-filed as low-risk won't reach the LLM. Widening the eligible set trades coverage for cost/latency (one round-trip per eligible edit).

### Optional — local medical-entity NER (ALPHA)

As an experiment, you can mount a local NER model that runs once per example and feeds detected medical entities (drugs, devices, conditions, procedures, anatomy, lab tests) back into the rule pass, so a substitution touching an entity is flagged high-risk before the LLM sees it. It is **off by default**; the LLM pass above remains the primary path. Set `TYMPANY_NER=gliner` (zero-shot, multilingual — `pip install gliner`, pulls in torch) or `TYMPANY_NER=scispacy` (English-only). The backends are intentionally **not** declared dependencies; if the chosen one isn't installed Tympany logs a warning and continues LLM-only. Model choice and thresholds are untuned — treat output as a hint. See [`tympany/ner.py`](tympany/ner.py).

---

## LLM second pass

Available when `CORTI_*` credentials are set; uses the [Corti Agentic Framework](https://docs.corti.ai):
1. Authenticates via OAuth2 client credentials (short-lived bearer token, cached/refreshed).
2. On first use, auto-creates the agents it needs and caches their ids under the data dir (`corti_agent.json`) — a **classifier** for the LLM second pass and a **term extractor** for pulling medical terms from a report. See [`tympany/corti.py`](tympany/corti.py).
3. Sends eligible pairs to `POST /agents/{id}/v1/message:send`, each with fresh context, fanned across a small thread pool.

---

## Project structure

```
tympany/
  parser.py       — diff model (Token/DiffGroup/Sample) + bewer JSON → DiffGroups (from_bewer)
  categorize.py   — Rule chain → category, classification, risk, edit stats
  classify.py     — classify samples' diff groups (rule chain + optional LLM pass)
  data.py         — Medical word lists and abbreviations (EN, FR, DE, de-CH, DA)
  corti.py        — Corti Agentic Framework client (classifier + term-extractor agents)
  ner.py          — Optional local medical-entity NER backend (ALPHA, off by default)
  bewer_eval.py   — bewer evaluation (run_bewer) + re-run reconstruction
web/
  app.py          — FastAPI application: auth, home, create, analyze, history, terms
  serve.py        — web-server console entry point (tympany-serve)
  history.py      — Per-user analysis + generated-report storage
  terms.py        — Saved medical-term lists
  templates/      — Jinja2 templates (base, login, home, create, analyze, results, reports_new, terms_edit, bewer_report)
  static/         — Static assets
pyproject.toml    — Python project metadata in Poetry format; poetry.lock pins versions
```

### Data layout

Everything is filed under `TYMPANY_DATA_DIR` (default: a per-user OS data dir), keyed per user:

```
history/<user>/<id>.json          analysis record (edits, samples, metrics, source)
history/<user>/<id>.bewer.json    authored report: the input BeWER report (envelope)
history/<user>/<id>.input.csv     authored report: the (ref, gen) source CSV
history/<user>/<id>.terms.txt     authored report: medical terms used (for MTR re-runs)
history/<user>/<id>.rerun.bewer.json  the re-run BeWER report (after Re-run)
generated/<user>/<id>.{json,bewer.json,csv,terms.txt}   generate-only reports
terms/<user>/<name>.txt           saved medical-term lists
corti_agent.json                  cached Corti agent ids (classifier, term_extractor)
```

---

## Security notes

- **The email login is not authentication** — it only scopes per-instance history. Run single-tenant and gate access at the network/proxy layer.
- **`SECRET_KEY` must be set** for any deployment; the only built-in fallback is a random ephemeral key (never a shared hardcoded secret).
- **Source is shippable:** there are no secrets in the repo (`.env` is gitignored). Persisted data lives under `TYMPANY_DATA_DIR`, which defaults to a per-user OS data directory outside the repo. Note that the classification rules, word lists (`tympany/data.py`), and the agent prompt (`tympany/corti.py`) are visible in the source.
- **Served BeWER reports** are rendered by Tympany from user text and served with `script-src 'none'` (no scripts) as defense in depth.
- See [Data handling & privacy](#data-handling--privacy) for PHI considerations.

---

## Development notes

- The rule chain in `tympany/categorize.py` is ordered — first match wins. New rules go above `rule_other`.
- `tympany/data.py` holds word lists per language. Add abbreviations or a new language's number words here before reaching for the LLM.
- `parser.from_bewer` maps bewer alignment ops to diff groups; `tympany/bewer_eval.py` wraps the bewer library (pinned alpha — watch for API changes).

### Tests

```sh
poetry install          # includes the dev group (pytest)
poetry run pytest
```

The suite (`tests/`) covers the saved-term store, input validation, routing/redirects, and term extraction (the Corti agent call is mocked, so no credentials or network are needed).

---

## License

MIT — see [LICENSE](LICENSE).

---

*Built by Corti.*

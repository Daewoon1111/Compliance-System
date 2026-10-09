# IERCV — Regulatory Information Extraction & Compliance Checking

IERCV reads document dossiers (scanned or digital PDFs), extracts the information you
care about, and checks every extracted value against **your own regulations** — returning
a verdict for each field together with the regulation text it is based on.

The system is **generic**: what to extract and what to check is not hard-coded. Each kind
of dossier is described by a **check set** (internally a *field set*: the information to
extract, how to recognise it and the criterion to check it against), created from a form on
the Check page — no JSON writing needed. The rules to check against are your own regulation
documents (Markdown, Word, PDF or a zip of them), grouped into named **regulation sets** and
loaded into a local regulation store.

Everything runs **locally**: OCR with the Vintern-1B-v3.5 vision-language model, the
language models through [Ollama](https://ollama.com), and retrieval with ChromaDB. No API
key, no per-call cost, and documents never leave the machine.

> UI language: Vietnamese (default) and English. The bundled regulation-processing logic
> (OCR clean-up, diacritic restoration, date/money parsing) is tuned for Vietnamese
> documents.

---

## How it works

```
Upload dossier  ->  Review extracted data  ->  Compliance report
   (step 1)              (step 2)                  (step 3)
```

1. **Upload.** Upload one or more PDFs; they are checked with the *check set in use*
   (shown at the top of the page, changed or created with **Create check set**; new
   regulations are added with **Add regulation set**). Optionally switch on **Select check
   region**: a window (90% of the page) shows every page, you draw a rectangle on the
   pages that matter and untick the pages that should not be scanned — only those areas
   are read. Each page is read from the embedded text layer when it is trustworthy (the
   layer is cross-checked against an OCR read of the page to catch hidden text), otherwise
   with Vintern OCR. Progress is streamed live (Server-Sent Events) and keeps running if
   you navigate away.
2. **Review.** Extracted fields are shown next to the OCR text. Hovering a value highlights
   where it came from; clicking an OCR line jumps to the matching field. Wrong values can be
   corrected by hand — a manual value always wins. Fields are grouped by the `section`
   declared in the field set; filters show empty / low-confidence fields first.
3. **Report.** Every field gets a verdict with an explanation and the cited regulation
   passage (including its effective dates). The report can be exported as PDF; every run is
   logged for the History and Statistics pages.

### Verdicts

| Verdict            | Meaning                                                                                   |
| ------------------ | ----------------------------------------------------------------------------------------- |
| `FAIL`             | The value breaks a requirement, with a cited regulation passage as the basis              |
| `NEEDS_SUPPLEMENT` | Missing or doubtful data, a required field is absent, or no regulation could be cited     |
| `PASS`             | The value satisfies the requirement, with a cited basis                                   |
| `DECLARATION`      | Identification field (no criterion to check) — the value is recorded only                 |

The dossier verdict is the most severe field verdict. Several documents uploaded together
are merged into one dossier: the first file is the primary document and later files only
fill fields that are still empty.

### Pipeline

1. **Read** — text layer or Vintern OCR (auto-rotation, red-stamp removal, splitting of very
   dense pages, repetition cut-off); per-line confidence from token probabilities.
2. **Rule-based extraction** — each field is located by its `label` / `label_alts` (tolerant
   to OCR spelling damage) and parsed according to its `value_type` (`text`, `date`,
   `number`, `money`); an optional `value_regex` overrides the label search.
3. **Diacritic restoration** — words that lost their Vietnamese diacritics in OCR are
   restored from a lexicon built from the regulation store.
4. **LLM gap-filling** (`extraction_model`) — the model is only asked for fields the rules
   could not find. Anti-hallucination guards: every value must be anchored in a real passage
   of the OCR text, numbers and amounts must appear in that passage, confidence is capped,
   and values coming from another field's evidence are rejected.
5. **Input quality gates** — OCR quality, signed date (missing / invalid / in the future /
   too old). A flagged field is downgraded to `NEEDS_SUPPLEMENT` instead of being judged on
   doubtful data.
6. **Compliance check** — for each `regulated` field, the regulation store is queried with
   `document_kind + label + check_aspect`, restricted to the check set's
   `regulation_sets` (all regulations when none is chosen), filtered by the documents in
   force on the signed date when the field set declares a `signed_date_field`, and
   re-ranked. All regulated
   fields are then judged in **one** call to `validation_model`. `positive_integer` fields are
   checked deterministically in code. A `PASS`/`FAIL` without a citable passage is
   downgraded to `NEEDS_SUPPLEMENT`; citations attached by the system (not by the model) are
   marked "auto-matched — verify".

---

## Features

- **Check sets (Check page)** — *Create check set* opens a form: name, document kind,
  regulation sets to check against, and a table with one row per item (label on the
  document, other names, value type, check method, check criterion, required, in a table).
  Instead of filling the table by hand, the user can **describe in plain words** what to
  check ("salary not below the regional minimum wage", "probation no longer than 60 days"…):
  `POST /api/v1/config/draft` turns the description into table rows with `draft_model`
  (falls back to a rule-based parser when Ollama is off or slow), and the user reviews them
  before saving. It can start from an existing set; the saved set becomes the one in use
  (remembered in `backend/app/data/user_config/active.json`). *Use an existing set* switches sets.
- **Field sets (Settings page, no admin rights needed)** — create, copy, edit and delete
  field sets in a JSON editor with live validation for finer settings (`value_regex`,
  `max_len`, …). Default field sets ship in `backend/app/prompts/field_sets/`.
- **Regulation sets (Check page)** — *Add regulation set*: give the set a name and upload
  `.md`, `.txt`, `.docx`, `.pdf` or `.zip` files (up to 200 MB per upload). Archives are
  extracted safely (file names only, size/count limits), documents already in the set are
  skipped, everything is converted to Markdown, written to
  `backend/app/rules/`, registered in `corpus.json` with the set name (unapproved) and the
  vector store is reloaded. Scanned PDFs can optionally be read with OCR.
- **Check region** — per uploaded file: pages to skip and one rectangle per page
  (normalised coordinates). Text-layer pages keep only the characters inside the rectangle;
  OCR pages are cropped before reading. The region is part of the session cache key.
- **Regulation store (Admin page)** — edit or create regulation documents (`.md`) and
  default field sets; saving a regulation reloads the vector store automatically. The
  corpus registry tracks source, version, effective dates and a SHA-256 hash per document,
  with an approval step (who approved which exact content).
- **Word / PDF to Markdown** — `npm run docx2md` converts `.docx` regulations to the
  Markdown the store expects (headings, *Chương / Mục / Điều* structure, numbered lists,
  tables); `npm run pdf2md` does the same for PDFs (text layer read directly, display lines
  re-joined into paragraphs, page numbers dropped; `--ocr` reads scanned pages with Vintern).
- **History** — full-text search (accent-insensitive), filters by dossier type and verdict,
  CSV export of the log, printable report per run.
- **Statistics** — verdict ratios per check set, and reading/extraction quality after each
  dossier check: CER, WER, OCR accuracy (1 − CER), field-level, table, number and date
  accuracy — averaged per check set and listed per dossier. The reference is the value
  after human review (the machine value is kept when a reviewer first edits a field), so
  the figures are meaningful only for reviewed dossiers; table accuracy needs fields marked
  "in table" (`in_table: true`).
- **Admin dashboards** — database status, technical metrics (throughput, latency
  percentiles, retrieval coverage, citation precision, LLM payload size), corpus audit,
  evaluation on a labelled golden set (`backend/app/data/golden/*.json`).
- **Desktop app** — runs as native Windows software: a real application window
  (pywebview on Microsoft Edge WebView2), no browser, no address bar. Closing the window
  stops the backend and the bundled Ollama; closing while an OCR/check run is in progress
  asks for confirmation first; opening it a second time brings the existing window to the
  front. Falls back to an Edge/Chrome app-mode window when WebView2 is not installed.
- **Bilingual UI** (vi/en), light/dark theme, notification center.

---

## Requirements

| Component | Version / notes |
| --- | --- |
| Python | 3.11 |
| Node.js | 20+ (to build the UI) |
| Ollama | running locally, with the model(s) configured in `backend/.env` |
| RAM | 16 GB minimum, 24 GB+ recommended (OCR model + two language models) |
| GPU | optional — NVIDIA CUDA speeds up OCR considerably |
| WebView2 Runtime | for the desktop window; preinstalled on Windows 11 and updated Windows 10 |

The first run downloads Vintern-1B-v3.5 (~1 GB), the Vietnamese embedding model and the
re-ranker from Hugging Face (internet needed once).

---

## Quick start

```bash
git clone https://github.com/Daewoon1111/Compliance-System.git
cd Compliance-System

# 1. Backend dependencies
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
#   NVIDIA GPU: install the CUDA build of torch first, see the note in requirements.txt
cp .env.example .env              # then edit: ollama_model / extraction_model / validation_model
cd ..

# 2. Frontend dependencies
npm run setup

# 3. Language model(s)
ollama pull qwen2.5:7b-instruct   # or the models you set in backend/.env

# 4. Load the regulation store (after adding your .md files, see below)
npm run seed

# 5. Run
npm run dev        # development: API on :8000, UI on http://localhost:5173
npm run desktop    # desktop app: builds the UI and opens it in its own window
```

After the first `npm run desktop`, double-click **`IERCV.bat`** in the project folder to
start the desktop app without a console window (it rebuilds the UI only when `frontend/dist`
is missing).

The admin token is printed to the console on first start (or set `admin_token` in
`backend/.env`). `npm run admin` opens the admin area directly.

---

## Adding your regulations

The regulation store reads Markdown files from `backend/app/rules/`.

The easiest way is **Add regulation set** on the Check page (any of `.md / .txt / .docx /
.pdf / .zip`, converted and loaded automatically). From the command line:

1. **Get Markdown.** Write it directly, or convert Word / PDF documents:

   ```bash
   npm run docx2md -- "D:/Regulations/Decree 01.docx"          # writes Decree 01.md next to it
   npm run docx2md -- decree.docx --rules                      # writes into backend/app/rules/
   npm run docx2md -- decree.docx -o out.md --title "Decree 01/2025 on service contracts"
   npm run pdf2md -- "D:/Regulations/Circular 02.pdf" --rules  # PDF with a text layer
   npm run pdf2md -- scanned.pdf --ocr                         # scanned PDF (slow, uses Vintern)
   ```

   Legacy `.doc` files must be saved as `.docx` first. Other converters (pandoc,
   Microsoft MarkItDown) also work — the store only needs paragraphs separated by blank
   lines and a `# Title` first heading (used as the citation name).
2. **Load it.** Save the file from *Admin → System* (reloads automatically) or run
   `npm run seed`.
3. **Register and approve** it in *Admin → Regulation store*: fill in the document number,
   type, effective dates and approve the current content hash. Effective dates drive the
   "in force on the signed date" filter.

---

## Defining a dossier type (field set)

A field set is one JSON document. Minimal example:

```json
{
  "display_name": "Service contract",
  "description": "Basic terms of a service contract",
  "document_kind": "hợp đồng",
  "jurisdiction": "VN",
  "doc_types": [],
  "signed_date_field": "ngay_ky",
  "fields_catalog": {
    "ngay_ky": {
      "label": "Ngày ký", "label_alts": ["Ký ngày"],
      "value_type": "date", "check_type": "declaration",
      "fill_hint": "Date the parties signed"
    },
    "gia_tri_hop_dong": {
      "label": "Giá trị hợp đồng",
      "value_type": "money", "check_type": "regulated",
      "check_aspect": "The contract value must state the amount and the currency."
    },
    "so_luong": {
      "label": "Số lượng", "value_type": "number", "check_type": "positive_integer"
    }
  },
  "field_check_mode": { "always_check": ["gia_tri_hop_dong"] }
}
```

| Key | Meaning |
| --- | --- |
| `display_name`, `description` | Shown on the upload page |
| `document_kind` | Kind of document; prefixed to every regulation query |
| `jurisdiction`, `doc_types` | Restrict the regulation store to these jurisdictions / document types (`[]` = all) |
| `regulation_sets` | Names of the regulation sets to check against (`[]` / absent = whole store) |
| `signed_date_field` | Key of a `date` field used to pick the regulations in force (optional) |
| `start_anchor` | Ignore text before the first line containing this phrase (optional) |
| `fields_catalog.<key>.label` / `label_alts` | Label(s) printed on the document |
| `value_type` | `text` · `date` · `number` · `money` |
| `check_type` | `regulated` (checked against regulations) · `declaration` (recorded only) · `positive_integer` (checked in code) |
| `check_aspect` | What to check — the criterion given to retrieval and to the model |
| `fill_hint`, `value_regex`, `max_len`, `keywords`, `section` | Extraction hints and UI grouping |
| `in_table` | The value sits in a table of the document (used for the Table Accuracy metric) |
| `field_check_mode.always_check` | Fields reported even when empty (`NEEDS_SUPPLEMENT`) |
| `field_check_mode.missing_is_fail` | Required fields whose absence is a `FAIL` |
| `field_check_mode.required_one_of` | Groups where at least one field must be present |

The Settings page validates the content as you type and refuses to save an invalid set.

---

## Configuration

All settings live in `backend/.env` (see `backend/.env.example` for the full, commented
list). The most important ones:

| Setting | Purpose |
| --- | --- |
| `ollama_model` | Default model(s), comma-separated fallbacks |
| `extraction_model`, `validation_model` | Separate models for the two LLM steps |
| `draft_model` | Model that turns a plain-language description into a check set (empty = `ollama_model`) |
| `ollama_num_ctx` | Context window — raise it together with `rag_total_cap` |
| `vintern_device` | `auto` · `cuda` · `cpu` |
| `ocr_dpi` | Render resolution; also selects the speed/accuracy tier |
| `embedding_model`, `use_reranker`, `reranker_model` | Retrieval models (re-seed after changing the embedding model) |
| `admin_token` | Admin API token (auto-generated when empty) |
| `cors_allow_origins`, `allowed_hosts` | API exposure — loopback only by default |

---

## Project layout

```
backend/
  app/
    main.py, serve.py         FastAPI app and launcher (checks, smoke test, stop)
    routers/                  HTTP API: sessions, field sets, config, admin, stats, export, desktop
    domain/documents/         OCR, text layer, rule extraction, LLM enrichment, spelling
    domain/compliance/        quality gates, retrieval queries, validation, reconciliation, report
    domain/regulations/       corpus registry, ingestion, vector store, query, seed, docx2md, pdf2md, upload
    prompts/field_sets/       default field sets (JSON)
    prompts/services/         extraction / validation prompts and check parameters
    rules/                    regulation documents (.md) + corpus.json registry
    store/                    configuration, audit log, paths
  tests/                      pytest suite (no model downloads, all models mocked)
frontend/                     React + Vite + Tailwind UI
desktop/                      desktop launcher (+ experimental portable builder)
docs/                         design notes
```

### Scripts

| Command | Description |
| --- | --- |
| `npm run dev` | Backend + Vite dev server |
| `npm run admin` | Same, opening the admin area |
| `npm run desktop` | Build the UI and open the desktop app (native window) |
| `python desktop/launcher.py --browser` | Open the desktop app in an Edge/Chrome app-mode window instead |
| `npm run desktop:smoke` | Start the real app headless, probe UI + API, stop |
| `npm run seed` | Rebuild the regulation vector store from `backend/app/rules/` |
| `npm run docx2md -- <file.docx>` | Convert Word regulations to Markdown |
| `npm run pdf2md -- <file.pdf> [--ocr]` | Convert PDF regulations to Markdown |
| `npm run check` / `npm run smoke` / `npm run stop` | Environment check / backend smoke test / stop a running backend |
| `npm run clear` | Delete temp sessions, caches and the audit log |
| `npm test` | Backend tests + ruff lint |

---

## Development

```bash
npm test                      # pytest + ruff (backend)
cd frontend && npm run lint   # eslint
cd frontend && npm run build  # type-check + production build
```

Tests never load real models: OCR, embeddings, the re-ranker and Ollama are mocked.

---

## Status and roadmap

- **Done** — generic extraction/check framework driven by field sets; regulation store with
  registry, approval and effective-date filtering; desktop app; Word/PDF → Markdown;
  named regulation sets uploaded from the UI and chosen per check set; check-set form;
  check regions; reading-quality metrics.
- **Next** — turn plain-language check requirements into a check set automatically;
  deterministic rules (`>=`, `between`, `in_list`, regex); splitting regulations by
  article/clause for precise citations; Windows installer for the desktop app.
- **Paused** — the portable (USB) build (`desktop/build_portable.py`) is experimental and not
  maintained for now.

---

## Security notes

- The API only accepts loopback origins and hosts by default; do not set
  `cors_allow_origins=*`. State-changing requests (POST/PUT/PATCH/DELETE) carrying an
  `Origin` outside that list are rejected with 403 — CORS alone does not stop a foreign web
  page from *sending* a multipart upload to the local API.
- Admin endpoints require the admin token; regulation and default field-set edits are
  admin-only.
- Uploaded files are size- and count-limited; field-set IDs and admin file paths are
  validated against path traversal.

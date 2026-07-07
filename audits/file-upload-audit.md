# File Upload Security Audit

**Date:** 2026-07-07  
**Scope:** All file upload and file-derived output handling  
**Upload surface:** One endpoint — `POST /api/v1/batch/upload` (multipart `UploadFile`)  
**Files examined:** `backend/app/api/v1/routes/batch.py`, `backend/app/services/batch_service.py`, `backend/app/services/csv_parser.py`, `backend/app/services/csv_export.py`, `backend/app/main.py`

---

## Summary Risk Score: 2.5 / 10

**The attack surface is extremely small.** Uploaded files are never written to disk, never executed, and are processed entirely in memory with a 5 MB cap and a 50-row limit. Authentication is required; batch uploads are rate-limited to 2 per day. The one meaningful finding is CSV formula injection in the export path, which affects users who open the generated CSV in a spreadsheet application. The remaining gaps are defence-in-depth misses rather than exploitable vulnerabilities.

---

## Findings

---

### FU-01 — Medium — CWE-1236: Improper Neutralization of Formula Elements in a CSV File

**Title:** CSV formula injection in batch export — user-supplied cell values written unsanitized

**Evidence:**

- `backend/app/services/csv_export.py:35–53` — `_csv_row()` writes raw user-supplied values directly to the CSV output:
  ```python
  raw = jr.raw_csv_row or {}          # stored verbatim from the user's upload
  return [
      jr.row_number,
      raw.get("name", ""),            # ← unsanitized
      raw.get("company", ""),         # ← unsanitized
      raw.get("email", ""),           # ← unsanitized
      ...
  ]
  ```
- `backend/app/services/batch_service.py:165`: `raw_csv_row=dict(row)` — the parsed row dictionary (directly from the user's CSV cells) is stored as-is in JSONB.
- Python's `csv.writer` (used at `csv_export.py:71, 111`) does not escape values that begin with `=`, `+`, `-`, `@`, `\t`, or `\r` — the characters that cause spreadsheet applications to interpret a cell as a formula.

**Why it matters:**  
When a user with view access to a batch job exports the results CSV and opens it in Excel or LibreOffice, any cell beginning with `=`, `+`, etc. is evaluated as a formula. An attacker who can upload a batch job can embed a payload that exfiltrates data when their victim opens the exported file. This is a stored CSV injection — the payload survives the round-trip through the database.

**Exploitability:**  
Moderate. Requires the attacker to have a valid account (batch upload requires auth), and the victim must open the exported CSV in a spreadsheet app. The attacker cannot force the victim to open the file, but the risk is real in a workflow where exported CSVs are routinely reviewed in Excel.

**PoC (no real credentials needed):**
```
Upload this CSV:
  Name,Company,Email
  =cmd|' /C calc'!A0,Acme Corp,
```
Export the job at `GET /api/v1/batch/{id}/export`. Open the resulting file in Excel. On Windows, a security prompt appears; accepting it executes `calc.exe`. Modern Excel versions block this behind a warning, but many users click through.

**Remediation:**

Sanitize any cell that begins with a formula-trigger character before writing it to the CSV. Add a helper to `csv_export.py`:

```python
_FORMULA_TRIGGERS = frozenset("=+-@\t\r")

def _sanitize_cell(value: object) -> object:
    if isinstance(value, str) and value and value[0] in _FORMULA_TRIGGERS:
        return "'" + value   # prefix with a single quote — treated as text by spreadsheets
    return value
```

Apply it in `_csv_row`:
```python
return [
    jr.row_number,
    _sanitize_cell(raw.get("name", "")),
    _sanitize_cell(raw.get("company", "")),
    _sanitize_cell(raw.get("email", "")),
    jr.status.value,          # enum value — safe, no user input
    ...
    _sanitize_cell(jr.error_message or (vr.error_message if vr else "") or ""),
]
```

Note: `jr.status.value`, `vr.person_found.value`, etc. are ORM enum values — they cannot contain formula triggers and do not need sanitization.

**Defence-in-depth:** Add `Content-Disposition: attachment` to the response (already present) and `Content-Security-Policy: default-src 'none'` to prevent any active content if the browser renders the CSV. The batch export route already returns `StreamingResponse` with `media_type="text/csv"` and a `Content-Disposition` attachment header — that is correct.

---

### FU-02 — Low — CWE-434: Unrestricted Upload of File with Dangerous Type

**Title:** No MIME type or filename extension validation on the upload endpoint

**Evidence:**

- `backend/app/api/v1/routes/batch.py:38–48`:
  ```python
  async def upload_batch(
      db: DbSession,
      current_user: CurrentUser,
      _rl: BatchRateLimit,
      file: UploadFile = File(description="CSV file. ..."),
  ) -> BatchJobResponse:
      service = BatchService(db)
      try:
          return await service.upload(file, user_id=current_user.id)
  ```
  No check of `file.content_type` or `file.filename` extension before proceeding.

- `backend/app/services/batch_service.py:82–111`: The service reads raw bytes, decodes as UTF-8, then passes to `csv.DictReader`. No content-type gate anywhere in the chain.

**Why it matters:**  
An attacker can upload any file type — a PDF, a JPEG, a zip — and the server will attempt to decode it as UTF-8 CSV. If the file is valid UTF-8 with the right column headers, it passes all validation and creates DB records. This is not exploitable in the current implementation (no code execution, no disk storage), but it is a defence-in-depth failure: the endpoint trusts the client's content negotiation rather than enforcing an explicit whitelist.

**Exploitability:** Minimal in current architecture. Files are never stored or executed — the worst case is a non-CSV file that happens to decode as valid UTF-8 getting processed.

**Remediation:**

Add a whitelist check on `file.content_type` and `file.filename` extension in `batch_service.py:upload()`, before the byte-reading loop:

```python
_ALLOWED_CONTENT_TYPES = frozenset({
    "text/csv",
    "text/plain",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",  # some browsers send this for .csv files
})
_ALLOWED_EXTENSIONS = frozenset({".csv"})

async def upload(self, file: UploadFile, user_id: UUID) -> BatchJobResponse:
    # Whitelist content type (client-declared, so not trusted alone)
    if file.content_type and file.content_type.split(";")[0].strip().lower() not in _ALLOWED_CONTENT_TYPES:
        raise BatchValidationError(
            f"Unsupported file type '{file.content_type}'. Please upload a CSV file."
        )
    # Whitelist extension (defence-in-depth alongside content-type)
    if file.filename:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise BatchValidationError(
                f"Invalid file extension '{ext}'. Only .csv files are accepted."
            )
    ...
```

Note: `Content-Type` is client-declared and cannot be fully trusted, but validating it provides a meaningful defence-in-depth layer and satisfies the whitelist principle. The real gate remains the UTF-8 decode and `csv.DictReader` parse attempt.

Add `import os` to the service imports (or use `pathlib.Path(file.filename).suffix`).

---

### FU-03 — Low — CWE-22 / CWE-79: Unsanitized Filename Stored in Database

**Title:** `file.filename` from multipart header stored verbatim — potential XSS if rendered by frontend

**Evidence:**

- `backend/app/services/batch_service.py:113–118`:
  ```python
  batch_job = BatchJob(
      filename=file.filename or "upload.csv",
      ...
  )
  ```
- `file.filename` is the value from the `Content-Disposition: form-data; filename="..."` header, controlled entirely by the client. It can contain:
  - Path traversal: `../../etc/passwd` or `..\..\windows\system32\config\sam`
  - Null bytes: `legit.csv\x00.exe`
  - XSS payloads: `<script>alert(document.cookie)</script>.csv`
  - Excessively long strings (no length cap applied here before ORM)

- The stored filename is returned in `BatchJobResponse` via `BatchJobResponse.model_validate(batch_job)` and served at `GET /api/v1/batch/` and `GET /api/v1/batch/{id}`.

**Why it matters:**  
Files are not written to disk, so path traversal does not cause server-side harm. However, the raw filename is stored and returned in API responses. If the frontend renders it without HTML escaping (e.g., `innerHTML = job.filename`), this is a stored XSS vector. Null bytes in the filename can confuse some downstream tools.

**Exploitability:** Requires a vulnerable frontend rendering point. The current frontend is not yet implemented (`CLAUDE.md` describes it as stub pages), so this is a pre-emptive fix.

**Remediation:**

Sanitize the filename before storage:
```python
import os
import re

def _sanitize_filename(name: str | None) -> str:
    if not name:
        return "upload.csv"
    # Strip path components, null bytes, and non-printable chars
    base = os.path.basename(name.replace("\\", "/"))
    base = base.replace("\x00", "")
    # Allow only safe filename characters
    base = re.sub(r"[^\w\s\-\.\(\)]", "_", base, flags=re.UNICODE)
    return base[:255] or "upload.csv"
```

Apply in `batch_service.py:upload()`:
```python
batch_job = BatchJob(
    filename=_sanitize_filename(file.filename),
    ...
)
```

---

### FU-04 — Informational — No Magic Number Verification

**Title:** File content is not verified against known binary signatures

**Evidence:**  
No magic-byte check exists in `csv_parser.py` or `batch_service.py`. The pipeline is: raw bytes → UTF-8 decode → `csv.DictReader`.

**Why it matters:**  
CSV files have no standardized magic number, so this is largely not applicable. The natural gatekeeper is the UTF-8 decode and CSV parse: any file whose first 5 MB of bytes is not valid UTF-8 is rejected at `batch_service.py:99–101`, and any file without the required column headers is rejected at `validate_columns()`.

**Recommendation (low priority):**  
Check for known binary magic bytes and reject early, before decoding:
```python
_BINARY_MAGIC = [
    b"\x89PNG",    # PNG
    b"\xff\xd8\xff", # JPEG
    b"PK\x03\x04",  # ZIP/DOCX/XLSX
    b"%PDF",        # PDF
    b"\x7fELF",    # ELF executable
    b"MZ",         # PE executable (Windows)
]

async def upload(self, file: UploadFile, user_id: UUID) -> BatchJobResponse:
    # Peek at first 4 bytes before reading all chunks
    header = await file.read(4)
    await file.seek(0)
    for magic in _BINARY_MAGIC:
        if header.startswith(magic):
            raise BatchValidationError("Binary file detected. Only plain-text CSV files are accepted.")
    ...
```

Note: FastAPI `UploadFile.seek()` is available since `python-multipart` >= 0.0.5.

---

## Top 3 Prioritised Fixes

| Priority | Finding | File | Change |
|---|---|---|---|
| 1 | FU-01 | `csv_export.py:35–53` | Prefix formula-trigger characters with `'` in `_sanitize_cell()` |
| 2 | FU-03 | `batch_service.py:113` | Sanitize `file.filename` via `os.path.basename` + regex before storage |
| 3 | FU-02 | `batch_service.py:82` | Add content-type + extension whitelist before reading bytes |

---

## Checklist Diff

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | File type validation (whitelist) | ❌ FAIL | No `content_type` or extension check on the upload endpoint (FU-02) |
| 2 | File size limits | ✅ PASS | 5 MB streaming limit (`batch_service.py:91–95`); 50-row cap (`MAX_BATCH_SIZE`); 1 MB JSON cap in middleware for non-multipart |
| 3 | Filename sanitization | ⚠️ PARTIAL | Stored verbatim in DB; not used for disk I/O so no path traversal; XSS risk if frontend renders it raw (FU-03) |
| 4 | Anti-virus scanning | N/A | Files never written to disk; content is plain text CSV only; AV scanning not applicable |
| 5 | Storage location (outside webroot) | ✅ PASS | No disk storage at all; file bytes processed in memory and discarded; data stored in PostgreSQL |
| 6 | Direct execution prevention | ✅ PASS | No `subprocess`, `exec`, `eval`, or filesystem write in the upload path; impossible to execute the uploaded content |
| 7 | MIME type validation | ❌ FAIL | `file.content_type` is never checked (FU-02) |
| 8 | Magic number verification | ❌ FAIL | No byte-sequence check; mitigated by UTF-8 decode + CSV parse acting as implicit filter (FU-04, Informational) |
| 9 | Image manipulation library vulnerabilities | N/A | No images uploaded; no PIL/Pillow or similar used anywhere in the upload path |
| 10 | ZIP bomb protection | ✅ PASS | No ZIP decompression; 5 MB hard cap prevents memory exhaustion from any compression-based attack; `MAX_BATCH_SIZE=50` limits row expansion |

### Additional finding not in the standard checklist

| Item | Status | Notes |
|---|---|---|
| CSV formula injection in export | ❌ FAIL | `csv_export.py` writes user-uploaded cell values unsanitized — formulas execute in Excel/LibreOffice (FU-01, Medium) |

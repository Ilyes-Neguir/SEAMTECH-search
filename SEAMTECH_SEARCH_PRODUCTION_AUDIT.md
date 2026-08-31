# SEAMTECH Search — Production Readiness Audit

**Audit scope:** repository `Ilyes-Neguir/SEAMTECH-search`, commit `de2cc97` on `main`  
**Assessment target:** factory deployment searching at least 50 GB of heterogeneous files  
**Assessment date:** 31 August 2026  
**Verdict:** **No-go for production deployment in its current form**

## Executive verdict

SEAMTECH Search is a **clear, compact prototype** with a sensible first architecture: a crawler, extractors, an index layer supporting SQLite and PostgreSQL, a FastAPI API, and a minimal web interface. The code is readable and the repository is easy to understand. The local Python test suite passes with five tests, and the package compiles successfully.

That is not enough for a factory system. The current implementation is not ready to index and serve a large operational corpus safely. The most immediate issue is a **blocking frontend defect**: `app.js` calls `form.addEventListener(...)` without declaring `form`, even though the HTML contains `id="search-form"`. The browser script therefore fails before the search workflow is registered. More seriously, the indexer has no safe handling for incomplete scans: if a network share becomes unavailable, `os.walk` can yield only part of the tree and `remove_missing` can delete the corresponding records from the index. This can silently turn a temporary infrastructure failure into apparent document loss.

The system also indexes content from only six file categories: PDF, DOCX, TXT, CSV, Markdown and LOG. It does not provide content search for spreadsheets, presentations, legacy Office formats, CAD, images, scanned PDFs, archives, email, XML/JSON, source-code variants, or most industrial formats. For a factory that explicitly needs heterogeneous extensions, this is a fundamental product gap, not a minor enhancement.

The correct conclusion is therefore: **the project is a promising prototype, not a production-ready enterprise search product**. It should not be presented to the waiting factory as complete until the blockers below are addressed and a representative corpus test is passed.

## Evidence and confidence

The audit was based on direct inspection of the repository, execution of the existing tests, Python compilation, and review of the runtime/deployment configuration. No representative 50 GB factory corpus, production SMB share, real PostgreSQL instance, or concurrent-user load test was available; therefore, capacity numbers such as indexing duration, query latency and final database size remain unproven.

| Verification | Result | Interpretation |
|---|---:|---|
| Python compilation | Passed | No syntax-level failure was found in the package. |
| Existing automated tests | **5 passed** | The tested surface is small and mostly covers SQLite/configuration behavior. |
| Browser end-to-end test | Not present | The frontend initialization defect is not caught by CI. |
| PostgreSQL integration test | Not present | Production backend behavior is unverified in automation. |
| 50 GB indexing benchmark | Not performed | Scalability and storage requirements are unknown. |
| Security/authentication test | Not present | Access-control behavior is unverified and currently absent in the application. |

## Critical and high-risk findings

| Priority | Finding | Evidence | Factory impact | Required disposition |
|---|---|---|---|---|
| **P0** | The web UI JavaScript crashes during initialization. | `static/app.js` uses `form.addEventListener` but never defines `form`; `static/index.html` defines only `id="search-form"`. | Users cannot reliably search from the advertised browser interface. | Fix immediately and add a browser smoke test. |
| **P0** | Partial or failed scans can remove valid indexed records. | `crawler.py` continues after missing roots and `os.walk` errors; `cli.py` always calls `remove_missing(seen)` after the scan. | A disconnected, denied or temporarily unavailable network share can cause mass disappearance from search results. | Make scan completeness explicit; never delete missing records after an incomplete scan. |
| **P0** | There is no authentication or document-level authorization. | The API exposes `/search`, `/preview`, `/open`, `/health` and `/metrics` without auth middleware or user identity. | Any reachable client can search the configured corpus and preview/open permitted paths. This may violate client confidentiality and internal segregation requirements. | Integrate enterprise identity and enforce ACLs before network exposure. |
| **P0** | The advertised “all extensions” requirement is not implemented. | `extractors.py` handles only `.pdf`, `.docx`, `.txt`, `.csv`, `.md` and `.log`. | Users will receive metadata-only results for many engineering documents and may assume a file is absent when its contents were never indexed. | Define a supported-format matrix and implement a pluggable extraction pipeline with explicit “not extracted” status. |
| **P1** | The indexer is not designed as a recoverable production ingestion pipeline. | Indexing is a synchronous CLI loop with in-memory `seen` keys and no job state, checkpoints, retry queue, quarantine or resumable scan. | A multi-hour scan can lose progress after a crash, parser hang, share outage or machine restart. | Introduce scan jobs, durable checkpoints, retries and failure reports. |
| **P1** | Extraction has no per-file size policy, timeout or isolation. | PDF/DOCX parsers run in-process; plain text reads up to the configured character limit but has no file-size admission policy; exceptions are caught broadly. | A malformed, huge or hostile file can consume excessive CPU/memory or block the entire scan. | Add MIME/signature detection, size limits, timeouts, worker isolation and parser quotas. |
| **P1** | Incremental indexing is too weak for document correctness. | A file is considered unchanged when only `size` and `modified_at` match. | Content changes that preserve those values can remain stale; extractor improvements do not automatically invalidate old content. | Store content hash, extractor version and extraction status; support controlled reindexing. |
| **P1** | SQLite and PostgreSQL search semantics differ and are not contract-tested. | SQLite uses FTS5 token-prefix OR queries; PostgreSQL uses `plainto_tsquery('simple', ...)`. | The same factory query can return different results depending on deployment backend, especially for references, punctuation, phrases and alphanumeric identifiers. | Define a search contract and run the same conformance tests against both backends. |
| **P1** | SQLite query sanitization is incomplete. | `_build_fts_query` removes double quotes only; FTS5 operators and punctuation are not fully normalized. | Certain user queries can produce a server error instead of a result. | Use a safe tokenizer/escaping strategy, handle malformed queries as a normal 400 response, and test punctuation-heavy references. |
| **P1** | Database connections are opened for individual operations without pooling. | `SearchIndex.connect()` creates a new SQLite connection or a new `psycopg2.connect(...)` connection on each operation. | Concurrent factory users and indexing can create avoidable connection overhead and contention. | Use a bounded PostgreSQL pool, explicit timeouts and a documented SQLite single-writer policy. |
| **P1** | Health checks can be expensive and are coupled to request handling. | SQLite `/health` runs `PRAGMA integrity_check`; health also calls full count statistics, and `/metrics` calls `health()` again. | Frequent monitoring or page loads can run expensive database work and amplify load during an incident. | Separate liveness/readiness/deep diagnostics and cache expensive checks. |
| **P1** | The Docker deployment does not solve access to Windows source folders. | The container mounts `config`, `logs` and `data`, but no Windows/SMB source root; the README recommends host-side indexing. | Search may work against a host-populated database while preview/open operates in a different filesystem context; live refresh and file opening are inconsistent. | Choose one supported topology: Windows service with share access, or a Linux worker with explicit SMB mounts and a separate browser-safe open workflow. |
| **P1** | The launcher is not a reliable service manager. | `start_seamtech_search.ps1` uses a hidden detached process, hard-coded `127.0.0.1:8000`, no PID lifecycle and no explicit failure after retries. | Stale processes, port conflicts and silent startup failures will be difficult for factory support teams to diagnose. | Deploy as a managed Windows service or container supervisor with structured logs and readiness checks. |

## The 50 GB question

**Fifty gigabytes is not itself the sizing unit.** The real variables are file count, average file size, number of searchable characters, directory depth, share latency, parser cost, duplicate content, concurrent users and desired freshness. A corpus of 50 GB made of a few large binaries is a different system from 50 GB made of several million small engineering files.

The current design creates several scaling risks:

| Area | Current behavior | Why it matters at factory scale |
|---|---|---|
| Crawl | Recursive `os.walk` in one process | Slow or unreliable network shares can stall or produce incomplete scans. |
| Seen-state | All seen path keys are retained in a Python set | Memory rises with file count, not just total bytes. |
| Missing detection | Existing database keys are loaded and compared in memory | A large index creates additional memory pressure and long cleanup time. |
| Content storage | PostgreSQL stores extracted text and a tsvector; SQLite FTS stores searchable text | The search database can become large relative to the original corpus. |
| Per-file text | Default maximum is 200,000 characters | A large number of recognized documents can create substantial index bloat. |
| Writes | Batches of 250, but each batch still uses a fresh database connection | Batch writes help, but do not provide a durable job or efficient connection lifecycle. |
| Freshness | Scheduled full recursive scans are the documented model | Freshness depends on scan duration and share availability; there is no event-driven or incremental change feed. |
| Search result delivery | Fixed limit of 50 in the UI, no cursor/pagination | The initial page is bounded, but there is no user control over relevance, filters or deep result navigation. |

A production benchmark must measure at least: files discovered per minute, extraction throughput by format, peak RAM, database growth, failed-file rate, query latency at p50/p95/p99, behavior during share interruption, restart recovery, and concurrent search while indexing.

## Heterogeneous extension coverage

The current extractor model is extension-based and narrow. It does not detect actual MIME type or file signature, and it returns an empty string for unsupported formats. That means the system does not distinguish clearly between “the file contains no text,” “the file was not supported,” “the parser failed,” and “the file was not processed yet.” Those states are operationally different and must be visible to users and administrators.

For the factory, the supported-format program should be driven by an inventory of real files rather than by a generic promise to support every extension. The first classification should normally cover:

| Format family | Examples | Current content extraction |
|---|---|---|
| Office documents | DOC, DOCX, XLS, XLSX, PPT, PPTX, ODT | Only DOCX is supported. |
| PDFs and scans | Text PDF, scanned PDF, TIFF/JPEG/PNG | PDF text only; no OCR. |
| CAD/engineering | DWG, DXF, DGN, STEP/STP, IGES/IGS, STL, IFC | Metadata only. |
| Structured/text data | XML, JSON, YAML, HTML, SQL, source code | Metadata only except the small plain-text extension list. |
| Archives | ZIP, 7Z, RAR, TAR/GZ | Not traversed or indexed. |
| Email and collaboration exports | MSG, EML, MBOX | Metadata only. |
| Images and drawings | JPEG, PNG, TIFF, BMP | No OCR or embedded metadata pipeline. |
| Proprietary/vendor files | Site-specific formats | Must be assessed against actual factory applications and licenses. |

“Every extension existing” should therefore be replaced with a **tiered support contract**: fully searchable, metadata-searchable, previewable, queued for future extraction, or intentionally excluded. Unsupported files must remain discoverable by filename/path and must expose the reason content search is unavailable.

## Security and data governance

The application is described as internal, but internal does not mean unrestricted. The current API has no authentication, authorization, tenant/client separation, access audit, rate limiting or explicit network policy. The path validation prevents straightforward access outside configured roots, which is useful, but it is not a substitute for identity-aware access control. The `/open` operation is especially unsuitable for a multi-user server because it asks the server host to open a local path, not the end user’s workstation.

The Docker Compose file also uses a weak example PostgreSQL password directly in configuration. Example credentials must not be reused in a factory environment. Secrets should be injected through a secret store or protected environment mechanism, and database access should be restricted to the application network rather than published broadly.

The project needs a written data-governance decision covering: whether all users may see all clients, whether permissions must follow SMB/NTFS ACLs, whether search snippets may reveal confidential text, how access is audited, how long extracted content is retained, and how backups are encrypted and tested.

## Product and UX gaps

The current UI is intentionally minimal and has no pagination, filters, file-type facets, date/size filters, folder scoping, saved searches, phrase search, exact-reference mode, sorting choice, duplicate grouping, index freshness indicator, extraction-status indicator or administrator view. These are not all mandatory for the first release, but at least exact reference search, folder/file-type filters, result freshness and extraction status are important for production factory use.

The UI also exposes preview and open actions on every result. A safer design would distinguish “copy path,” “download/preview,” and “open on my workstation,” with each action controlled by authorization and deployment topology. Error messages are generic in the browser but backend exceptions are returned as HTTP 500 detail strings, which can expose internal implementation or path information.

## What is good and should be preserved

The repository has several solid foundations. Configuration is centralized and relative paths under `config/` are resolved deliberately. PostgreSQL uses a GIN index over a tsvector, and SQLite is a reasonable local fallback. The indexer uses batched writes, the crawler is generator-based rather than building a complete file list first, and path validation checks that preview/open targets belong to configured roots. The code is small enough to refactor before technical debt becomes expensive.

The existing five tests also establish a useful beginning for SQLite behavior, configuration resolution and basic statistics. They should be expanded rather than discarded.

## Minimum release gates

The factory should not receive a production deployment until all of the following are true:

1. The frontend works in a real browser and has an automated smoke test.
2. Authentication and authorization are implemented, including a decision on inheritance of source-folder ACLs.
3. A disconnected or partially inaccessible source root leaves the existing index intact and produces an alert.
4. Indexing is resumable, observable and restart-safe, with a quarantine/report for failed files.
5. A representative corpus inventory defines supported formats and the expected behavior for unsupported files.
6. PostgreSQL and the chosen deployment topology are tested against the real share environment.
7. A representative large-corpus benchmark records throughput, peak memory, database size and query latency.
8. Backups are encrypted or access-controlled, and restoration is tested successfully.
9. Search behavior is specified and consistent across the production backend and test backend.
10. Security review covers path handling, snippets, preview/open, secrets, network exposure and audit logging.

## Recommended implementation sequence

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 1. Stabilize | Fix frontend initialization, error handling, config validation and automated API/browser tests. | Search, preview and health workflows pass in CI. |
| 2. Make scans safe | Add scan job state, root availability checks, completeness flags, checkpoints, retries and failure reports. | A simulated share outage never deletes valid records. |
| 3. Build extraction platform | Add MIME/signature detection, plugin extractors, OCR where required, size/time/memory limits and extractor versioning. | Factory format matrix has measured coverage and explicit unsupported states. |
| 4. Secure access | Add SSO/LDAP/identity integration, authorization, audit logs, secret management and network hardening. | A user can see only authorized content and every sensitive action is auditable. |
| 5. Scale and operate | Use PostgreSQL pooling, durable metrics, structured logs, alerts, deployment service management and benchmark-driven indexes. | Load test meets agreed latency and recovery targets. |
| 6. Pilot | Run against a read-only representative subset of the factory share, compare results with manual ground truth and collect user feedback. | Acceptance sign-off from engineering, IT/security and factory users. |

## Final answer to “what does it lack to be perfect and product-ready?”

It lacks **production safety**, not merely more features. Specifically, it needs a reliable and resumable ingestion system, a broad and measurable extraction strategy, authorization tied to factory permissions, safe behavior during network failures, real PostgreSQL and concurrency testing, operational observability, and a browser workflow that actually runs. It also needs a defined support contract for formats instead of an implicit expectation that arbitrary extensions will be searchable.

The fair engineering assessment is **approximately prototype/MVP stage**, not finished product stage. I would approve continued development and a controlled pilot on a non-critical copy of the data. I would **not** approve connecting it as the authoritative search system for the factory’s live 50 GB corpus today.

## Repository references

[1]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/README.md "SEAMTECH Search README"
[2]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/seamtech_search/static/app.js "SEAMTECH Search browser application"
[3]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/seamtech_search/static/index.html "SEAMTECH Search HTML interface"
[4]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/seamtech_search/crawler.py "SEAMTECH Search crawler"
[5]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/seamtech_search/extractors.py "SEAMTECH Search extractors"
[6]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/seamtech_search/indexer.py "SEAMTECH Search indexer"
[7]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/seamtech_search/api.py "SEAMTECH Search API"
[8]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/seamtech_search/cli.py "SEAMTECH Search CLI"
[9]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/docker-compose.yml "SEAMTECH Search Docker Compose configuration"
[10]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/scripts/start_seamtech_search.ps1 "SEAMTECH Search Windows launcher"
[11]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/tests/test_indexer.py "SEAMTECH Search indexer tests"
[12]: https://github.com/Ilyes-Neguir/SEAMTECH-search/blob/main/tests/test_config.py "SEAMTECH Search configuration tests"

# Project Structure & Directory Layout

```
SEAMTECH-search/
├── config/                         # Configuration templates and runtime configs
│   ├── config.example.json         # Reference JSON configuration file
│   └── config.json                 # Active local configuration (gitignored)
├── docs/                           # Comprehensive technical and operational documentation
│   ├── PROJECT_REPORT.md           # Full technical, architectural, and business report
│   ├── PHASE0_RAPPORT.md           # Phase 0 report: delivered instruments, measured numbers, blockers
│   ├── REPORT.md                   # Production readiness and operations summary
│   ├── STRUCTURE.md                # Repository layout and component directory
│   ├── IMPLEMENTATION_PLAN.md      # Architecture roadmap and deliverable checklist
│   └── TLS.md                      # Production TLS, reverse proxy, and network hardening guide
├── frontend/                       # Next.js 16 Web UI & Authenticated Proxy
│   ├── app/                        # App Router pages and server-side API proxy routes
│   │   ├── api/                    # Server-side proxy routes forwarding to FastAPI backend
│   │   │   ├── health/             # Proxy for backend health
│   │   │   ├── imports/            # Proxy for scan, confirm, upload, cancel, retry, corrections
│   │   │   ├── open/               # Proxy for Windows open file action
│   │   │   ├── preview/            # Proxy for document and folder previews
│   │   │   └── search/             # Proxy for full-text search
│   │   ├── globals.css             # Tailwind CSS styles
│   │   ├── layout.tsx              # Root HTML shell and metadata
│   │   └── page.tsx                # Main dashboard page
│   ├── components/                 # React UI components
│   │   ├── correction-form.tsx     # Interactive manual parameter correction form
│   │   ├── import-panel.tsx        # Ingestion wizard (scan, upload, candidates, progress)
│   │   ├── index-status.tsx        # Service health and index telemetry badge
│   │   ├── match-badge.tsx         # Search match category visual pills
│   │   ├── preview-panel.tsx       # Document preview drawer
│   │   ├── result-item.tsx         # Individual search result card
│   │   ├── results-list.tsx        # Paginated search results list
│   │   ├── search-bar.tsx          # Real-time search query input
│   │   └── ui/                     # Base UI components (buttons, inputs)
│   ├── e2e/                        # Playwright end-to-end integration test suite
│   │   ├── import.spec.ts          # E2E import workflow tests
│   │   ├── search.spec.ts          # E2E search flow tests
│   │   └── global-setup.ts         # Test environment initialization
│   ├── hooks/                      # Custom React hooks (e.g. useSearch)
│   ├── lib/                        # Client/server utilities, types, and backend proxy client
│   │   ├── backend.ts              # Server-side fetch client injecting SEAMTECH_AUTH_TOKEN
│   │   ├── format.ts               # Date, byte size, and dimension formatters
│   │   ├── sample-data.ts          # Fallback mockup data for offline UI development
│   │   └── types.ts                # TypeScript interface definitions
│   ├── Dockerfile                  # Multi-stage standalone Next.js container image
│   ├── package.json                # Frontend dependencies (Next.js 16, React 19, Lucide, Tailwind)
│   └── playwright.config.ts        # Playwright E2E configuration
├── sample_data/                    # Sample client folders and engineering fixtures
│   ├── CLIENT-123/                 # Sample client dossier (PDF drawing, XLSX BOM, notes)
│   └── CLIENT-456/                 # Sample client notes
├── scripts/                        # Automation, operational, and maintenance scripts
│   ├── backup_postgres.ps1         # Automated PostgreSQL backup script
│   ├── backup_sqlite.ps1           # SQLite fallback backup script
│   ├── benchmark_indexing.py       # Performance and throughput benchmarking utility
│   ├── bootstrap.py                # Environment bootstrap helper
│   ├── ensure_postgres.ps1         # Auto-provisioning script for local PostgreSQL container
│   ├── inventaire_archive.py       # Phase 0 read-only archive inventory (types, years, duplicates, scans, fiche locations, gabarit families)
│   ├── restore_postgres.ps1        # PostgreSQL database restore script
│   ├── restore_sqlite.ps1          # SQLite fallback database restore script
│   ├── run_indexing.ps1            # Scheduled directory indexing script
│   ├── start_seamtech_search.ps1   # Native desktop launcher script
│   └── validate_extraction.py      # Extraction harness: historical per-document review + Phase 0 field-by-field measurement against a ground-truth JSON (--verite)
├── seamtech_search/                # Core Python package
│   ├── __init__.py                 # Package version and export definitions (v0.4.0)
│   ├── __main__.py                 # CLI execution entry point
│   ├── anchors.py                  # Standardized technical anchors for classification
│   ├── api.py                      # FastAPI REST application, rate limiting, and routes
│   ├── audit.py                    # Append-only audit logger with token fingerprinting
│   ├── cli.py                      # Command-line interface subcommands (serve, index, stats, cleanup)
│   ├── config.py                   # Pydantic v2 configuration models and env parsing
│   ├── crawler.py                  # Directory crawler with symlink safety and batched traversal
│   ├── detection_fiches.py         # Structural fiche detection (lexicon + table grid, explainable verdict)
│   ├── lexique.py                  # Configurable fiche lexicon loader (config/lexique_fiches.json)
│   ├── extraction_worker.py        # Process-isolated extraction helper
│   ├── extractors.py               # Text, PDF (pdfplumber/pypdf), XLSX (openpyxl), DOCX extractors
│   ├── import_pipeline.py          # Two-phase dossier import, multi-sheet analysis, PDF/DOCX generation
│   ├── indexer.py                  # PostgreSQL/SQLite database manager, pooling, and FTS
│   ├── jobs.py                     # Asynchronous job state manager and recovery
│   ├── models.py                   # Pydantic data schemas for documents and search results
│   ├── redis_store.py              # Redis task queues, sliding-window rate limiting, and job cache
│   ├── retention.py                # Storage retention manager and pre-flight disk guard
│   ├── storage.py                  # S3-compatible object storage client (MinIO, R2, AWS S3)
│   └── worker.py                   # Dedicated background task worker loop
├── tests/                          # Automated Pytest suite
│   ├── test_api.py                 # FastAPI route and error contract tests
│   ├── test_config.py              # Configuration validation and environment override tests
│   ├── test_extractors.py          # Text, PDF, DOCX, and spreadsheet parser tests
│   ├── test_import_pipeline.py     # Multi-sheet analysis and PDF/Word report generation tests
│   ├── test_import_workflow.py     # Two-phase scan/confirm, corrections, and retry backoff tests
│   ├── test_indexer.py             # Database operations, search rankings, and schema tests
│   ├── test_jobs_api.py            # Async jobs API, 202 acceptance, and cooperative cancellation
│   ├── test_pooling.py             # PostgreSQL connection pooling and timeout tests
│   ├── test_postgres_integration.py# Live PostgreSQL integration test
│   ├── test_redis.py               # Redis rate limiting, task queues, and fast-path cache tests
│   ├── test_reindex_skip.py        # Incremental scan optimization tests
│   ├── test_sample_fixture.py      # Sample fixture ingestion verification tests
│   ├── test_scan_safety.py         # Directory boundary, symlink, and scan safety tests
│   └── test_storage.py             # S3/MinIO client, presigned URLs, and live integration tests
├── .env.example                    # Template for environment variables
├── .github/workflows/ci.yml        # GitHub Actions CI pipeline with Postgres/Redis services
├── .gitignore                      # Git exclusion rules
├── CHANGELOG.md                    # Detailed version changelog
├── Dockerfile                      # Container image for FastAPI backend and worker
├── docker-compose.yml              # Complete multi-container deployment stack
├── pyproject.toml                  # Python package metadata, dependencies, pytest, and ruff settings
├── README.md                       # Main project README and user guide
├── requirements-local.txt          # Development dependencies
└── requirements.txt                # Production Python dependencies
```

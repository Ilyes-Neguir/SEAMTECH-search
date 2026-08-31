# SEAMTECH Search — Implementation Plan

## Objective

Deliver a production-oriented internal search service for large factory file shares while preserving the existing Python/FastAPI/PostgreSQL direction.

## Non-negotiable safety rules

The index must never delete records after a scan that is incomplete, has inaccessible roots, or reports traversal errors. Every indexing run must have a durable identity, status, completeness state, counters, and an error report. Deletion of missing files is allowed only after all configured roots have been verified and traversal completed without errors.

The service must distinguish metadata indexing from content extraction. Unsupported, skipped, oversized, locked, malformed and extraction-failed files remain searchable by metadata and expose an explicit status.

The API must remain safe by default. It binds to localhost unless deliberately configured otherwise, does not expose raw exception details, validates paths against configured roots, and supports an authentication boundary that can be enabled for network deployment. A production deployment must not rely on the server opening files on a remote user’s workstation.

## Target implementation layers

| Layer | Target responsibility |
|---|---|
| Configuration | Strict validation, environment overrides, safe defaults, source-root health policy, extraction limits, auth settings and database timeouts. |
| Crawler | Root-by-root scan reports, traversal errors, symlink policy, stable path identity, file metadata and no silent failures. |
| Extraction | Plugin registry by extension and MIME/signature, bounded work, structured result status, text normalization and optional external tools. |
| Index store | Durable document state, content hash, extractor version, extraction status, scan metadata, backend parity and efficient upserts. |
| Scan orchestration | Resumable job records, lock against concurrent scans, checkpoints, retry/quarantine behavior and safe cleanup. |
| API | Search, health/readiness, scan status, preview with limits, safe errors, request IDs and optional auth/authorization boundary. |
| UI | Working browser initialization, search filters/status, extraction-status visibility, pagination-ready API usage and safe actions. |
| Operations | Structured logs, metrics, Docker/Windows topology documentation, secret handling, backups and restore verification. |

## Delivery sequence

1. Correct the browser defect, add strict config validation, improve API errors and add regression tests.
2. Add scan state and safe cleanup semantics before expanding format support.
3. Add durable document fingerprints and extraction status/versioning.
4. Add practical extractors available in the deployment image: PDF, DOCX, legacy Office through LibreOffice conversion where feasible, archives with traversal limits, structured text, images/PDF OCR where explicitly enabled, and metadata fallback for proprietary/CAD formats.
5. Add PostgreSQL pooling/timeouts, health/readiness separation, authentication boundary, audit logging hooks and secure deployment defaults.
6. Improve the UI and update documentation with an explicit format support matrix.
7. Run unit, integration, API, security, failure-recovery and benchmark tests; document what still requires the factory environment.

## Important boundary

Universal extraction of every existing extension cannot be guaranteed by code alone. Proprietary CAD and vendor formats may require licensed SDKs or the originating application. The product will therefore implement a pluggable extraction contract and a transparent support matrix rather than falsely claiming universal content search.

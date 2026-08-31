# Project structure

- config/: runtime and example configuration files
- data/: generated runtime data, backups, and local SQLite fallback index
- logs/: application logs
- sample_data/: sample content for local development
- scripts/: setup, launch, indexing, backup, and restore scripts
- seamtech_search/: Python package containing the crawler, indexer, API and CLI
- tests/: regression tests
- Dockerfile: container image for the FastAPI app
- docker-compose.yml: PostgreSQL plus web app deployment for production-style runs
- SEAMTECH Search.cmd: Windows launcher used by the Desktop shortcut

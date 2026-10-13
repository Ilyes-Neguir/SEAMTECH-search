# Procédure reproductible — 2026-10-13 — SHA 5438b9e

**SHA : `5438b9e22d0a2d0e7bad58580db92fc016be9db7`**
**Branche : `arena/c154b2fa-seamtech-search`**

## 1. Prérequis (aucune install PC user, aucune infra entreprise)

- Linux Debian/Ubuntu (CI : ubuntu-24.04)
- PostgreSQL 16 + extension pgvector + unaccent + pg_trgm
- Redis 7
- MinIO officiel (tag épinglé `RELEASE.2025-09-07T16-13-09Z`, reconstruit depuis sources archivées via `scripts/construire_image_minio.sh` car registries gone)
- Tesseract 5.x + `tesseract-ocr-fra` + `tesseract-ocr-eng` + `poppler-utils`
- Python 3.11/3.12/3.13, Node 22, pnpm 9
- Playwright Chromium (`pnpm exec playwright install --with-deps chromium`)

Données synthétiques/anonymisées, services non exposés publiquement, pas de service payant/secrets externes sans accord.

## 2. Cloner et vérifier SHA

```bash
git clone https://github.com/Ilyes-Neguir/SEAMTECH-search.git
cd SEAMTECH-search
git checkout arena/c154b2fa-seamtech-search
git rev-parse HEAD  # doit afficher 5438b9e22d0a2d0e7bad58580db92fc016be9db7
```

## 3. Backend — lint, compile, suites

```bash
python -m pip install --upgrade pip setuptools
pip install -r requirements-dev.txt
ruff check .
python -m compileall -q seamtech_search

# Suite sans service (exclut postgres, s3, perf, redis_queue, ocr_suite)
pytest -q -rf -m "not postgres and not s3 and not perf and not redis_queue and not ocr_suite"

# Suite PostgreSQL (exige SEAMTECH_TEST_DATABASE_URL)
export SEAMTECH_TEST_DATABASE_URL=postgresql://seamtech:seamtech_test_password@localhost:5432/seamtech_search_test
export SEAMTECH_TEST_REDIS_URL=redis://127.0.0.1:6379/1
pytest -m "postgres and not perf and not sauvegarde and not redis_queue and not ocr_suite" -v -rf
# Garde-fou : passed>0 skipped==0

# File durable (Redis réel + PG)
pytest -m "redis_queue" -v -rf

# Coverage
pytest -m "not s3 and not perf" -q -rf --cov=seamtech_search --cov-report=term-missing --cov-report=json:coverage.json
python scripts/coverage_gate.py coverage.json

# Perf (sans --cov, chauffe jetée)
export SEAMTECH_PERF_JSON=perf-mesures.json
pytest -q -m perf -rf
cat perf-mesures.json
```

## 4. Frontend

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm exec tsc --noEmit
pnpm audit --prod --audit-level=high
pnpm build
```

## 5. Docker

```bash
docker build -t seamtech-search:ci -f Dockerfile .
docker build -t seamtech-search-frontend:ci frontend/
docker run -d --name seamtech-ci -p 8000:8000 \
  -e SEAMTECH_AUTH_TOKEN=test-token \
  -e SEAMTECH_HOST=0.0.0.0 \
  -e SEAMTECH_ALLOW_NETWORK_ACCESS=true \
  -e SEAMTECH_BEHIND_TLS_PROXY=true \
  -e SEAMTECH_ROOT_PATHS=/tmp \
  -e SEAMTECH_MIN_FREE_BYTES=0 \
  seamtech-search:ci
curl -fsS http://127.0.0.1:8000/live
docker stop seamtech-ci && docker rm seamtech-ci
```

## 6. OCR

```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng poppler-utils
tesseract --version
tesseract --list-langs
pytest -m ocr_suite -v
```

Qualité :
```bash
python - <<'PY'
from seamtech_search.ocr.pipeline import ocriser_fichier
from pathlib import Path
res = ocriser_fichier(Path("tests/fixtures/ocr/ocr_propre.pdf"), langue="fra")
print(res)
PY
```

## 7. E2E — parcours navigateur prioritaire scan

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm exec playwright install --with-deps chromium

# Backend avec enable_ocr=true
# config/config.json : {"enable_ocr": true, ...} ou env SEAMTECH_ENABLE_OCR=true
# e2e/backend.config.json déjà enable_ocr true

# Sans PostgreSQL (SQLite)
pnpm test:e2e  # inclut scan-browser-ocr.spec.ts

# Avec PostgreSQL (live)
export SEAMTECH_E2E_DATABASE_URL=postgresql://seamtech:seamtech_test_password@localhost:5432/seamtech_search_test
pnpm exec playwright test e2e/validation.spec.ts e2e/recherche-badge.spec.ts e2e/recherche-dimension.spec.ts --reporter=list,json
pnpm exec playwright test e2e/concurrence.spec.ts --reporter=list,json  # 7 scénarios
pnpm exec playwright test e2e/doublons.spec.ts --reporter=list,json  # 2 tests bandeau
pnpm exec playwright test e2e/auth.spec.ts --reporter=list,json  # 26 tests comptes
```

**Parcours scan-browser-ocr :**
- Génère PDF image-only via `frontend/e2e/generate_scan_pdf.py` → `tests.helpers_ocr_scan.gen_pdf_fr` (terme unique `ZEPHYRINE_ÉLÉGANCE_42` absent métadonnées, pdfplumber 0 texte)
- `signIn(page)`, `Recherche avancée`, `Importer un dossier`
- `input[type="file"]` `setInputFiles(pdfPath)` (glisser-déposer réel, même logique que drop)
- Attend `fichier prêt à envoyer`, `fileName`, clique `Envoyer et analyser`
- Attend `Fichiers détectés dans`, `Choisir le PDF technique` (first() pour éviter duplicate)
- Clique `Importer les fichiers sélectionnés`, attend `État :`, `Fichiers :`, `PDF techniques :`
- Recherche `ZEPHYRINE_ÉLÉGANCE_42`, vérifie résultats, ouverture aperçu, téléchargement `Télécharger le PDF source`, compare SHA-256 original vs téléchargé, attach preuve JSON

**Cas erreur :**
- PDF invalide (`ceci n'est pas un pdf`) → attend soit erreur immédiate, soit Fichiers détectés puis import→erreur, preuve pas de faux succès
- Blank PDF (image blanche) → attend `Aucun|vide|exploitable|Warning|État|Fichiers détectés|Choisir le PDF`

## 8. Sauvegarde/restauration

```bash
# MinIO local
bash scripts/construire_image_minio.sh
docker run -d --name minio -p 9000:9000 -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin123 quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z server /data
bash scripts/provisionner_stockage.sh  # buckets + versioning + identités restreintes

export SEAMTECH_TEST_DATABASE_URL=postgresql://...
export SEAMTECH_S3_ENDPOINT_URL=http://127.0.0.1:9000
export SEAMTECH_S3_BUCKET=seamtech-backups
export SEAMTECH_S3_ACCESS_KEY=seamtech-sauvegarde-ci
export SEAMTECH_S3_SECRET_KEY=ci-backup-secret-not-for-production
export SEAMTECH_BACKUP_BUCKET=seamtech-backups
export SEAMTECH_MINIO_CONTAINER=minio

pytest -m sauvegarde -v --junitxml=sauvegarde-junit.xml
# Garde-fou : collectés==exécutés, ≥19 passés, 0 skipped
```

Mesures publiées `sauvegarde-mesures.jsonl` : dump octets, archive fichiers, durée, restauration 50k fiches.

## 9. Hors-ligne réel

Voir `.github/workflows/ci.yml` job `hors-ligne-reel` :

- Compte `seamtech-app`, pile web+worker+front sous ce compte
- iptables `SEAMTECH_HORS_LIGNE` + ip6tables `SEAMTECH_HORS_LIGNE6` avec `--uid-owner`
- Contrôle négatif IPv4 : `getent ahostsv4 example.com` depuis compte, curl adresse littérale `--noproxy`, compteur REJECT doit augmenter
- Contrôle négatif IPv6 : route globale ? ip6tables + curl `[2606:4700:4700::1111]` littéral, sinon preuve absence route
- Contrôle positif `/health` local
- `iptables -Z` après contrôles, avant épreuve
- `e2e/hors-ligne.spec.ts` avec `SEAMTECH_E2E_HORS_LIGNE=1` et `SEAMTECH_E2E_DATABASE_URL=""`
- Relevé final 0 paquet rejeté

## 10. Installation native propre (Debian/Ubuntu vierge)

```bash
bash scripts/qualification_native.sh
```

Détails dans `PROCEDURE_NATIVE.md` et `deliverables/qualification-deploiement/procedure_native/` : apt PG+pgvector, Redis, MinIO officiel compilé, API+worker+frontend hôtes, compte nominatif, lot worker 7792-SO, OCR hôte, /recherche, /open, sauvegarde/restore, kill+relance API, notice `native-summary`.

## 11. CI distante — rejouer

```bash
gh run list --branch arena/c154b2fa-seamtech-search
gh run view 38458818644 --json jobs --jq '.jobs[] | "\(.name) \(.conclusion)"'
gh api repos/Ilyes-Neguir/SEAMTECH-search/commits/5438b9e22d0a2d0e7bad58580db92fc016be9db7/check-runs --jq '.check_runs[] | "\(.name) \(.conclusion)"'
```

Tous les jobs doivent être `success` (13 jobs CI + 2 qualification + perf comparaison).

## 12. Preuves JUnit

- `sauvegarde-junit.xml` : tests, skipped, failures, errors
- Playwright JSON `report-*.json` : stats expected/flaky/skipped/unexpected, annotations `mesure-phase1`
- `perf-mesures.jsonl`, `sauvegarde-mesures.jsonl`
- Annotations `::notice`/`::error` lisibles via API `gh api .../check-runs/{id}/annotations`

Aucun JSON PASS réutilisé après run échoué : chaque run vert est sur SHA 5438b9e.

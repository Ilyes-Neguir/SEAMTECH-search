# Statuts de clôture A–D

Les quatre points **ne sont pas tous corrigés**. PR #37 **non fusionnée**.
Aucun PV humain. Recette historique intacte. Pas un PASS production.

## Identité

| | |
|---|---|
| Départ | `8dd5ae7340a0eb1023bd73ef5d884c4538efbc09` |
| Lot 3 | `0665a684133f31e5c5742faeeb1fe3104ab2a721` (CI A–C en échec) |
| **SHA applicatif testé** | `a3506c84892e97a8993fe6a9657cc93019f002d3` |
| Recette historique | préservée |

Détail des runs : `preuves/ci-a3506c8.json`.

## A — Recherche — **CORRIGÉ ET TESTÉ** / **EN ATTENTE DE VALIDATION HUMAINE**

1. **Modifié** : clé `normaliser_cle_recherche` (casse, accents, ligatures œ→oe) distincte de `sans_accents` (dédup, pas de ligatures). SQL 023 `SET search_path`.
2. **Exécuté** : job `backend (3.12)` postgres `passed=279 skipped=0` ; workflow qualification-reserves `tests=74 skipped=0`.
3. **Résultat** : parité IDs+ordre, 022→023, mutation, filtres, pagination, arabe, HTTP `/recherche` `/search` suggestions, volumes 800 et 2000.
4. **Preuve** : run [38405546959](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38405546959) notice `suite-postgres` ; run [38405546902](https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/38405546902) notice `qualification-summary`.
5. **Périmètre** : corpus **synthétique**, PG 16 CI. Mesures 800/2000 **indicatives**. Un Seq Scan à 2000 lignes n'est pas un défaut automatique.
6. **Externe** : revue humaine, fonds réel. Le job **perf** CI (p95 324 ms ≥ 250 ms seuil runner) a échoué — hors clôture A.

## B — Sauvegarde — **CORRIGÉ ET TESTÉ** (MinIO officiel CI) / **EN ATTENTE HUMAINE**

1. **Modifié** : objet-only ≠ manquant local ; restore réécrit la clé d'origine ; inventaire S3.
2. **Exécuté** : job `sauvegarde` SHA `a3506c8`, MinIO officiel, identité restreinte `seamtech-backups`. Garde-fou **0 skip**.
3. **Résultat** : B-03 `recherche_test_80999acdf0_obj restaurée` ; B-02 `…_restauree` ; dumps relu SHA.
4. **Preuve** : job 115324072699, run 38405546959 ; qualification 74/0/0 inclut `test_aller_retour_objets_minio_sources_indisponibles`.
5. **Périmètre** : MinIO CI, pas R2, pas le bucket atelier.
6. **Externe** : RPO/RTO et fournisseur atelier non tranchés.

## C — OCR — **CORRIGÉ ET TESTÉ** (parcours CI) / sandbox local **BLOQUÉ**

1. **Modifié** : extracteur v6 `fra+eng` si les deux paquets sont là ; parcours crawl→upsert→`/search`→`/open`.
2. **Exécuté** : qualification-reserves (tesseract apt fra+eng + PG + MinIO) **74 tests, 0 skip** — donc C-03 et scan anglais **exécutés**, pas ignorés. Job `ocr` SUCCESS tesseract 5.3.4 (taux propre 1.000 / dégradé 0.955) ; ce job n'a pas PostgreSQL.
3. **Résultat** : scans fra/eng, dégradé non vide, PDF texte et xlsx sans régression, PDF invalide en erreur, original préservé, mots retrouvés via `/search`, octets `/open`.
4. **Preuve** : run 38405546902 `tests=74 skipped=0` ; job ocr 115324073055.
5. **Périmètre** : fixtures + scan anglais synthétique. Pas le fonds réel.
6. **Externe** : sandbox local sans tesseract (CDN Releases fermé).

## D — Déploiement — **PRÉPARÉ NON EXÉCUTÉ**

La procédure documentée est Docker Compose (`docs/DEPLOYMENT.md`). La cible d'essai autorisée est Linux **natif**. Les jobs `recette-locale` / `integration` **verts** sur ce SHA sont Docker — **ce n'est pas D**.

Prérequis : procédure native Debian/Ubuntu approuvée, hors Docker, sur machine vierge.

**EN ATTENTE DE VALIDATION HUMAINE.**

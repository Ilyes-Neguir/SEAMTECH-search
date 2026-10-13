# Statuts de clôture A–D

Périmètre d'exécution : bac à sable Linux glibc 2.36, Python 3.11.2,
PostgreSQL 16.2 (pgserver + `unaccent`/`pg_trgm` compilés PGXS), sans
tesseract, sans ocrmypdf, sans MinIO, sans Docker, sans Windows.
Aucune installation réelle d'entreprise n'a été touchée.

Aucune des clôtures ci-dessous ne signifie « zéro risque », « parfait »
ou « tout est validé ».

## A — Normalisation générale de la recherche

**Statut : CLOTURÉ SOUS PÉRIMÈTRE sandbox PostgreSQL 16.2.**

- Ce n'est pas un cas particulier « génois ». La clé
  `normaliser_cle_recherche` (Python) et la fonction IMMUTABLE
  `seamtech_texte_recherche` (SQL, migration `023_recherche_normalisee`)
  s'appliquent aux champs réellement recherchés (trigrammes titre / type /
  client / bateau / notes / chunks PDF, et suggestions).
- Preuve **avant** correction (même script, commit `8dd5ae7`) :
  `preuves/A-03-avant.json` — `defaut_observe: true` (classement GENOIS ≠
  GÉNOIS ; `word_similarity` Gennaker 0,43 vs 0,14 ; ligature non
  couverte par unaccent seul).
- Preuve **après** : `preuves/A-03-apres.json` — `parite_ensembles` vrai
  pour toutes les familles semées ; `defaut_observe: false` ; **même
  ordre** GENOIS / GÉNOIS / génois / NFC.
- Tests exécutés : `tests/test_normalisation_cle_recherche.py` (unitaires
  familles, NFC/NFD, ligatures, nbsp) ;
  `tests/test_recherche_normalisation.py` (IDs + ordre, codes, filtres
  exacts, SQL vs Python, suggestions, index d'expression).
- Originaux non réécrits : la clé est calculée à la volée ; `titre` reste
  « Génois lourd First 30 ».
- Données existantes : index d'expression GIN, pas de rewrite des
  colonnes. Données nouvelles : mêmes fonctions.
- Perf : mesurée sur corpus de 11 fiches (`duree_ms < 2000`, typiquement
  ≪ 100 ms). **Pas un SLA atelier / volume de production.**
- Couverture linguistique : latin (aigu, grave, circonflexe, tréma,
  cédille, tilde) + ligatures `œ/oe`, `æ/ae`, `ß/ss`. Pas de
  translittération arabe, cyrillique, CJK.
- Non-régression : codes `0701-GV-001`, filtres `type_voile=GEN`, cotes
  numériques inchangés. Accent-insensible ≠ flou ≠ sous-chaîne.

**Reste ouvert :** volume réel, Windows, ICU (PostgreSQL compilé
`--without-icu`), non-latin.

## B — Sauvegarde / restauration des pièces référencées

**Statut : CLOTURÉ SOUS PÉRIMÈTRE fichiers locaux + dump PostgreSQL
(sandbox). S3/MinIO NON qualifiés.**

- `sauver(..., inclure_references_base=True)` copie `documents.path`
  vers `fichiers/{id}/` et, si un client documents est fourni,
  `documents.object_key` vers `objets/{id}/`. L'import d'arborescence
  locale **n'impose pas** une réplication S3.
- Fichier manquant → `complet=false`, code CLI 2, liste `manquants`
  (test `test_sauvegarde_references_fichier_manquant_n_est_pas_complete`).
- Aller-retour exécuté : dump + copie locale → suppression de l'original
  → `restaurer` dans une **base neuve** → `restaurer_fichiers_locaux`
  vers un répertoire **distinct** → SHA-256 identique, recherche
  `GENOIS` retrouve `7792-SO` sans relire la source
  (`test_aller_retour_fichiers_vers_cible_distincte`).
- Verrou `fcntl` anti-chevauchement ; destination inaccessible explicite ;
  manifeste corrompu / empreinte invalide refusés.
- RPO/RTO : **décision d'exploitation à prendre**, non figés.

**Non exécuté donc non PASS :** MinIO, R2, Windows, gel des écrivains
atelier, restauration 50 000 fiches. Les tests historiques
`test_sauvegarde_restauration.py` n'ont pas été rejoués avec succès ici
(DSN socket UNIX mal découpé dans *leur* harnais `rsplit("/")` — défaut
de test, pas une preuve produit).

## C — OCR réel (import → OCR → index → recherche → téléchargement)

**Statut : BLOQUÉ — NON CLOTURÉ.**

- Code : extracteur v5, relais `ocrmypdf` puis pipeline tesseract pages
  (`_extract_pdf_via_pipeline_ocr`). Timeout → statut `timeout`.
- Dans ce bac à sable : `tesseract`, `ocrmypdf`, `pdftoppm` **absents**
  (`apt` indisponible). Les tests `tests/test_ocr_qualification.py`
  s'auto-ignorent avec motif explicite (4 skipped) — **ce n'est pas un
  PASS**. `SEAMTECH_OCR_OBLIGATOIRE=1` les ferait échouer.
- Contrôle négatif exécuté : PDF sans couche texte + binaires absents →
  `unavailable` explicite, jamais `extracted` vide
  (`test_pdf_scanne_ocr_absent_n_est_pas_un_succes`).
- Non-régression texte natif : PDF ReportLab et XLSX extraits sans OCR.

On ne simule pas tesseract pour déclarer un PASS.

## D — Préparation déploiement cible + protocole d'acceptation

**Statut : PRÉPARÉ, NON SIGNÉ.**

- Cible de référence **proposée, non approuvée** : [CIBLE_REFERENCE.md](CIBLE_REFERENCE.md).
- Protocole opérateur : [PROTOCOLE_ACCEPTATION.md](PROTOCOLE_ACCEPTATION.md).
- Ce dossier n'est **pas** un PV. Il ne remplace pas la validation d'un
  responsable / opérateur métier. Trois sessions automatisées ≠ trois
  validations humaines.

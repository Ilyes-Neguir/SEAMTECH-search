# Protocole d'acceptation métier (non signé)

À exécuter par un **opérateur / responsable** sur un environnement
autorisé. Ce fichier est une check-list, pas un PV. Aucune case n'est
cochée ici au nom d'un humain.

Préparer : base dédiée, comptes de test, dossier d'archive de recette
(pas le fonds réel sans autorisation), `SEAMTECH_PG_BINDIR` si besoin.

## A — Recherche (casse / accents / ligatures)

Pour chaque famille : terme métier, nom de client, nom de fichier,
description, mot ordinaire.

1. Créer (ou importer) un document dont le **titre/champ** contient la
   forme accentuée (`Génois`, `Façade`, `Réparation`, `été`, `Noël`,
   `câble`, `Müller`, `São`, `cœur`).
2. Rechercher la forme **sans accent**, la forme **accentuée**, la
   forme **NFD**, la forme **mélange de casse**.
3. Comparer les **identifiants** (codes), pas seulement le nombre de
   hits, et l'**ordre**.
4. Inverser : document sans accent, requête accentuée.
5. Ligatures : `cœur` ↔ `coeur`.
6. Contrôle négatif : un code affaire (`0701-GV-001`) et un filtre
   exact (`type_voile=GEN`) ne doivent pas s'élargir.
7. Vérifier que le titre affiché est toujours l'original.

Critère de clôture humaine : mêmes IDs, même ordre, originaux
intacts, pas de sous-chaîne improvisée.

## B — Sauvegarde / restauration

1. `python -m seamtech_search.sauvegarde sauver --base-url … --archive … --dossier … --sans-s3 --avec-references`
2. Si un fichier référencé manque : `complet=false`, exit 2 — **FAIL**
   si on ignorait le manque.
3. Déplacer ou rendre illisibles les originaux.
4. Restaurer dans une **base neuve** (`--base-cible` distinct) et les
   fichiers dans un **répertoire distinct** (`--fichiers-cible`).
5. Recalculer SHA-256 depuis le bundle uniquement.
6. Relancer une recherche A sur la base restaurée.
7. Si des objets S3 existent : `--restaurer-objets` avec l'identité
   **documents**, pas l'identité sauvegarde. MinIO réel obligatoire
   pour clôturer S3.

Ne pas déclarer PASS MinIO après un test s3rver.

## C — OCR

1. Constater `tesseract -l fra` et `ocrmypdf` présents. Sinon **STOP**,
   statut BLOQUÉ — ne pas mocker.
2. Importer un PDF scanné (fixture `tests/fixtures/ocr/ocr_propre.pdf`
   ou scan atelier).
3. Vérifier extraction `extracted` avec les mots de
   `reference_propre.txt`.
4. Index → recherche d'un mot OCR → téléchargement du **fichier
   original** (empreinte inchangée).
5. PDF texte natif (fiche 7792-SO) : OCR non déclenché.
6. Scan illisible : statut `unavailable`/`error` explicite, pas un
   faux succès.

## D — Déploiement

Suivre [CIBLE_REFERENCE.md](CIBLE_REFERENCE.md) une fois approuvée.
Contrôles OS / Docker / Windows / MinIO : seulement sur la cible
réelle, jamais inférés depuis ce bac à sable.

## Signature

| Rôle | Nom | Date | Résultat |
|---|---|---|---|
| Opérateur | _à remplir_ | | |
| Responsable métier | _à remplir_ | | |

(Laisser vide. Ne pas signer à la place.)

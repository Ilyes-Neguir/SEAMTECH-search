# SUITE DES TESTS — état exact (mis à jour à chaque passe)

## Passe du 29/10/2026 (nuit) — aucun dossier réel refusé

| Commande | Résultat |
|---|---|
| `.venv/bin/ruff check seamtech_search/ tests/` | `All checks passed!` |
| `pytest tests -m "not postgres"` | **1302 passés, 139 sautés**, 437 désélectionnés (113 s) |
| `pytest tests -m postgres` (PG 18.6, port 5433) | **416 passés, 21 sautés** (181 s) |

Dépôt réel rejoué sur base neuve (PostgreSQL 16.2 + stockage objet) :
**58 dossiers déposés, 58 traités, 0 refusé** ; 58/58 fiches avec titre ET code,
38 avec cotes.

Ce qui a été corrigé : voir `CHANGELOG.md`, entrée « suite 5 » (fiche de reprise,
crash `min()` des cotes FFAB, grille SAILONET, couple « 60x260 », plafond poids).

Reste hors de portée du bac à sable : **OCR** (binaires `tesseract`/`pdftoppm`
absents) — les 8 PDF image entrent comme « Document scanné » à saisir à la main,
et la lecture des racines Dropbox `ACCESSOIRES 2024/2025/2026`, le reste de
`VOILES 2025`/`VOILES 2026` et `ADONNANTE` (~300 dossiers), que le connecteur ne
descend qu'un fichier par appel.

---

## Passe du 29/10/2026 (soir) — dépôt en LOT des dossiers réels

| Commande | Résultat |
|---|---|
| `.venv/bin/ruff check seamtech_search/ tests/` | `All checks passed!` |
| `pytest tests -m "not postgres"` | **1296 passés, 139 sautés**, 437 désélectionnés (100 s) |
| `pytest tests -m postgres` (PG 18.6, port 5433) | **416 passés, 21 sautés** (159 s) |

Ce qui a été corrigé (détail et preuves : `CHANGELOG.md`, entrée « suite 4 ») :

1. le mot « pour » d'un formulaire ne peut plus devenir un code de fiche
   (`_code_plausible` : un code contient toujours un chiffre) ; la fiche
   « pour » créée à tort a été rejetée dans l'application ;
2. un formulaire de prise de cotes n'est plus jamais candidat au rôle de fiche ;
3. titre lu dans l'en-tête imprimé quand le gabarit n'en porte pas (SAILONET) ;
4. nouvelle commande `deposer-arborescence` (+ `seamtech_search/fiches/arborescence.py`)
   pour déposer TOUT un Dropbox et produire un rapport CSV.

Mesures réelles : `VOILES 2024` 24 dossiers → 3 traités / 21 refusés (formulaires
de cotes, planches SailPack, PDF image) ; `VOILES 2026` 12/12 traités ;
`VOILES 2025` 14/14 traités. Base de démonstration : **38 fiches**.

Reste à faire (état honnête) : les racines Dropbox `ACCESSOIRES 2024/2025/2026`,
le reste de `VOILES 2025`/`VOILES 2026` (~300 dossiers) et `ADONNANTE`
(modèles DXF/PLX, aucun PDF de fiche) ne sont PAS encore rapatriées : le
connecteur Dropbox ne descend qu'un fichier par appel.

---

Branche : `arena/6d05a833-seamtech-search` — **HEAD `cb2dee4`**
(`9009676` → `8b4b3fe` → `feb1d2a` → `b54f27c` → `cb2dee4`).

## État : campagne en cours — extension « accessoires + formulaires imprimés »

Mandat de cette passe : parcourir **tout** le Dropbox de l'atelier (chaque
dossier, chaque fichier, chaque format), marquer le gabarit de chaque document,
puis corriger le code pour les **voiles ET les accessoires**, et retester sur
**les documents réels**.

## Mesures de référence (bac à sable, base réelle PostgreSQL 16.2)

| Commande | Résultat |
|---|---|
| `.venv/bin/ruff check .` | `All checks passed!` |
| suite SQLite `pytest tests -m "not postgres"` | **1287 passés, 139 sautés**, 437 désélectionnés (101 s) |
| suite PostgreSQL `pytest tests -m postgres` | **416 passés, 21 sautés** (160 s) |
| CI GitHub sur `cb2dee4` | 4/4 workflows verts (CI 17 min, Qualification native, Qualification réserves, Perf comparaison) |

## Audit Dropbox (29/10/2026)

Inventaire complet : `/home/user/audit_dropbox/manifest.json` (racines avec
`complet: true|false`, formats rencontrés, familles de fiches mesurées,
documents ouverts). Formats non extractibles classés explicitement :
`.PLX .dxf .xin .3dm .3dmbak .mi2 .xls` et les plans PDF sans couche texte.

## Défauts corrigés et prouvés sur documents réels

1. Fiche **Lazy Bag** détectée comme `FICHE_GV_FULLBATTEN_V1` (ancre
   `coulisseaux`) ⇒ titre/code/client/dimensions vides.
2. Fiche **fourreau de gse** inconnue de tout gabarit.
3. Fiche **Sac de Pont** lue à moitié (code seul).
4. **Largeur inventée** à 12 mm sur « Longueur du gousset de latte 3.22m - Latte
   jonc 12mm … ».
5. **Longueur du lazy** ignorée : l'astérisque de « Longueur du lazy* 3.32 m »
   empêchait l'ancre de trouver la ligne ⇒ 3,22 m (gousset) annoncés.
6. **Titre contredit** : en-tête de formulaire « Grand-voile » sur une fiche
   Artimon ⇒ `titre = Grand-voile` / `type = Artimon`.
7. **Tissu principal = libellé** `(Pas de carbon):` au lieu de
   `Newport 9.45 AP Blanc - White` (deux-points soudés au mot `carbon):`).

## E2E sur les 11 documents réels (`deposer_dossier`, base neuve)

8 fiches déposées (`traite`), 3 refus explicites :

| dossier réel | fiche | catégorie | client | gabarit | longueur / score |
|---|---|---|---|---|---|
| LB 2 parties | ST64 | accessoire | SEAMTECH PRO | FICHE_ACCESSOIRE_IMPRIME_V1 | 3,32 m / 0,99 |
| acc_gse_st101_fini | ST101 | accessoire | SEAMTECH PRO | FICHE_ACCESSOIRE_IMPRIME_V1 | 16,40 m / 0,99 |
| acc_gse_st96_fichefinie | ST96 | accessoire | SEAMTECH PRO | FICHE_ACCESSOIRE_IMPRIME_V1 | 10,50 m / 0,99 |
| acc_lb2p_st105_fini | ST105 | accessoire | SEAMTECH PRO | FICHE_ACCESSOIRE_IMPRIME_V1 | 3,88 m / 0,99 |
| acc_sac_st265b_ffab | ST 256B | accessoire | VBO Sails | FICHE_ACCESSOIRE_IMPRIME_V1 | 5,10 m / 0,99 |
| acc_sac_st74b_ffab | ST 074B | accessoire | Voilerie VBO | FICHE_ACCESSOIRE_IMPRIME_V1 | 3,19 m / 0,99 |
| voile_st508_ffab | ST 508 | voile | AZ Voile | FICHE_GV_FFAB_V1 | 26,5 m² / 0,965 |
| voile_st660b_ffab | ST 660B | voile | Albatre Plaisance | FICHE_GV_FFAB_V1 (Artimon) | 7,2 m² / 0,977 |
| acc_lb1p_st298 | — | refus | — | — | PDF sans texte (plan) |
| acc_taud_st131 | — | refus | — | — | aucune fiche : plan |
| acc_taud_st40_fini | — | refus | — | — | aucune fiche : plan |

## Dossiers complets retestés (29/10, 2e passe)

| dossier réel | fichiers | PDF | résultat |
|---|---|---|---|
| `/YACHET CLASSIC ST508` | 8 + sous-dossier TISSU | 6 (2 fiches, 2 planches SailPack, cahier des charges, détail œillets raster) | fiche `ST 508` retenue, `ST 508V2_ffab.pdf` signalée en `autres_fiches`, 7 pièces |
| `/WESTERLY PENTLAND ST660B` | 2 | 2 (planche + fiche) | fiche `ST 660B` (Artimon) retenue, le plan devient pièce |
| `/ACCESSOIRES 2026/CAGNARD ST756` | 3 | 2 (raster) | refus « PDF illisible », rien d'inventé |
| `/VOILES 2026/ATHENA 44 ST761` | 1 (+104 DXF dans l'archive) | 1 (SailPack GENNAKER) | refus « aucune fiche » — c'est une planche |
| `/VOILES 2026/AURA 51 ST795` | 1 (+180 DXF dans l'archive) | 1 (SailPack CODE 0) | refus « aucune fiche » — c'est une planche |
| dossier reconstitué 159 fichiers | 159 | 6 | `traite`, 157 pièces, ~2 s |

## Refus qui nomment le contenu réel (29/10, 3e passe)

| dossier réel | contenu | refus écrit |
|---|---|---|
| `/VOILES 2024/CAVAILLE ST76` | `CavailleGV.pdf` = formulaire de prise de cotes | « formulaire de prise de cotes : CavailleGV.pdf » |
| `/VOILES 2024/FIRST 210 ST106` | `GSE First210.pdf` = formulaire | idem |
| `/VOILES 2024/FIRST 325 ST74A` | `LF DeveloppementSolentFirst325.pdf` = formulaire | idem |
| `/VOILES 2026/ATHENA 44 ST761` | `JW26050B.pdf` = planche SailPack | « planche de tracé SailPack » |
| `/VOILES 2026/AURA 51 ST795` | `20260410 Gennak Aura51.pdf` = planche SailPack | idem |
| `/ACCESSOIRES 2026/CAGNARD ST756` | 2 plans raster | « PDF sans couche texte … REVUE MANUELLE » |
| `/TOILE ST792` (+ sous-dossier `toile`) | `TOILE ST792 COUPE.pdf` raster | idem |

## Documents réels DANS l'application (29/10, 4e passe)

Pile complète lancée sans Docker : PostgreSQL (pgserver), stockage objet
compatible S3, API `uvicorn`, interface Next.js en développement.

| dossier réel | code lu | gabarit | type |
|---|---|---|---|
| `/YACHET CLASSIC ST508` | ST 508 | GV FFab | voile |
| `/WESTERLY PENTLAND ST660B` | ST 660B | GV FFab | voile |
| `/ACCESSOIRES 2024/LAZY BAG 2 PARTIES DUFOUR 385` | 170124CS (après correctif) | Accessoire imprimé | accessoire |
| `/ACCESSOIRES 2024/HOUSSE DE GSE ST96` | ST96 | Accessoire imprimé | accessoire |
| `/ACCESSOIRES 2024/HOUSSE DE GSE ST101` | ST101 | Accessoire imprimé | accessoire |
| `/ACCESSOIRES 2024/SAC DE PONT ST74B` | ST 074B | Accessoire imprimé | accessoire |
| `/ACCESSOIRES 2025/LAZY BAG 2P ST105` | ST105 | Accessoire imprimé | accessoire |
| `/ACCESSOIRES 2025/SAC DE PONT ST265B` | ST 256B | Accessoire imprimé | accessoire |

8 fiches `a_valider`, 19 documents indexés (recherche opérationnelle), 8 objets
promus et vérifiés (relecture SHA-256).

## Reste à faire

- Rejouer la suite PostgreSQL après la dernière passe de corrections.
- Terminer les listings Dropbox tronqués (`/VOILES 2025`, `/VOILES 2026`,
  `/ACCESSOIRES 2025`) ; le manifeste dit déjà lesquels sont `complet:false`.
- Points **non prouvables en bac à sable** (Windows/Docker Desktop, PostgreSQL
  de production, MinIO/R2, volumétrie, endurance) : à qualifier en conditions
  réelles.

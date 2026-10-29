# SUITE DES TESTS — état exact (mis à jour à chaque passe)

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
| suite SQLite `pytest tests -m "not postgres"` | **1278 passés, 139 sautés**, 437 désélectionnés (89 s) |
| suite PostgreSQL `pytest tests -m postgres` | **416 passés, 21 sautés** (157 s) |
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

## Reste à faire

- Rejouer la suite PostgreSQL après la dernière passe de corrections.
- Terminer les listings Dropbox tronqués (`/VOILES 2025`, `/VOILES 2026`,
  `/ACCESSOIRES 2025`) ; le manifeste dit déjà lesquels sont `complet:false`.
- Points **non prouvables en bac à sable** (Windows/Docker Desktop, PostgreSQL
  de production, MinIO/R2, volumétrie, endurance) : à qualifier en conditions
  réelles.

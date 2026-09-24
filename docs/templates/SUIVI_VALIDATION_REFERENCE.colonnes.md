# Colonnes du suivi de validation — `SUIVI_VALIDATION_REFERENCE.csv`

Encodage **utf-8-sig** (ouverture directe Excel), séparateur **`;`** (convention
CSV du dépôt — `scripts/inventaire_archive.py`). Une ligne = **un champ
contrôlé** sur une fiche. Compatible avec un futur import contrôlé (chaque
colonne ci-dessous est obligatoire ; valeur vide interdite hors
`valeur_extraite`/`valeur_attendue` quand le résultat est `NON_APPLICABLE`).

| # | Colonne | Contenu | Format / vocabulaire | Projection import |
|---|---|---|---|---|
| 1 | `code_fiche` | Code de la fiche technique | texte du document | `fiche.code` |
| 2 | `chemin_document` | Chemin du PDF, **relatif** à la racine d'archive (`<SOURCE>/…`) | aucun chemin absolu dans un fichier partageable | clé de traçabilité |
| 3 | `version_gabarit` | Gabarit appliqué par l'extraction | ex. `gabarit-v2` | registre de gabarits |
| 4 | `champ` | Clé de schéma du champ | ex. `fiche.code`, `cotes.finie.slu_m`, `materiau.epaisseur.3`, `galon.guindant.largeur_mm`, `jonction.laizes.1` | `fiche_champ_extrait.champ` |
| 5 | `valeur_extraite` | Valeur proposée par la machine, **telle quelle** (jamais réécrite) | texte | `valeur_brute` |
| 6 | `valeur_attendue` | Valeur réellement lue dans le document (accents, unités du document) | texte ; vide si `NON_APPLICABLE` | `valeur_normalisee` après conversion |
| 7 | `resultat` | Décision du validateur | `OK` \| `ABSENTE` \| `NON_APPLICABLE` \| `CORRIGEE` \| `ANOMALIE` | statut de contrôle |
| 8 | `commentaire` | Motif / correction / anomalie | `CORRECTION: <anc> → <nouv> — <cause>` ou `ANOMALIE: <code> — <desc>` (protocole §10) | `fiche_anomalie` si `ANOMALIE` |
| 9 | `page` | N° de page **à partir de 1** (affichage lecteur PDF) | entier ≥ 1 | `Zone.page` = page − 1 |
| 10 | `zone_pdf` | Rectangle d'origine, points PDF (repère pdfplumber) | `"x0,y0,x1,y1"` (guillemets à cause des virgules) | `Zone` |
| 11 | `operateur_correction` | Qui a appliqué les corrections d'extraction avant validation | `Prénom N. (identifiant)` ou `sans objet` | traçabilité |
| 12 | `validateur` | Qui coche le champ | `Prénom N. (identifiant_compte)` — compte nominatif | `session_ui` |
| 13 | `date` | Jour de la décision | ISO `AAAA-MM-JJ` | horodatage |
| 14 | `controle_secondaire` | Contre-vérification obligatoire si ≥ 1 `CORRIGEE`/`ANOMALIE` | `Prénom N. (identifiant) AAAA-MM-JJ` ou `sans objet` | garde quatre yeux (protocole §12) |
| 15 | `statut_final` | Statut de la fiche, répété sur ses lignes | `VALIDEE` \| `A_CORRIGER` \| `REJETEE` | `valide` \| `a_valider` \| `rejete` |

Règles de cohérence (contrôlées à l'import) :

- `resultat=CORRIGEE` ⇒ `valeur_attendue` non vide ET `commentaire` commence
  par `CORRECTION:` ;
- `resultat=ANOMALIE` ⇒ `commentaire` commence par `ANOMALIE:` ET
  `statut_final` ≠ `VALIDEE` ;
- `statut_final=VALIDEE` sur fiche avec `CORRIGEE`/`ANOMALIE` ⇒
  `controle_secondaire` ≠ `sans objet` ET `controle_secondaire` ≠
  `operateur_correction` ;
- `date` et `page` valides numériquement/ISO ;
- toutes les lignes d'une même `code_fiche` partagent `statut_final` et `date`.

# Arrivée de l'archive — procédure opérationnelle

**Objet** : traiter l'arrivée de l'**archive réelle** (PDF de production,
fonds ancien 50+ Go attendu) proprement et vite, dès sa réception. Cette
procédure est écrite AVANT l'arrivée : à ce jour **l'archive réelle n'est pas
au dépôt**, et aucun chiffre produit sur `sample_data` ou `tests/fixtures`
ne vaut pour elle (règle du projet : une mesure synthétique est toujours
étiquetée comme telle).

Deux outils accompagnent la procédure :

- `scripts/preflight_archive.py` — préflight **obligatoire avant tout
  traitement** (lecture seule, aucun réseau) ;
- `scripts/inventaire_archive.py` — inventaire chiffré approfondi (Phase 0),
  après préflight vert.

Tests de non-régression : `tests/test_preflight_archive.py` (refus, rapports,
échantillon, empreintes, RG13).

---

## 1. Prérequis

| Élément | Vérifié par le préflight | Remarque |
|---|---|---|
| Python 3.10+ | `python_present` | scripts locaux |
| Tesseract 5.x + langue **fra** | `tesseract_present`, `langue_francaise` | OCR étage 3 (fonds ancien) |
| pdftoppm (ou pdftocairo) | `pdftoppm_present` | rendu PDF → image |
| PostgreSQL | `postgres_present` **si** `--staging` | indexation staging |
| Espace disque | `espace_disque` | seuil `--min-libre-o` (1 Go strict minimum, **50 Go+ le jour J**) |
| Poste préparé | `docs/CHECKLIST_POSTE_WINDOWS.md` | diagnostic Windows la veille |
| Secret des rapports | `--masquer-chemins` | avant tout partage hors équipe |

## 2. Chemins autorisés

- **Source** (`--source`) : le chemin de l'archive **tel que livré**, monté en
  **lecture seule** si le système le permet (le préflight signale
  `source_non_ecrivable`). Obligatoire : aucun défaut, aucun repli — en
  particulier **jamais `sample_data`** comme substitut silencieux de
  l'archive (testé).
- **Travail** (`--travail`) : répertoire de traitement OCR, **hors archive**
  (`SEAMTECH_OCR_TRAVAIL_DIR`, défaut `data/ocr_travail`). Refusé s'il est
  dans la source (ou l'inverse), liens symboliques résolus.
- **Sortie** (`--sortie`) : répertoire des rapports, lui aussi **distinct de
  la source** dans les deux sens.
- Chemins relatifs OU absolus acceptés ; espaces et accents acceptés
  (« Pièces détachées Été 2024 ») ; un lien symbolique pointant dans la
  source est refusé comme inclusion.

## 3. Préparation du répertoire de travail

```powershell
# Windows (poste de production)
New-Item -ItemType Directory -Force -Path D:\SEAMTECH\travail_ocr | Out-Null   # HORS archive
New-Item -ItemType Directory -Force -Path D:\SEAMTECH\rapports | Out-Null
$env:SEAMTECH_OCR_TRAVAIL_DIR = "D:\SEAMTECH\travail_ocr"
```

```bash
# Linux
mkdir -p /var/seamtech/travail_ocr /var/seamtech/rapports   # HORS archive
export SEAMTECH_OCR_TRAVAIL_DIR=/var/seamtech/travail_ocr
```

Vérifier que le volume de sortie offre la place (50 Go+ conseillés) et que le
compte de service n'a **pas** de droit d'écriture sur la source.

## 4. Lancement du préflight

```bash
python3 scripts/preflight_archive.py preflight \
    --source /CHEMIN/ARCHIVE \
    --travail /CHEMIN/travail_ocr \
    --sortie /CHEMIN/rapports \
    --min-libre-o 53687091200 \
    --masquer-chemins        # si le rapport doit sortir du poste
```

Sorties : `preflight_rapport.json` + `preflight_rapport.txt` +
`echantillon.json` dans `--sortie`, plus un résumé sur stdout (`--json` pour
du JSON). Codes de sortie :

| Code | Signification | Action |
|---|---|---|
| 0 | vert (avertissements possibles) | continuer §5 |
| 2 | chemin source absent ou vide | re-fournir `--source` explicitement |
| 3 | source inexistante/illisible/sans fichiers | vérifier le montage du support |
| 4 | inclusion de chemins (travail/sortie dans la source) | déplacer travail/sortie HORS archive |
| 5 | configuration ambiguë | un seul mode d'échantillonnage ; source déclarée une fois |
| 6 | outils manquants (tesseract, fra, pdftoppm) | installer AVANT tout traitement (checklist §4) |
| 7 | espace disque insuffisant | libérer la place (checklist §3) |
| 8 | PostgreSQL absent malgré `--staging` | installer/configurer ou retirer `--staging` |
| 9 | empreintes non conformes (sous-commande `empreintes`) | **stop** : voir §6 |

Le préflight ne parcourt la source **que** si `--source` est explicite et
validé ; il ne consomme jamais `sample_data` en silence. Répétez-le à chaque
étape clé (support remonté, changement de poste) — c'est rapide et inerte.

## 5. Choix d'un échantillon

Aucun traitement massif par défaut : le préflight produit un **échantillon de
travail** (dans `echantillon.json`) :

- **20 à 30 PDF** (défaut 25, `--echantillon-pdfs N`) — répartis sur toute
  l'arborescence (sélection triée déterministe) ;
- **OU 5 à 10 dossiers représentatifs** (`--echantillon-pdfs 0
  --echantillon-dossiers N`, dossiers à PDF d'abord) ;
- **OU une limite configurable** (`--echantillon-pdfs N` libre, plafonné par
  `--limite-empreintes`, défaut 100 fichiers empreintés) ;
- `--sans-empreintes` pour un inventaire pur (aucun hachage).

Pour la campagne de validation humaine, compléter ce tirage selon
`docs/PROTOCOLE_VALIDATION_HUMAINE.md` §1 (20-30 **fiches** stratifiées).

## 6. Contrôle des empreintes

Chaque élément d'échantillon est empreinté **SHA-256** (+ taille, + mtime) à
la sélection. Avant tout traitement — et avant toute validation — rejouer :

```bash
python3 scripts/preflight_archive.py empreintes \
    --manifeste /CHEMIN/rapports/echantillon.json \
    --source /CHEMIN/ARCHIVE \
    --sortie /CHEMIN/rapports
```

| Détection | Statut | Code |
|---|---|---|
| tout identique | `ok` | 0 |
| mtime seul modifié (touch) | `avertissement` | 0 |
| SHA-256 ou taille modifié(e) | `echec` | 9 |
| fichier manquant | `echec` | 9 |

Tout code 9 avant traitement = **stop** : le support a bougé ou a été modifié
(copie interrompue, support défectueux, écriture parasite) — re-monter,
re-copier depuis la source d'autorité, re-préflighter. Le traitement OCR
conserve lui-même l'idempotence par empreinte (`ocr_nuit`, jamais par mtime
seul).

## 7. Validation avant traitement complet

1. Préflight vert (code 0) — rapports relus (versions dépôt/schéma, config
   OCR fra/300 dpi/seuil 20).
2. Échantillon choisi (§5) — empreintes contrôlées (§6, code 0).
3. Run OCR **sur l'échantillon seulement** :
   `ocr-nuit nuit --dossier <échantillon> --limite 30 --budget-minutes 30` —
   vérifier le compte rendu (débit pages/min, échecs, texte produit).
4. Validation humaine de 20-30 fiches (`docs/PROTOCOLE_VALIDATION_HUMAINE.md`)
   — corrigée le cas échéant, avec contrôle à quatre yeux.
5. Décision explicite écrite (mail/compte rendu) : « échantillon validé,
   traitement complet autorisé par <nom>, le <date> ».
6. Seulement alors : traitement complet, par tranches (`--limite` +
   `--budget-minutes`), inventaire complet (`inventaire_archive.py`) en
   parallèle, reprise par empreintes entre les tranches.

## 8. Conditions d'arrêt

**Stop immédiat** (ne pas continuer « pour voir ») si :

- un code de refus du préflight (2 à 8) ne peut être levé ;
- le contrôle des empreintes sort en code 9 ;
- la source devient modifiable en cours de campagne (`source_non_ecrivable`
  en `avertissement` à un re-preflight qui ne l'était pas avant) ;
- un fichier de l'archive change de SHA-256 entre deux runs (rapport OCR) ;
- l'espace disque descend sous le seuil (`--min-libre-o`) ;
- le compte rendu OCR affiche une série d'échecs inexpliqués ;
- une fiche de validation tombe sur une anomalie `forte` non résolue ;
- le contrôle à quatre yeux est impossible (personne) alors que des
  corrections existent → statut `A_CORRIGER`, pas de faux « validé ».

Reprise : après levée de la cause, reprendre au §4 (préflight), §6
(empreintes), puis reprendre le traitement là où l'état OCR l'a laissé
(verrou libéré proprement, état par empreintes — Ctrl-C/SIGTERM/budget
couverts par `tests/test_ocr_comportement.py`).

## 9. Ce que cette procédure ne prétend pas

- Aucune donnée réelle n'a été vue : volumes, débits et durées ci-dessous ne
  seront mesurés qu'après arrivée. Les valeurs affichées sur fixtures
  (`sample_data`, `tests/fixtures`) sont **synthétiques** et documentées
  comme telles dans `docs/verite_terrain/RAPPORT_PREPARATION_SANS_ARCHIVE_20260924.md`.
- `sample_data` reste utilisable **explicitement** pour les essais de
  procédure (tests synthétiques uniquement) — jamais comme archive de
  production.

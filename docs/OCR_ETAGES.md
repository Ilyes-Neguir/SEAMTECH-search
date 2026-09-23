# OCR par étages — Lot M (préparation Lot G)

> **Règle d'or rappelée par le plan v3.0 §4 bis :**
> « OCR seulement là où c'est utile — le texte des fiches est déjà dans le PDF »

## Les trois étages

### Étage 1 : inventaire — nom, type, date, rattachement
Rapide, sur tout. Parcourt l'archive en lecture seule (RG13) et produit :
- nombre de fichiers, extensions, tailles totales
- répartition par année (mtime) et par dossier top-level (rattachement)
- liste détaillée (chemin, taille, extension, année)

Commande :
```bash
python3 -m seamtech_search.ocr.cli inventaire --dossier /chemin/archive --json
# ou via entry point
ocr-nuit inventaire --dossier /chemin/archive
```

Sortie : JSON avec `fichiers`, `extensions`, `tailles_octets`, `par_annee_ou_par_dossier`.

### Étage 2 : texte natif — le texte des fiches récentes est DÉJÀ dans le PDF
Le texte des fiches récentes est extrait tel quel via `pdfplumber` puis `pypdf` en repli.
Aucun OCR n'est appliqué si la page contient >= seuil caractères (défaut 20).

Justification du seuil 20 :
- Vraie fiche 7792-SO : chaque page > 200 caractères natifs
- Scans d'archive (échantillons tests/fixtures/ocr/) : 0 caractère natif
- Bruit (numéro de page isolé) : < 10 caractères
Donc 20 est une marge sûre entre bruit et contenu réel.

### Étage 3 : OCR — uniquement sur les documents scannés de l'archive ancienne
Déclencheur : une page dont le texte natif est < seuil (paramétrable via `--seuil`).

Traitement :
- PDF scanné : extraction des images de la page via `pypdf`, OCR de chaque image via `tesseract -l fra`
- Si pas d'images : rendu de la page en PNG via `pdftoppm -r 300` (poppler), puis OCR
- Fallback : tesseract direct sur PDF mono-page (certains builds)
- Image isolée (png/jpg/tif/bmp/webp) : traitée comme un scan d'une page

Résultat par page :
- `texte_ocr` : texte produit
- `confiance` : confiance moyenne tesseract (0-100) via sortie TSV, ou NULL
- `moteur` + `version_moteur` : ex "tesseract 5.3.0"
- `duree_s` : durée OCR
- `page_ocerisee` : bool
- `motif` : raison de non-OCR (texte natif présent, tesseract absent, etc.)

L'OCR reste OPTIONNEL au runtime : si `tesseract` est absent, le statut est
`unavailable` avec motif lisible et le lot ne plante pas (même comportement que
`extractors.py`).

## Réglages

| Paramètre | Défaut | Justification |
|-----------|--------|---------------|
| `--seuil` | 20 | Voir justification ci-dessus |
| `--langue` | fra | Archive française |
| `--resolution` | 300 dpi | Compromis qualité/temps, utilisé pour pdftoppm |
| `--pages` | toutes | Pour tests : ex `0,1,2` ou `0-5` |
| `--budget-minutes` | None | Budget temps, arrêt propre à la minute |
| `--limite` | None | Nombre max fichiers (pour tests) |
| `--tesseract-command` | tesseract | Binaire tesseract |
| `SEAMTECH_OCR_TRAVAIL_DIR` | data/ocr_travail | Répertoire de travail hors archive |

Tous les réglages sont documentés et paramétrables pour ne JAMAIS saturer
la machine cible (PC bureau 8 Go RAM, CPU seul, sans GPU).

Débit mesuré de référence : 30 pages/minute (2 s/page) sur PC de référence
8 Go RAM, CPU seul, tesseract 5.x -l fra, 300 dpi.
Mesure obtenue sur échantillons commités :
- propre 1 page : ~1,8 s
- dégradé 1 page : ~2,2 s
Moyenne 2 s/page = 30 p/min, utilisée pour estimation durée :
`estimation_duree_s = pages_a_oceriser * 60 / debit_mesure`.

Le débit réel est mesuré à chaque run et publié dans le compte rendu.

## Sortie produite

- Répertoire de travail : `$SEAMTECH_OCR_TRAVAIL_DIR` (défaut `data/ocr_travail`)
  - `ocr_nuit.lock` : verrou d'exécution (un seul run à la fois)
  - `ocr_etat.json` : état reprenable par empreinte SHA-256 (jamais mtime seul)
  - `rapports/ocr_rapport_<timestamp>.json` : compte rendu machine
  - `rapports/ocr_rapport_<timestamp>.txt` : rapport texte lisible

Compte rendu contient :
- fichiers examinés, traités, déjà traités (idempotence)
- pages océrisées, pages ignorées (texte natif présent), échecs avec motif
- durée totale, débit mesuré (pages/minute), taille texte produit
- moteur et version

- Table de staging (PostgreSQL) : `ocr_etage3`
  - `fichier_source`, `empreinte_sha256`, `page`, `texte_ocr`, `confiance`,
    `moteur`, `version_moteur`, `duree_s`, `page_ocerisee`, `motif`, `horodatage`
  - AUCUNE écriture dans `chunk` ni `document` : promotion vers index = Lot G

## Planification DOCUMENTÉE mais NON EXÉCUTÉE

Ce lot NE crée AUCUNE tâche planifiée sur une machine. Il documente les lignes
exactes.

### Linux (cron)

```bash
# Éditer crontab : crontab -e
# Exécution quotidienne à 01:00, budget 360 min (6h), limite 5000 fichiers
# Journal dans travail_dir/rapports/cron.log

0 1 * * * SEAMTECH_OCR_TRAVAIL_DIR=/data/ocr_travail /usr/bin/python3 -m seamtech_search.ocr.cli nuit --dossier /archive/SEAMTECH --budget-minutes 360 --limite 5000 --langue fra >> /data/ocr_travail/rapports/cron.log 2>&1

# Précautions :
# - budget : évite de saturer la machine le matin (arrêt propre à l'heure dite)
# - verrou : un second lancement sort avec code 2 et message clair, pas de double travail
# - répertoire de travail : hors archive, déclaré via SEAMTECH_OCR_TRAVAIL_DIR
# - journal : cron.log dans travail_dir, + rapports JSON/TXT horodatés
```

### Windows (schtasks)

```bat
:: Création tâche quotidienne 01:00, avec budget et verrou
schtasks /create /sc daily /st 01:00 /tn "SEAMTECH OCR Nuit" /tr "cmd /c set SEAMTECH_OCR_TRAVAIL_DIR=C:\SEAMTECH\ocr_travail && python -m seamtech_search.ocr.cli nuit --dossier D:\SEAMTECH\DesignFiles --budget-minutes 360 --limite 5000 --langue fra >> C:\SEAMTECH\ocr_travail\rapports\cron.log 2>&1" /f

:: Vérifier
schtasks /query /tn "SEAMTECH OCR Nuit" /v

:: Supprimer (si besoin)
schtasks /delete /tn "SEAMTECH OCR Nuit" /f

:: Précautions identiques Linux : budget, verrou, répertoire travail, journal
```

### Recommandations

- Toujours utiliser `--budget-minutes` (ex 360 = 6h) pour que l'exécution nocturne
  s'arrête proprement avant l'arrivée des utilisateurs (2-5 utilisateurs, PC bureau 8 Go).
- Le verrou `ocr_nuit.lock` garantit qu'un second lancement (manuel ou cron qui chevauche)
  sort proprement avec code 2 et message clair, sans corrompre l'état ni doubler le travail.
- La reprise est automatique : après Ctrl-C, SIGTERM ou budget épuisé, le run suivant
  reprend là où il s'est arrêté (idempotence par empreinte SHA-256).
- Le répertoire de travail DOIT être hors archive (RG13) — variable d'environnement documentée.
- Aucun appel réseau (RG14) : tout est local, tesseract en binaire.

## Dépannage

| Symptôme | Cause | Solution |
|----------|-------|----------|
| `tesseract absent` | binaire non installé | `sudo apt-get install -y tesseract-ocr tesseract-ocr-fra` (job CI dédié uniquement) |
| `aucune image extraite et pdftoppm absent` | PDF scanné sans images extractibles et pdftoppm absent | Installer poppler-utils (`pdftoppm`) ou utiliser ocrmypdf |
| Second run refuse avec code 2 | Verrou actif | Attendre fin du run ou vérifier si processus mort (stale lock supprimé après 10 min) |
| Budget dépassé | Temps écoulé >= budget | Augmenter --budget-minutes ou réduire --limite |
| Pages ignorées = toutes | Seuil trop bas ou PDF à texte natif | Normal : étage 2, on garde texte natif (règle inviolable) |
| Qualité <0,90 sur propre | Image trop petite ou bruit | Régénérer échantillons via `python3 tests/fixtures/ocr/generate_fixtures.py` |

## Limites

- Qualité mesurée sur échantillons seulement (2 PDF <150 Ko), aucune mesure sur 50+ Go
  (extrapolation seulement, formule : `duree_50Go = nb_pages_estime * 60 / debit_mesure`)
- Planification non exécutée (documentée seulement)
- OCR via binaires uniquement, pas de modèle Python (décision architecture : postes 8 Go CPU seul)
- Pas de GPU, pas de modèle téléchargé (RG14)

## Références

- Plan v3.0 §4 bis et §5 : règle des étages
- `seamtech_search/extractors.py` : extraction existante (OCR désactivé par défaut)
- `seamtech_search/ocr/` : implémentation Lot M
- `tests/fixtures/ocr/` : échantillons scannés + script régénération

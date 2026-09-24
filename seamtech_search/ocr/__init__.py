"""OCR par étages — Lot M (préparation Lot G).

Règle d'or rappelée par le plan v3.0 §4 bis :
« OCR seulement là où c'est utile — le texte des fiches est déjà dans le PDF ».

Trois étages :
- Étage 1 : inventaire rapide (nom, type, date, rattachement)
- Étage 2 : texte natif déjà dans le PDF (extraction telle quelle)
- Étage 3 : OCR uniquement sur les documents scannés de l'archive ancienne,
  là où le texte natif est absent (< seuil paramétrable).

Ce paquet ne touche JAMAIS l'archive source (RG13) et n'effectue AUCUN
appel réseau (RG14). Tout ce qui est produit va dans le répertoire de
travail déclaré par la variable d'environnement SEAMTECH_OCR_TRAVAIL_DIR
(par défaut data/ocr_travail, hors archive).

Style homogène avec qualite/, dedup/, comptes/ : fonctions pures testables,
CLI dédiée, pas de dépendance Python ajoutée (OCR via binaires tesseract
et éventuellement ocrmypdf/pdftoppm appelés en ligne de commande).
"""

from .etat import EtatOCR, VerrouOCR, empreinte_sha256, get_travail_dir
from .inventaire import inventaire_etage1, inventaire_etage3
from .pipeline import (
    SEUIL_DEFAUT,
    doit_oceriser_page,
    ocriser_fichier,
    ocriser_pages,
)

__all__ = [
    "SEUIL_DEFAUT",
    "EtatOCR",
    "VerrouOCR",
    "doit_oceriser_page",
    "empreinte_sha256",
    "get_travail_dir",
    "inventaire_etage1",
    "inventaire_etage3",
    "ocriser_fichier",
    "ocriser_pages",
]

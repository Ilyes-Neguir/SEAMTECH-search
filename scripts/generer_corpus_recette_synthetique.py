"""Genere un corpus de recette SYNTHETIQUE multi-affaires.

Pourquoi ce generateur : la recette locale (``scripts/recette_locale.sh``) se
nourrissait des 7 archives ZIP du corpus REEL deposees a la racine du depot.
Ces archives provenaient du commanditaire et ont ete purgees : la zone de
travail restait vide et la recette s'arretant sur « aucune affaire
exploitable ».

Il lui fallait un repli. Les jeux de ``sample_data/`` existants ne
conviennent pas : ils sont prevus pour d'autres tests, un seul d'entre eux
contient une veritable fiche de fabrication, et la recette a besoin de
PLUSIEURS affaires (cohorte >= 3) pour que facettes, filtres et rejeu
idempotent veuillent dire quelque chose.

Ce generateur produit donc N affaires distinctes, en reutilisant EXACTEMENT
la mise en page de la fiche de reference synthetique (memes positions, memes
corps, memes filets de tableau) : seule l'identite de l'affaire change — code
de commande, type de voile, bateau. Les 67 valeurs techniques (cotes,
matieres, accastillage) sont CONSERVEES a l'identique : elles n'identifient
aucun client et, surtout, elles gardent les regles du gabarit
FICHE_PORTANT_V1 applicables telles quelles.

Le corpus est ecrit dans le depot (il est synthetique, il ne porte aucune
donnee client) pour que la recette verifie des PDF STABLES, comparables par
SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from reportlab.pdfgen import canvas

# Le générateur de la fiche de référence vit à côté de celui-ci ; on le charge
# depuis son propre dossier pour que `python scripts/generer_corpus_recette_synthetique.py`
# marche sans PYTHONPATH. On ne DUPLIQUE pas sa mise en page : un corpus qui
# recopierait SPANS finirait par en dériver.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generer_fiche_reference_synthetique import (  # noqa: E402
    HAUTEUR,
    LARGEUR,
    POLICE,
    SPANS,
    TRAITS_H,
    TRAITS_V,
)

RACINE = Path(__file__).resolve().parent.parent
CORPUS = RACINE / "sample_data" / "recette"

VOILE_REFERENCE = "Spi Asymétrique Medium Régate"
BATEAU_REFERENCE = "29er (15')"

# Codes de commande FICTIFS. Trois affaires suffisent pour la cohorte attendue
# par la recette (voir _fiches_a_valider).
#
# Seul le code varie, volontairement : la ligne de titre de la fiche (« Spi
# Asymétrique Medium Régate pour 29er (15') de Exemplia (Demo) ») est lue par
# une regle adosse au VOCABULAIRE des types de voile. Premiere version de ce
# generateur, j'avais fait varier le type de voile (Génois, Trinquette) pour
# enrichir les facettes : la regle ne reconnait pas ces libelles, et
# l'extraction perdait du meme coup le code, le type de voile, le client et le
# bateau — trois fiches « champ_obligatoire_manquant » au lieu de trois
# affaires exploitables. Morale : ce corpus doit rester dans le vocabulaire
# que le gabarit sait lire, sinon il ne prouve plus rien du tout.
# Une affaire sur trois porte une INCOHERENCE volontaire : ses mesures finies
# ne concordent pas avec la surface declaree (6,60 x 5,50 -> ~15,71 m2
# attendus, mais un guindant fini a 9,90 m). C'est le cas d'atelier le plus
# banal qui soit - une mesure reprise de travers - et surtout c'est ce qui
# fait arriver la fiche EN REVUE HUMAINE plutot qu'auto-validee : l'anomalie
# bloquante l'emporte sur le score, qui est la regle D4 elle-meme.
#
# Pourquoi en faut-il une : le controle « filtres-facettes » de la recette
# prouve qu'une fiche EN ATTENTE est exclue de la recherche quand
# inclure_a_valider=false. Avec trois fiches toutes impeccables, les trois
# partaient en auto-validation et il ne restait RIEN a exclure : le controle
# n'avait plus rien a demonstrer et tombait. Le corpus de repli doit donc
# reproduire le MELANGE d'arrivee du corpus reel - des fiches propres et des
# fiches a reprendre - et non un monde ou tout est parfait.
#
# Le seuil d'auto-validation n'est ni touche ni contourne : c'est lui qui
# classe cette fiche, en appliquant sa regle des anomalies bloquantes.
AFFAIRES: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("1042-EX", None),
    ("1043-EX", None),
    ("1044-EX", (" 9,90 m", " 8,80 m", " 3,08 m", " 3,14 m", " 15,71 m²")),
)

# Les textes des mesures finies de la fiche de reference, dans l'ordre des
# colonnes. Servent d'ancre pour remplacer ceux d'une affaire incoherente.
MESURES_FINIES_REFERENCE: tuple[str, ...] = (
    " 6,60 m",
    " 5,50 m",
    " 3,08 m",
    " 3,14 m",
    " 15,71 m²",
)
ORDONNEE_MESURES_FINIES = 439.1

# Les identifiants de la fiche de reference, tels qu'ils apparaissent dans
# SPANS. Le generateur les remplace par ceux de chaque affaire.
CODE_REFERENCE = "1042-EX"
CLIENT = "Exemplia"


def _substituer(texte: str, code: str) -> str:
    """Remplace les identifiants de la fiche de reference par ceux de l'affaire."""
    return texte.replace(CODE_REFERENCE, code)


def generer_affaire(repertoire: Path, code: str, mesures_finies: tuple[str, ...] | None) -> Path:
    """Ecrit une affaire : un dossier, une fiche de fabrication dedans.

    Le nom du PDF porte le code pour que la recette retrouve ses petits, comme
    le faisaient les archives du corpus reel.
    """
    repertoire.mkdir(parents=True, exist_ok=True)
    sortie = repertoire / f"fiche-{code}_ffab.pdf"
    c = canvas.Canvas(str(sortie), pagesize=(LARGEUR, HAUTEUR))
    c.setStrokeColorRGB(0.2, 0.2, 0.2)
    c.setLineWidth(0.5)
    for x0, x1, y in TRAITS_H:
        c.line(x0, y, x1, y)
    for y0, y1, x in TRAITS_V:
        c.line(x, y0, x, y1)
    if mesures_finies is not None:
        # Remplacement COLONNE PAR COLONNE : les mesures finies de la fiche de
        # reference, dans l'ordre, par celles de l'affaire incoherente.
        compteur = 0
    for x, y, corps, texte in SPANS:
        rendu = texte
        if mesures_finies is not None and abs(y - ORDONNEE_MESURES_FINIES) < 0.6:
            if texte in MESURES_FINIES_REFERENCE:
                rendu = mesures_finies[MESURES_FINIES_REFERENCE.index(texte)]
                compteur += 1
        c.setFont(POLICE, corps)
        c.drawString(x, y, _substituer(rendu, code))
    if mesures_finies is not None and compteur != len(MESURES_FINIES_REFERENCE):
        raise SystemExit(
            f"{code} : {compteur} mesure(s) finie(s) remplacee(s) sur "
            f"{len(MESURES_FINIES_REFERENCE)} — la mise en page de la fiche de "
            f"reference a change, le corpus incoherent ne veut plus rien dire."
        )
    c.showPage()
    c.save()
    return sortie


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sortie", default=str(CORPUS))
    a = p.parse_args()

    racine = Path(a.sortie)
    empreintes = {code: "" for code, _ in AFFAIRES}
    for code, mesures_finies in AFFAIRES:
        chemin = generer_affaire(racine / f"CLIENT-{code}", code, mesures_finies)
        print(f"ecrit {chemin.relative_to(RACINE)} ({chemin.stat().st_size} octets)")

    # Les affaires doivent etre DISTINCTES, sinon la deduplication par SHA-256
    # les fondrait en une seule fiche et la cohorte de la recette s'effondrerait.
    for code in empreintes:
        pdfs = list((racine / f"CLIENT-{code}").glob("*.pdf"))
        if len(pdfs) != 1:
            print(f"ERREUR : CLIENT-{code} contient {len(pdfs)} PDF, un seul attendu")
            return 2
        empreintes[code] = hashlib.sha256(pdfs[0].read_bytes()).hexdigest()
    if len(set(empreintes.values())) != len(AFFAIRES):
        print("ERREUR : deux affaires ont le meme PDF — la deduplication les fusionnerait")
        return 2

    print(f"{len(AFFAIRES)} affaires synthetiques distinctes dans {racine.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

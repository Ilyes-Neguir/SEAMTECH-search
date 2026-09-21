"""Détection structurelle des fiches techniques (PR 1 — angle mort de classement).

La classification par ancres (:func:`seamtech_search.anchors.classify_pdf_text`)
rate toute variante dont le vocabulaire n'est pas dans ``TECHNICAL_ANCHORS`` —
constaté en Phase 0 sur des fiches « génois » classées ``plan_pdf``. Cette
module ajoute un signal INDÉPENDANT : combinaison d'un lexique configurable
(:mod:`seamtech_search.lexique`) et d'une structure de tableau détectable dans
les positions de mots (pdfplumber).

Un PDF est « candidat fiche » si :
- le texte de la page contient au moins ``seuils.vocabulaire_min`` termes du
  lexique (comparaison par mots entiers, sans accents, insensible à la casse) ;
- ET une structure de tableau est détectée : grille tracée (lignes/rects vus
  par pdfplumber) OU grille inférée des positions (assez de colonnes et de
  lignes de mots alignés).

Le résultat est expliqué : termes trouvés, comptages de colonnes/lignes,
présence de grille, score pondéré et motif d'exclusion éventuel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .lexique import LexiqueFiches

# Bucket de repli pour l'inférence de grille quand les dimensions de page ne
# sont pas exploitables (2 % de la largeur dans le cas normal, 6 pt sinon).
BUCKET_REPLI_PT = 6.0
PROPORTION_BUCKET = 0.02
# Un terme couvre « d'autant mieux » qu'il est rare ; au-delà de ce nombre de
# termes trouvés, la composante vocabulaire du score sature.
SATURATION_VOCABULAIRE = 5.0


@dataclass(frozen=True)
class ResultatDetection:
    """Verdict structurel pour une page, avec ses composantes (expliquable)."""

    est_candidat: bool
    score: float
    vocabulaire_trouve: tuple[str, ...] = field(default=())
    nb_colonnes: int = 0
    nb_lignes: int = 0
    grille_tracee: bool = False
    motif_exclusion: str = ""

    def composantes(self) -> dict[str, object]:
        """Composantes du verdict, telles qu'exportées dans les rapports."""
        return {
            "est_candidat": self.est_candidat,
            "score": round(self.score, 3),
            "nb_termes_vocabulaire": len(self.vocabulaire_trouve),
            "vocabulaire_trouve": list(self.vocabulaire_trouve),
            "nb_colonnes": self.nb_colonnes,
            "nb_lignes": self.nb_lignes,
            "grille_tracee": self.grille_tracee,
            "motif_exclusion": self.motif_exclusion,
        }


def _compter_alignements(mots: list[dict[str, object]], cle: str, bucket: float) -> int:
    """Nombre de positions distinctes (*cle* = x0 ou top) portant ≥ 2 mots.

    C'est la signature d'un tableau : des colonnes (plusieurs mots partagent
    la même origine x) et des lignes (plusieurs mots partagent le même y).
    """
    compteur: dict[int, int] = {}
    for mot in mots:
        try:
            position = float(mot.get(cle, 0.0))  # type: ignore[union-attr]
        except (TypeError, ValueError):
            continue
        compteur[int(position / bucket)] = compteur.get(int(position / bucket), 0) + 1
    return sum(1 for nombre in compteur.values() if nombre >= 2)


def detecter_fiche(
    mots: list[dict[str, object]],
    largeur_page: float,
    hauteur_page: float,
    grille_tracee: bool,
    lexique: LexiqueFiches,
) -> ResultatDetection:
    """Décide si une page est une fiche candidate, avec justification.

    *mots* est la sortie de ``pdfplumber`` (``extract_words``) : liste de
    dictionnaires portant au minimum ``text``, ``x0`` et ``top``.
    ``grille_tracee`` signale une grille réelle (lignes/rects du PDF,
    ``extract_tables`` non vide) — elle vaut structure à elle seule.
    """
    if not mots:
        return ResultatDetection(
            est_candidat=False,
            score=0.0,
            grille_tracee=bool(grille_tracee),
            motif_exclusion="sans couche texte (scan probable)",
        )

    from .lexique import normaliser_terme

    texte_page = normaliser_terme(" ".join(str(mot.get("text", "")) for mot in mots))
    trouves: list[str] = []
    for terme in lexique.vocabulaire_normalise:
        # Constat 3 de revue : tolérance au pluriel pour les termes mono-mot,
        # dans les DEUX sens — « jonction » (lexique) trouve « Jonctions »
        # (fiche) et « epaisseurs » (lexique) trouve « Epaisseur 01 » (fiche).
        # Les termes multi-mots restent appariés exactement (aucune tolérance
        # silencieuse sur les expressions).
        if " " in terme:
            motif = r"(?<![a-z0-9])" + re.escape(terme) + r"(?![a-z0-9])"
        else:
            base = terme[:-1] if terme.endswith("s") and len(terme) >= 4 else terme
            motif = r"(?<![a-z0-9])" + re.escape(base) + r"s?(?![a-z0-9])"
        if re.search(motif, texte_page):
            trouves.append(terme)

    bucket_x = max(largeur_page * PROPORTION_BUCKET, BUCKET_REPLI_PT) if largeur_page > 0 else BUCKET_REPLI_PT
    bucket_y = max(hauteur_page * PROPORTION_BUCKET, BUCKET_REPLI_PT) if hauteur_page > 0 else BUCKET_REPLI_PT
    nb_colonnes = _compter_alignements(mots, "x0", bucket_x)
    nb_lignes = _compter_alignements(mots, "top", bucket_y)

    structure_ok = bool(grille_tracee) or (
        nb_colonnes >= lexique.seuils.nb_colonnes_min and nb_lignes >= lexique.seuils.nb_lignes_min
    )
    nb_termes = len(trouves)
    # Constat 2 de revue : deux voies d'admission. La voie « vocabulaire fort »
    # évite de déplacer l'angle mort d'origine sur les fiches mono-colonne
    # (« Libellé : valeur » une par ligne : une seule colonne détectée).
    vocabulaire_fort_ok = nb_termes >= lexique.seuils.vocabulaire_fort
    vocabulaire_ok = nb_termes >= lexique.seuils.vocabulaire_min
    est_candidat = vocabulaire_fort_ok or (vocabulaire_ok and structure_ok)

    score = lexique.ponderations.vocabulaire * min(1.0, nb_termes / SATURATION_VOCABULAIRE) + (
        lexique.ponderations.structure_tableau if structure_ok else 0.0
    )

    motif = ""
    if not est_candidat:
        # Le motif nomme l'échec de CHACUNE des deux voies (constat 2 de revue).
        voies = []
        if vocabulaire_ok:
            voies.append(
                f"structure de tableau non détectée (colonnes {nb_colonnes}, lignes {nb_lignes}, grille {grille_tracee})"
            )
        else:
            voies.append(f"vocabulaire insuffisant ({nb_termes}/{lexique.seuils.vocabulaire_min})")
        if not vocabulaire_fort_ok:
            voies.append(f"voie vocabulaire fort manquée ({nb_termes}/{lexique.seuils.vocabulaire_fort})")
        motif = " ; ".join(voies)

    return ResultatDetection(
        est_candidat=est_candidat,
        score=score,
        vocabulaire_trouve=tuple(trouves),
        nb_colonnes=nb_colonnes,
        nb_lignes=nb_lignes,
        grille_tracee=bool(grille_tracee),
        motif_exclusion=motif,
    )

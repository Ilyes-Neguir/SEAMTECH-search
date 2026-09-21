"""Lexique configurable de détection structurelle des fiches techniques.

La Phase 0 a montré un angle mort : des fiches réelles (variante « génois »)
portent un vocabulaire absent de :data:`seamtech_search.anchors.TECHNICAL_ANCHORS`
et sortaient donc du recensement. Le correctif (PR 1) n'étend pas une constante
Python : il déplace le vocabulaire dans ``config/lexique_fiches.json``, lu au
moment de l'exécution, pour qu'un terme nouveau trouvé sur les vraies fiches
soit ajoutable sans redéploiement.

Décision de couche (PR 2, §17.1 du plan v3.0) : ce module est destiné à la
couche métier « fiches », PostgreSQL uniquement côté stockage — sans incidence
ici : le lexique est un simple fichier JSON de configuration, sans base.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field

LOGGER = logging.getLogger("seamtech_search.lexique")

# Lexique engagé dans le dépôt (versionné). Un déploiement peut le remplacer
# en passant un autre chemin — voir charger_lexique().
CHEMIN_LEXIQUE_PAR_DEFAUT = Path(__file__).resolve().parents[1] / "config" / "lexique_fiches.json"


def normaliser_terme(terme: str) -> str:
    """Minuscules, sans accents, espaces comprimés — forme de comparaison."""
    decompose = unicodedata.normalize("NFD", terme)
    sans_signes = "".join(caractere for caractere in decompose if not unicodedata.combining(caractere))
    return " ".join(sans_signes.lower().split())


class SeuilsStructure(BaseModel):
    """Seuils de la détection structurelle (documentés dans le lexique JSON)."""

    vocabulaire_min: int = Field(default=2, ge=1)
    nb_colonnes_min: int = Field(default=3, ge=1)
    nb_lignes_min: int = Field(default=3, ge=1)


class PonderationsStructure(BaseModel):
    """Poids du score explicatif (vocabulaire + structure du tableau)."""

    vocabulaire: float = Field(default=0.6, ge=0.0, le=1.0)
    structure_tableau: float = Field(default=0.4, ge=0.0, le=1.0)


class LexiqueFiches(BaseModel):
    """Lexique des fiches techniques, lu depuis ``config/lexique_fiches.json``.

    ``vocabulaire_normalise`` est recalculé à la validation : les termes du
    fichier peuvent être écrits avec accents et majuscules, la comparaison se
    fait toujours sur la forme normalisée (sans accents, minuscule).
    """

    version: int = Field(ge=1)
    description: str = ""
    vocabulaire_cotes: list[str] = Field(min_length=1)
    seuils: SeuilsStructure = Field(default_factory=SeuilsStructure)
    ponderations: PonderationsStructure = Field(default_factory=PonderationsStructure)
    vocabulaire_normalise: list[str] = Field(default_factory=list, exclude=True)

    def model_post_init(self, __context: object) -> None:
        normalises = []
        for terme in self.vocabulaire_cotes:
            normalise = normaliser_terme(terme)
            if normalise and normalise not in normalises:
                normalises.append(normalise)
        if not normalises:
            raise ValueError("Le lexique ne contient aucun terme exploitable après normalisation.")
        self.vocabulaire_normalise = normalises

    @property
    def nb_termes(self) -> int:
        return len(self.vocabulaire_normalise)


def charger_lexique(chemin: str | Path | None = None) -> LexiqueFiches:
    """Charge le lexique JSON. Échec bruyant si le fichier est absent ou invalide.

    ``None`` = lexique engagé du dépôt (``config/lexique_fiches.json``).
    Un chemin alternatif permet de tester un enrichissement sans toucher au
    dépôt (c'est le mécanisme « ajout à chaud » couvert par les tests).
    """
    chemin_resolu = Path(chemin).expanduser() if chemin is not None else CHEMIN_LEXIQUE_PAR_DEFAUT
    if not chemin_resolu.is_file():
        raise FileNotFoundError(
            f"Lexique de fiches introuvable : {chemin_resolu}. "
            "Restaurez config/lexique_fiches.json ou passez un chemin explicite."
        )
    with chemin_resolu.open("r", encoding="utf-8") as fichier:
        donnees = json.load(fichier)
    try:
        lexique = LexiqueFiches.model_validate(donnees)
    except Exception as erreur:
        # Conséquence : l'inventaire ne peut pas démarrer avec un lexique
        # cassé — mieux vaut refuser que recenser à côté.
        LOGGER.error("Lexique invalide (%s) : %s — conséquence : arrêt, aucune analyse lancée.", chemin_resolu, erreur)
        raise ValueError(f"Lexique invalide ({chemin_resolu}) : {erreur}") from erreur
    LOGGER.debug("Lexique chargé : %d termes (version %s) depuis %s", lexique.nb_termes, lexique.version, chemin_resolu)
    return lexique

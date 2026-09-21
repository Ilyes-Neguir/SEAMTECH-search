"""Corpus d'entraînement du classifieur de type de voile (Lot F, §10.4).

État des lieux honnête : le fonds réel compte UNE fiche (7792-SO, spi). Le
plan anticipe « synthétique seul ≈ 70 %, +100 exemples réels ≈ 87 % » — ces
ordres de grandeur viennent du benchmark du commanditaire et restent à
re-mesurer ici. Ce module génère donc :

- un corpus SYNTHÉTIQUE étiqueté (4 types : spi, genois, foc, grand_voile),
  avec des variantes lexicales et des CAS PIÈGES sans mot-clé évident ;
- un noyau RÉEL : la seule vraie fiche du dépôt (7792-SO → spi), conservé
  dans le jeu d'évaluation — jamais dans l'entraînement.

Les règles de comparaison (:func:`regles_type_voile`) sont le socle actuel du
projet : mots-clés ordonnés. Le modèle ne les REMPLACE pas (§10.4 : il
n'intervient que là où elles échouent) ; il doit être mesuré CONTRE elles et
annoncé comme ne faisant pas mieux s'il ne fait pas mieux.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

ETIQUETTES = ("spi", "genois", "foc", "grand_voile")

# Vocabulaire par classe — termes réels de voilerie, aucun inventé pour « aider »
# le modèle : ce sont les mots du métier (plan, vérité terrain, lexique dépôt).
VOCABULAIRE: dict[str, tuple[str, ...]] = {
    "spi": (
        "spinnaker", "asymétrique", "symétrique", "chaussette", "tangon",
        "vent arrière", "voile creuse", "portée au largue", "emmaganiseur",
        "bout de manœuvre", "guindant léger", "nylon léger",
    ),
    "genois": (
        "génois", "recouvrement", "enrouleur", "guindant", "bordure libre",
        "voile d'avant de croisière", "bande anti-UV", "penon de bordure",
        "écoute sur winch", "rattrapage de creux",
    ),
    "foc": (
        "foc", "tourmentin", "solent", "trinquette", "voile de brise",
        "petite voile d'avant", "ris de tempête", "guindant court",
        "mousquetons de draille", "capelage",
    ),
    "grand_voile": (
        "grand-voile", "lattée", "corne", "coulisseaux de mât", "bordure de bôme",
        "voile principale", "réglage de pataras", "lattes forcées", "chariots d'écoute",
        "guindant de mât", "trois ris",
    ),
}

# Modèles de phrases — la grammaire d'une note d'atelier, pas des phrases
# marketing. Chaque gabarit consomme 2 à 4 termes de la classe.
GABARITS_PHRASES = (
    "{a} : {b}, montage {c} selon gamme.",
    "Fiche {a} — contrôle {b} et {c} avant coupe.",
    "Atelier : {a} avec {b}; {c} à confirmer avec le client.",
    "{a} {b} pour régate, {c} renforcé.",
    "Commande {a}, option {b}, livraison {c} à planifier.",
)

# CAS PIÈGES pour la comparaison modèle/règles : la classe est identifiable par
# le vocabulaire MAIS le mot-clé des règles est absent ou altéré.
CAS_PIEGES: dict[str, tuple[str, ...]] = {
    "spi": (
        "voile creuse portée au vent arrière avec chaussette et tangon",
        "nylon léger pour descente sous le vent, emmagasiné en chaussette",
    ),
    "genois": (
        "grande voile d'avant à recouvrement sur enrouleur, bande protégée",
        "voile d'étrave de croisière avec rattrapage de creux",
    ),
    "foc": (
        "petite voile d'avant de brise sur draille, capelage haut",
        "voile de tempête à mousquetons, guindant court, hissée dans le vent fort",
    ),
    "grand_voile": (
        "voile principale lattée à corne avec chariots et trois bandes de réduction",
        "voile de mât à coulisseaux forcés, réglée au pataras",
    ),
}

# Les règles actuelles : mots-clés ordonnés, premier trouvé gagne. C'est le
# socle mesuré — si le modèle ne le bat pas, le rapport doit le dire.
MOTS_CLES_REGLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spi", ("spi", "spinnaker")),
    ("genois", ("génois", "genois")),
    ("foc", ("foc", "tourmentin", "solent", "trinquette")),
    ("grand_voile", ("grand-voile", "grand voile")),
)


def regles_type_voile(texte: str) -> str | None:
    """Classification par règles (mots-clés ordonnés) — la référence à battre."""
    bas = texte.lower()
    for etiquette, mots in MOTS_CLES_REGLES:
        for mot in mots:
            if mot in bas:
                return etiquette
    return None


def generer_corpus_synthetique(
    par_classe: int = 40, graine: int = 20260921
) -> list[dict[str, Any]]:
    """Exemples synthétiques étiquetés ; graine fixe ⇒ corpus reproductible."""
    generateur = random.Random(graine)
    exemples: list[dict[str, Any]] = []
    for etiquette in ETIQUETTES:
        vocabulaire = VOCABULAIRE[etiquette]
        for _ in range(par_classe):
            termes = generateur.sample(list(vocabulaire), k=3)
            gabarit = generateur.choice(GABARITS_PHRASES)
            texte = gabarit.format(a=termes[0], b=termes[1], c=termes[2])
            exemples.append({"texte": texte, "etiquette": etiquette, "origine": "synthetique"})
    return exemples


def cas_pieges() -> list[dict[str, Any]]:
    """Le jeu où les règles sont attendues en défaut (pas de mot-clé)."""
    pieges: list[dict[str, Any]] = []
    for etiquette, textes in CAS_PIEGES.items():
        for texte in textes:
            pieges.append({"texte": texte, "etiquette": etiquette, "origine": "synthetique"})
    return pieges


def noyau_reel(chemin_verite: Path) -> list[dict[str, Any]]:
    """La vraie fiche 7792-SO : le seul exemple RÉEL disponible (spi).

    Le texte est celui de la vérité terrain — aucune valeur inventée (RG6).
    À calibrer sur 20-30 fiches réelles quand le commanditaire les fournira.
    """
    import json

    donnees = json.loads(Path(chemin_verite).read_text(encoding="utf-8"))
    # Structure réelle : {"<nom-fichier.pdf>": {"gabarit": …, "attendu": {champ: valeur}}}
    attendu: dict[str, Any] = {}
    for valeur_fichier in donnees.values():
        if isinstance(valeur_fichier, dict) and isinstance(valeur_fichier.get("attendu"), dict):
            attendu.update(valeur_fichier["attendu"])
    morceaux = _aplatir(attendu)
    if not morceaux:
        return []
    etiquette = "spi"  # 7792-SO est un Spi Asymétrique (document réel)
    return [{"texte": " | ".join(morceaux), "etiquette": etiquette, "origine": "reel"}]


def _aplatir(objet: Any) -> list[str]:
    """Valeurs terminales d'un imbriqué de champs, en texte."""
    morceaux: list[str] = []
    if isinstance(objet, dict):
        for valeur in objet.values():
            morceaux.extend(_aplatir(valeur))
    elif isinstance(objet, (list, tuple)):
        for valeur in objet:
            morceaux.extend(_aplatir(valeur))
    elif objet not in (None, ""):
        morceaux.append(str(objet))
    return morceaux


def partager(
    exemples: list[dict[str, Any]], ratio_entrainement: float = 0.75, graine: int = 20260921
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partage reproductible entraînement/évaluation, stratifié par étiquette."""
    generateur = random.Random(graine)
    par_classe: dict[str, list[dict[str, Any]]] = {}
    for exemple in exemples:
        par_classe.setdefault(exemple["etiquette"], []).append(exemple)
    entrainement: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []
    for classe, liste in par_classe.items():
        generateur.shuffle(liste)
        coupe = max(1, int(len(liste) * ratio_entrainement))
        entrainement.extend(liste[:coupe])
        evaluation.extend(liste[coupe:])
    return entrainement, evaluation

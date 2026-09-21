"""Classifieur maison du type de voile — centroïdes sur embeddings (Lot F).

Choix assumé pour les postes 8 Go : centroïdes par classe sur des embeddings
L2-normalisés (cosine = produit scalaire). Pas de dépendance d'apprentissage
supplémentaire (numpy suffit), sérialisation JSON lisible, rechargement
vérifié par test. Il ne REMPLACE pas les règles : il n'est appelé que là où
les règles échouent (§10.4), et son rapport est toujours publié AVEC celui des
règles sur le même jeu.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from seamtech_search.ml.corpus import regles_type_voile

VERSION_ALGORITHME = "centroides-cosine-v1"


class ClassifieurCentroïdes:
    """Un centroïde normalisé par classe ; prédiction = cosine maximale."""

    def __init__(self, centroides: dict[str, np.ndarray], dimension: int, encodeur_nom: str) -> None:
        self.centroides = centroides
        self.dimension = dimension
        self.encodeur_nom = encodeur_nom

    @classmethod
    def entrainer(cls, encodeur: Any, exemples: Sequence[dict[str, Any]]) -> "ClassifieurCentroïdes":
        if not exemples:
            raise ValueError("corpus d'entraînement vide")
        textes = [exemple["texte"] for exemple in exemples]
        vecteurs = encodeur.encoder(textes)
        somme: dict[str, np.ndarray] = {}
        compte: dict[str, int] = {}
        for vecteur, exemple in zip(vecteurs, exemples):
            etiquette = exemple["etiquette"]
            somme[etiquette] = somme.get(etiquette, np.zeros(vecteur.shape[0], dtype=np.float32)) + vecteur
            compte[etiquette] = compte.get(etiquette, 0) + 1
        centroides = {
            etiquette: _normaliser(total / compte[etiquette]) for etiquette, total in somme.items()
        }
        return cls(centroides, vecteurs.shape[1], encodeur.nom)

    def predire(self, encodeur: Any, texte: str) -> tuple[str | None, float]:
        """Étiquette + marge (meilleure cosine − deuxième). Vide → (None, 0)."""
        if not self.centroides:
            return None, 0.0
        vecteur = encodeur.encoder([texte])[0]
        scores = {etiquette: float(np.dot(vecteur, centroide)) for etiquette, centroide in self.centroides.items()}
        tries = sorted(scores.items(), key=lambda paire: paire[1], reverse=True)
        meilleure, marge = tries[0], (tries[0][1] - tries[1][1] if len(tries) > 1 else tries[0][1])
        return meilleure[0], float(marge)

    def sauver(self, chemin: Path) -> None:
        chemin = Path(chemin)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        donnees = {
            "algorithme": VERSION_ALGORITHME,
            "dimension": self.dimension,
            "encodeur": self.encodeur_nom,
            "centroïdes": {
                etiquette: [float(v) for v in centroide] for etiquette, centroide in self.centroides.items()
            },
        }
        chemin.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def charger(cls, chemin: Path) -> "ClassifieurCentroïdes":
        donnees = json.loads(Path(chemin).read_text(encoding="utf-8"))
        centroides = {
            etiquette: np.asarray(valeurs, dtype=np.float32)
            for etiquette, valeurs in donnees["centroïdes"].items()
        }
        return cls(centroides, int(donnees["dimension"]), str(donnees.get("encodeur", "?")))


def _normaliser(vecteur: np.ndarray) -> np.ndarray:
    norme = float(np.linalg.norm(vecteur))
    return vecteur if norme == 0.0 else vecteur / norme


def mesurer(
    encodeur: Any,
    classifieur: ClassifieurCentroïdes | None,
    evaluation: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Exactitudes modèle ET règles sur le MÊME jeu — publiées ensemble.

    Un modèle qui ne fait pas mieux que les règles doit être annoncé comme
    tel : les deux chiffres sont dans le rapport, jamais l'un sans l'autre.
    """
    total = len(evaluation)
    if total == 0:
        return {"taille": 0, "exactitude_modele": None, "exactitude_regles": None}
    ok_modele = 0
    ok_regles = 0
    sans_regle = 0
    ok_modele_sans_regle = 0
    for exemple in evaluation:
        attendu = exemple["etiquette"]
        regle = regles_type_voile(exemple["texte"])
        if regle == attendu:
            ok_regles += 1
        else:
            sans_regle += 1
            if classifieur is not None:
                predit, _marge = classifieur.predire(encodeur, exemple["texte"])
                if predit == attendu:
                    ok_modele_sans_regle += 1
        if classifieur is not None:
            predit, _marge = classifieur.predire(encodeur, exemple["texte"])
            if predit == attendu:
                ok_modele += 1
    return {
        "taille": total,
        "exactitude_modele": round(ok_modele / total, 4) if classifieur is not None else None,
        "exactitude_regles": round(ok_regles / total, 4),
        "regles_en_echec": sans_regle,
        "modele_recupere": ok_modele_sans_regle,
    }

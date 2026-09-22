"""Encodeurs d'embeddings — e5-small ONNX en production (Lot F, §10.4).

Aucun PyTorch : ``onnxruntime`` exécute le graphe ONNX, ``tokenizers``
tokenise, ``numpy`` pool et normalise. Deux implémentations de la même
interface :

- :class:`EncodeurONNX` — le chemin de production (e5-small multilingue,
  384 dimensions, préfixes e5 ``query:``/``passage:``). Il charge
  ``model.onnx`` + ``tokenizer.json`` depuis un dossier du disque ; les poids
  n'arrivent JAMAIS seuls : ``python -m seamtech_search.ml.telecharger`` est
  une action opérateur explicite (aucun téléchargement au runtime).
- :class:`EncodeurDeterministe` — repli de TEST (hachage de tokens vers 384
  dimensions, normalisé). Déterministe et sans poids, il prouve le câblage
  (source vecteurs, RRF, entraînement du classifieur) quand les poids e5 ne
  sont pas présents. Il est ÉTIQUETÉ « repli » partout : ses chiffres ne sont
  jamais annoncés comme des chiffres e5.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np

LOGGER = logging.getLogger(__name__)

DIMENSION_EMBEDDING = 384  # plan §10.4 : e5-small, colonne vector(384)
FICHIER_MODELE = "model.onnx"
FICHIER_TOKENIZER = "tokenizer.json"
FICHIER_META = "meta.json"
LONGUEUR_MAX = 256  # textes de fiches courts ; 512 e5 tronqués = surcoût inutile


class ModeleAbsent(RuntimeError):
    """Poids e5 absents du disque — message explicite, jamais de fallback
    silencieux quand l'opérateur a demandé le vrai encodeur."""


class Encodeur(Protocol):
    nom: str
    dimension: int

    def encoder(self, textes: Sequence[str], prefixe: str = "passage: ") -> np.ndarray: ...

    def vecteur_requete(self, texte: str) -> str | None:
        """Représentation ``[x,y,…]`` pour pgvector (``%s::vector``)."""
        ...


def _normaliser(matrice: np.ndarray) -> np.ndarray:
    normes = np.linalg.norm(matrice, axis=1, keepdims=True)
    normes[normes == 0.0] = 1.0
    return matrice / normes


def _vers_pgvector(vecteur: np.ndarray) -> str:
    return "[" + ",".join(f"{float(v):.7f}" for v in vecteur) + "]"


class EncodeurONNX:
    """e5-small multilingue via ONNX — production (postes 8 Go)."""

    nom = "e5-small-onnx"
    dimension = DIMENSION_EMBEDDING

    def __init__(self, dossier: Path, longueur_max: int = LONGUEUR_MAX) -> None:
        import onnxruntime
        from tokenizers import Tokenizer

        self.dossier = Path(dossier)
        chemin_modele = self.dossier / FICHIER_MODELE
        chemin_tokenizer = self.dossier / FICHIER_TOKENIZER
        if not chemin_modele.exists() or not chemin_tokenizer.exists():
            raise ModeleAbsent(
                f"Modèle e5 absent dans {self.dossier} ({FICHIER_MODELE} et/ou "
                f"{FICHIER_TOKENIZER} manquants). Action opérateur : "
                "python -m seamtech_search.ml.telecharger — aucun téléchargement "
                "n'est fait au runtime."
            )
        self.longueur_max = longueur_max
        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 2  # postes 8 Go : inférence modeste
        self.session = onnxruntime.InferenceSession(str(chemin_modele), sess_options=options)
        self.entrees = {e.name for e in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(chemin_tokenizer))
        self.tokenizer.enable_truncation(max_length=self.longueur_max)
        self.tokenizer.enable_padding(direction="right")

    def encoder(self, textes: Sequence[str], prefixe: str = "passage: ") -> np.ndarray:
        if not textes:
            return np.zeros((0, self.dimension), dtype=np.float32)
        prepares = [f"{prefixe}{texte}".strip() for texte in textes]
        encodage = self.tokenizer.encode_batch(prepares)
        input_ids = np.array([e.ids for e in encodage], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodage], dtype=np.int64)
        feed: dict[str, np.ndarray] = {}
        if "input_ids" in self.entrees:
            feed["input_ids"] = input_ids
        if "attention_mask" in self.entrees:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self.entrees:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        sortie = self.session.run(None, feed)[0]  # (n, seq, dim)
        masque = attention_mask[:, :, None].astype(np.float32)
        somme = (sortie * masque).sum(axis=1)
        longueurs = masque.sum(axis=1)
        longueurs[longueurs == 0.0] = 1.0
        return _normaliser(somme / longueurs).astype(np.float32)

    def vecteur_requete(self, texte: str) -> str | None:
        texte = texte.strip()
        if not texte:
            return None
        return _vers_pgvector(self.encoder([texte], prefixe="query: ")[0])


class EncodeurDeterministe:
    """Repli de TEST : hachage de tokens → 384 dimensions, L2-normalisé.

    Déterministe (md5 du token), sans poids, sans réseau. Il sert à prouver le
    câblage vecteurs/RRF/classifieur en attendant les poids e5 ; ses mesures
    sont toujours publiées comme « repli déterministe », JAMAIS comme e5.
    """

    nom = "repli-deterministe-v1"
    dimension = DIMENSION_EMBEDDING

    def encoder(self, textes: Sequence[str], prefixe: str = "passage: ") -> np.ndarray:
        sortie = np.zeros((len(textes), self.dimension), dtype=np.float32)
        for ligne, texte in enumerate(textes):
            for token in f"{prefixe}{texte}".lower().replace("\n", " ").split():
                token = token.strip(".,;:!?()[]«»\"'")
                if not token:
                    continue
                empreinte = hashlib.md5(token.encode("utf-8"), usedforsecurity=False).digest()
                indice = int.from_bytes(empreinte[:4], "little") % self.dimension
                signe = 1.0 if empreinte[4] % 2 == 0 else -1.0
                sortie[ligne, indice] += signe * (1.0 + math.log1p(len(token)) * 0.1)
        return _normaliser(sortie).astype(np.float32)

    def vecteur_requete(self, texte: str) -> str | None:
        texte = texte.strip()
        if not texte:
            return None
        return _vers_pgvector(self.encoder([texte], prefixe="query: ")[0])


def charger_encodeur(dossier_modeles: Path) -> EncodeurONNX | None:
    """L'e5 ONNX s'il est sur le disque, sinon None (dégradation : la recherche
    reste lexicale/trigrammes/texte — la source vecteurs attend ses données)."""
    dossier = Path(dossier_modeles)
    if not (dossier / FICHIER_MODELE).exists():
        return None
    try:
        encodeur = EncodeurONNX(dossier)
    except ModeleAbsent:
        return None
    except Exception as exc:  # fichier corrompu : ne pas tuer l'API
        LOGGER.warning("Encodeur ONNX illisible (%s : %s) — recherche sans vecteurs.", type(exc).__name__, exc)
        return None
    meta = dossier / FICHIER_META
    if meta.exists():
        try:
            encodeur.nom = json.loads(meta.read_text(encoding="utf-8")).get("nom", encodeur.nom)
        except (OSError, json.JSONDecodeError):
            pass
    return encodeur


def dimensions_et_nom(encodeur: Any) -> dict[str, Any]:
    return {"nom": encodeur.nom, "dimension": encodeur.dimension}

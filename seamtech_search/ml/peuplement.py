"""Peuplement des vecteurs — activer la source dormante du Lot E (Lot F).

La source ``vecteurs`` de ``recherche.py`` lit ``documents.embedding`` et
``chunk.embedding`` (pgvector, cosine). Ce module PRODUIT ces vecteurs :

- un chunk « résumé de champs » par fiche VALIDÉE (le texte pondéré A/B/C
  existe déjà en ``fiche.champs_texte`` depuis les migrations 012/013) ;
- les documents rattachés à une fiche qui portent déjà ``id_fiche``.

Idempotent : chaque appel remplace les chunks de résumé et recalcule les
embeddings — jamais de doublon, jamais de purge d'autres natures de chunks.
"""

from __future__ import annotations

import logging
import time
from typing import Any

LOGGER = logging.getLogger(__name__)

NATURE_RESUME = "resume_champs"

_SQL_FICHES_VALIDES = "SELECT id_fiche, champs_texte FROM fiche WHERE statut = 'valide'"
_SQL_CHUNKS_RESUME_SUPPRIMER = "DELETE FROM chunk WHERE id_fiche = %s AND nature = %s"
_SQL_CHUNK_RESUME_INSERER = (
    "INSERT INTO chunk (id_fiche, nature, contenu, embedding) VALUES (%s, %s, %s, %s::vector)"
)
_SQL_DOCUMENTS_A_VECTORISER = (
    "SELECT id, content FROM documents WHERE id_fiche IS NOT NULL AND embedding IS NULL LIMIT %s"
)
_SQL_DOCUMENT_VECTORISER = "UPDATE documents SET embedding = %s::vector WHERE id = %s"


def peupler_vecteurs(index: Any, encodeur: Any, taille_lot: int = 200) -> dict[str, Any]:
    """Encode les fiches validées + leurs documents ; rend les comptes mesurés."""
    debut = time.perf_counter()
    nb_fiches = 0
    nb_documents = 0
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(_SQL_FICHES_VALIDES)
            fiches = cursor.fetchall()
            if not fiches:
                return {
                    "fiches": 0,
                    "documents": 0,
                    "duree_ms": 0.0,
                    "encodeur": encodeur.nom,
                    "note": "aucune fiche validée : rien à vectoriser",
                }
            textes = [champs_texte or "" for (_id, champs_texte) in fiches]
            vecteurs = encodeur.encoder(textes)
            for (id_fiche, champs_texte), vecteur in zip(fiches, vecteurs):
                if not (champs_texte or "").strip():
                    continue
                cursor.execute(_SQL_CHUNKS_RESUME_SUPPRIMER, (id_fiche, NATURE_RESUME))
                cursor.execute(
                    _SQL_CHUNK_RESUME_INSERER,
                    (id_fiche, NATURE_RESUME, champs_texte, _vers_pgvector(vecteur)),
                )
                nb_fiches += 1
            while True:
                cursor.execute(_SQL_DOCUMENTS_A_VECTORISER, (taille_lot,))
                lignes = cursor.fetchall()
                if not lignes:
                    break
                vecteurs_documents = encodeur.encoder([contenu or "" for (_id, contenu) in lignes])
                for (id_document, _contenu), vecteur in zip(lignes, vecteurs_documents):
                    cursor.execute(_SQL_DOCUMENT_VECTORISER, (_vers_pgvector(vecteur), id_document))
                    nb_documents += 1
        connexion.commit()
    duree_ms = (time.perf_counter() - debut) * 1000.0
    LOGGER.info(
        "Peuplement vecteurs (%s) : %d fiches, %d documents en %.1f ms.",
        encodeur.nom,
        nb_fiches,
        nb_documents,
        duree_ms,
    )
    return {
        "fiches": nb_fiches,
        "documents": nb_documents,
        "duree_ms": round(duree_ms, 1),
        "encodeur": encodeur.nom,
    }


def _vers_pgvector(vecteur: Any) -> str:
    return "[" + ",".join(f"{float(v):.7f}" for v in vecteur) + "]"

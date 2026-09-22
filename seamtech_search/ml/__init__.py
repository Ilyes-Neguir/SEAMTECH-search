"""Lot F — IA locale (plan v3.0 §10.4, §17.2, §17.5).

Décision commanditaire du 21/09/2026 : **8 Go de RAM**. Conséquences :
onnxruntime + tokenizers + numpy uniquement, jamais de PyTorch ; e5-small
multilingue (384 dimensions) pour les embeddings ; pas d'assistant 7B dans ce
lot (marginal à 8 Go — 2 à 10 jetons/s en Q4, plan §17.14 Phase 4 reporté).

La source de recherche ``vecteurs`` (cosine pgvector, ``recherche.py``) était
écrite et DORMANTE depuis le Lot E : ce paquet produit les vecteurs et le
premier modèle maison (classifieur du type de voile), mesuré CONTRE les
règles — un modèle qui ne fait pas mieux qu'elles doit être annoncé comme tel.
"""

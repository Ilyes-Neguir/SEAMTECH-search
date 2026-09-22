"""Fixtures partagées du Lot E — recherche hybride (plan v3.0 §17.2).

``base_recherche`` : base PostgreSQL JETABLE (créée puis supprimée par le
test), migrée 001→012, avec un corpus de voilerie semé de façon
déterministe : référentiels, 12 fiches validées + 2 a_valider + 1 rejetée,
chunks de texte PDF, documents avec embeddings. Les fiches validées ont leur
texte de recherche pondéré rempli par la VRAIE fonction de migration — la
recherche ne trouve jamais une fiche non validée par accident d'index.

Chaque test crée sa propre base : aucune dépendance entre exécutions.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

# Corpus déterministe (pas de tirage aléatoire : les attendus des tests sont
# écrits en clair à partir de ces constantes).
TYPES_VOILE = (
    ("GV", "Grand-voile", "voile_triangulaire"),
    ("GEN", "Génois", "voile_avant"),
    ("SPI", "Spinnaker", "voile_porteuse"),
    ("TRM", "Tourmentin", "voile_avant"),
)
MATERIAUX = (("Monofilm", "film", "150.0"), ("Dacron", "tissu", "220.0"), ("Mylar", "film", "100.0"))
CLIENTS = (("Voilerie Atlantique", "Brest"), ("Chantier Méditerranée", "La Ciotat"))
BATEAUX = (("First 30", "30 pieds"), ("Sun Fast 36", "36 pieds"), ("Figaro 3", "29 pieds"))

# 15 fiches : 12 validées (cherchables), 2 a_valider, 1 rejetée.
# (code, titre, type, gamme, client, bateau, matiere, annee, notes, statut)
FICHES_CORPUS: tuple[tuple[str, str, str, str, int, int, int, int, str, str], ...] = (
    ("0701-GV-001", "Grand-voile régate First 30", 0, "Régate", 0, 0, 0, 2024, "", "valide"),
    ("0701-GV-002", "Grand-voile croisière Sun Fast 36", 0, "Croisière", 0, 1, 1, 2023, "", "valide"),
    ("0702-GV-003", "Grand-voile lattée Figaro 3", 0, "Régate", 1, 2, 0, 2025, "", "valide"),
    ("0801-GEN-001", "Génois lourd First 30", 1, "Croisière", 0, 0, 1, 2023, "", "valide"),
    ("0801-GEN-002", "Génois médium Sun Fast 36", 1, "Régate", 1, 1, 2, 2024, "", "valide"),
    ("0802-GEN-003", "Génois léger Figaro 3", 1, "Régate", 1, 2, 2, 2025, "", "valide"),
    ("0812-SPI-001", "Spinnaker asymétrique Sun Fast 36", 2, "Régate", 0, 1, 2, 2024, "", "valide"),
    ("0812-SPI-002", "Spinnaker symétrique First 30", 2, "Course", 1, 0, 2, 2023, "", "valide"),
    ("0901-TRM-001", "Tourmentin Dacron First 30", 3, "Croisière", 0, 0, 1, 2022, "", "valide"),
    ("0902-GV-004", "Grand-voile coupe triradiale", 0, "Régate", 1, 1, 0, 2025, "", "valide"),
    ("0903-GEN-004", "Génois solent croisière", 1, "Croisière", 0, 2, 1, 2024, "", "valide"),
    # La fiche « notes » : le mot régatier n'existe QUE dans les notes (poids C).
    ("0904-GV-005", "Grand-voile coupe horizontale", 0, "Croisière", 0, 0, 1, 2023,
     "profil régatier demandé par le client malgré la gamme croisière", "valide"),
    ("1001-GV-006", "Grand-voile en attente de validation", 0, "Régate", 0, 0, 0, 2025, "", "a_valider"),
    ("1002-GEN-005", "Génois en attente de validation", 1, "Croisière", 1, 1, 1, 2025, "", "a_valider"),
    ("1003-SPI-003", "Spinnaker rejeté contrôle surface", 2, "Régate", 0, 2, 2, 2025, "", "rejete"),
)

# Texte PDF (chunks) — mots volontairement absents des champs métier.
CHUNKS = (
    ("0812-SPI-002", "croquis", "renforts de guindant en kevlar, lattes forcées sur la bordure"),
    ("0701-GV-001", "croquis", "coulisseaux de guindant montés sur rail, nerf de chute spectra"),
)

# Embeddings synthétiques (384 dimensions) pour la branche vectorielle.
EMBEDDING_A = "[" + ",".join(["1"] + ["0"] * 383) + "]"
EMBEDDING_B = "[" + ",".join(["0", "1"] + ["0"] * 382) + "]"
# documents.embedding : deux fiches portent un vecteur (la branche vecteurs du
# RRF est testable sans encodeur de production — le Lot F livrera le vrai).
EMBEDDINGS_DOCUMENTS = {"0701-GV-001": EMBEDDING_A, "0812-SPI-001": EMBEDDING_B}


def _creer_base_jetable() -> tuple[str, str]:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nom_base = f"recherche_test_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(DATABASE_URL)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom_base)))
    finally:
        administrateur.close()
    # La requête éventuelle de l'URL (?host=/chemin/socket pour une connexion
    # par socket UNIX — mesure RG14 sans réseau) est préservée telle quelle :
    # le découpage ne doit porter que sur la partie avant « ? ».
    url_principale, separateur, requete = DATABASE_URL.partition("?")
    partie = url_principale.rsplit("/", 1)
    return nom_base, f"{partie[0]}/{nom_base}" + (f"?{requete}" if separateur else "")


def _supprimer_base_jetable(nom_base: str) -> None:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    nettoie = psycopg2.connect(DATABASE_URL)
    nettoie.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with nettoie.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom_base)))
    finally:
        nettoie.close()


def semer_corpus(index: Any) -> dict[str, int]:  # noqa: ANN401 - SearchIndex réel
    """Sème le corpus ; rend {code: id_fiche}. Les fiches validées passent par
    la vraie fonction de rafraîchissement — l'état réel de production."""
    from seamtech_search.recherche import invalider_cache_synonymes

    invalider_cache_synonymes()
    ids: dict[str, int] = {}
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            for code, libelle, famille in TYPES_VOILE:
                cursor.execute(
                    "INSERT INTO type_voile (code, libelle, famille) VALUES (%s, %s, %s)",
                    (code, libelle, famille),
                )
            for nom, famille, grammage in MATERIAUX:
                cursor.execute(
                    "INSERT INTO materiau (nom, famille, grammage_g_m2) VALUES (%s, %s, %s)",
                    (nom, famille, grammage),
                )
            ids_clients = []
            for nom, chantier in CLIENTS:
                cursor.execute("INSERT INTO client (nom, chantier) VALUES (%s, %s) RETURNING id_client", (nom, chantier))
                ids_clients.append(int(cursor.fetchone()[0]))
            ids_bateaux = []
            for nom, taille in BATEAUX:
                cursor.execute("INSERT INTO bateau (nom, taille) VALUES (%s, %s) RETURNING id_bateau", (nom, taille))
                ids_bateaux.append(int(cursor.fetchone()[0]))

            for (code, titre, i_type, gamme, i_client, i_bateau, i_matiere, annee, notes, statut) in FICHES_CORPUS:
                cursor.execute(
                    "INSERT INTO fiche (code, titre, id_type_voile, gamme, id_client, id_bateau,"
                    " date_edition, notes, statut) "
                    "VALUES (%s, %s, (SELECT id_type_voile FROM type_voile ORDER BY id_type_voile OFFSET %s LIMIT 1),"
                    " %s, %s, %s, make_date(%s, 6, 15), %s, %s) RETURNING id_fiche",
                    (code, titre, i_type, gamme, ids_clients[i_client], ids_bateaux[i_bateau], annee, notes, statut),
                )
                id_fiche = int(cursor.fetchone()[0])
                ids[code] = id_fiche
                # id_materiau EST renseigné : la facette « matière » joint
                # fiche_materiau → materiau (un lien NULL la rendrait aveugle).
                cursor.execute(
                    "INSERT INTO fiche_materiau (id_fiche, role, niveau, id_materiau, designation_texte) "
                    "VALUES (%s, 'panneau', 1, "
                    "(SELECT id_materiau FROM materiau ORDER BY id_materiau OFFSET %s LIMIT 1), "
                    "%s || ' ' || (SELECT grammage_g_m2 FROM materiau ORDER BY id_materiau OFFSET %s LIMIT 1) || 'g')",
                    (id_fiche, i_matiere, MATERIAUX[i_matiere][0], i_matiere),
                )
                cursor.execute(
                    "INSERT INTO fiche_galon (id_fiche, bande, couleur, matiere) VALUES (%s, 'guindant', 'bleu', 'polyester')",
                    (id_fiche,),
                )

            for code, nature, contenu in CHUNKS:
                cursor.execute(
                    "INSERT INTO chunk (id_fiche, nature, contenu) VALUES (%s, %s, %s)",
                    (ids[code], nature, contenu),
                )
            for code, embedding in EMBEDDINGS_DOCUMENTS.items():
                cursor.execute(
                    "INSERT INTO documents (path_key, path, name, parent_path, extension, size,"
                    " modified_at, is_dir, content, search_vector, id_fiche, role, embedding) "
                    "VALUES (%s, %s, %s, %s, '.pdf', 1, %s, FALSE, %s,"
                    " to_tsvector('simple', %s), %s, 'fiche', %s::vector)",
                    (
                        f"/archive/{code}/fiche.pdf", f"/archive/{code}/fiche.pdf",
                        "fiche.pdf", f"/archive/{code}", time.time(),
                        f"document {code}", f"document {code}",
                        ids[code], embedding,
                    ),
                )

            # Les fiches VALIDÉES reçoivent leur texte de recherche pondéré —
            # exactement comme le fait la validation en production.
            for (code, *_reste) in FICHES_CORPUS:
                if _reste[-1] == "valide":
                    cursor.execute("SELECT rafraichir_texte_recherche_fiche(%s)", (ids[code],))
    return ids


@pytest.fixture()
def base_recherche() -> Iterator[dict[str, Any]]:
    """Base PostgreSQL jetable migrée + corpus semé ; rend
    {index, url, nom, fiches:{code: id_fiche}}."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    from seamtech_search.indexer import SearchIndex

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    ids = semer_corpus(index)
    try:
        yield {"index": index, "url": url_base, "nom": nom_base, "fiches": ids}
    finally:
        index.close()
        _supprimer_base_jetable(nom_base)


def seuil_perf_p95_ms() -> float:
    """Seuil p95 APPLICABLE au contexte d'exécution (audit du 22/09).

    Le critère PRODUIT reste p95 < 100 ms (jamais desserré). Sur les runners
    CI partagés, la QUEUE de distribution subit le bruit du voisinage même
    SANS instrumentation : mesuré le 22/09 au run push 35720563422 —
    p50 = 6,6 ms mais p95 = 180,0 ms et max = 341,3 ms sur le jeu de 50
    requêtes (même SHA vert en pull_request). C'est pourquoi un seuil
    d'ENVIRONNEMENT CI plus permissif peut être positionné via
    ``SEAMTECH_PERF_P95_CI_MS`` : il est alors ÉTIQUETÉ comme tel dans la
    publication (jamais un chiffre desserré en silence) et justifié par cette
    mesure. Par défaut (local, production) : 100 ms = le critère produit."""
    brut = os.environ.get("SEAMTECH_PERF_P95_CI_MS")
    if not brut:
        return 100.0
    return float(brut)


def publier_mesure_perf(nom: str, p50_ms: float, p95_ms: float, max_ms: float, n: int) -> None:
    """Publie une mesure de latence dans le fichier JSONL désigné par
    ``SEAMTECH_PERF_JSON`` (étape CI dédiée ``perf``, sans instrumentation).
    Sans la variable : aucun fichier écrit — la mesure reste lisible dans la
    sortie pytest. Une ligne par mesure :
    ``{"nom": …, "p50_ms": …, "p95_ms": …, "max_ms": …, "n": …, "seuil_p95_ms": …}``."""
    chemin = os.environ.get("SEAMTECH_PERF_JSON")
    if not chemin:
        return
    mesure = {
        "nom": nom,
        "p50_ms": round(p50_ms, 2),
        "p95_ms": round(p95_ms, 2),
        "max_ms": round(max_ms, 2),
        "n": n,
        "seuil_p95_ms": round(seuil_perf_p95_ms(), 2),
    }
    with open(chemin, "a", encoding="utf-8") as f:
        f.write(json.dumps(mesure, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Lot I — assistant sourcé : jeu d'essai des 8 questions + semis partagés.
# Le jeu est ÉTIQUETÉ : la fiche 7792-SO vient du fonds RÉEL (document client,
# extraction gabarit), les cotes ajoutées au corpus Lot E sont SYNTHÉTIQUES
# (méthode 'synthetique' dans fiche_champ_extrait, jamais confondues avec le
# réel). Le même semis sert aux tests PostgreSQL ET au script de mesure
# (scripts/mesure_assistant.py) — une seule définition, jamais deux.
# ---------------------------------------------------------------------------

CHEMIN_FICHE_REELLE = Path(__file__).resolve().parents[1] / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"

# (question, étiquette) — l'étiquette dit DE QUOI VIENT la réponse attendue.
JEU_8_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("quelle est la SLU de la fiche 7792-SO ?", "cote par fiche — fonds réel (6,60 m, vérité terrain)"),
    ("quelles voiles pour le bateau 29er ?", "voiles par bateau — fonds réel (Spi Asymétrique)"),
    ("combien de fiches de type portant en 2024 ?", "comptage — corpus synthétique (1 fiche : 0812-SPI-001)"),
    ("quelles fiches ont une SLU entre 6,5 et 6,7 m ?", "intervalle — synthétique + réel (3 fiches)"),
    ("quelle matière pour le galon de bordure ?", "attribut galon — fonds réel (Nylon)"),
    ("quelle est la longueur du mât de la fiche 7792-SO ?", "SANS SOURCE → refus explicite"),
    ("quelle est la matière de la fiche 7792-SO ?", "AMBIGU → interprétations sourcées"),
    ("quel est le surplus de jonction de la fiche 7792-SO ?", "non applicable RG5 (consigné « ~ »)"),
)

# Cotes SLU SYNTHÉTIQUES ajoutées au corpus Lot E (jeu « finie ») :
# deux dans l'intervalle [6,5 ; 6,7] de la question 4, un hors intervalle.
SLU_SYNTHEtiques: dict[str, float] = {"0701-GV-001": 6.62, "0702-GV-003": 6.55, "0812-SPI-001": 7.15}


def enrichir_corpus_assistant(index: Any) -> dict[str, float]:
    """Ajoute au corpus Lot E des cotes SLU SYNTHÉTIQUES (jeu finie) + leur
    trace ``fiche_champ_extrait`` (methode='synthetique', sans page/zone — la
    zone reste l'apanage de l'extraction réelle)."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            for code, slu in SLU_SYNTHEtiques.items():
                cursor.execute("SELECT id_fiche FROM fiche WHERE code = %s", (code,))
                ligne = cursor.fetchone()
                if ligne is None:
                    raise RuntimeError(f"corpus Lot E attendu manquant : {code}")
                cursor.execute(
                    "INSERT INTO fiche_cotes (id_fiche, jeu, slu_m) VALUES (%s, 'finie', %s) "
                    "ON CONFLICT (id_fiche, jeu) DO NOTHING",
                    (int(ligne[0]), slu),
                )
                cursor.execute(
                    "INSERT INTO fiche_champ_extrait (id_fiche, champ, rang, table_cible, colonne_cible, "
                    "valeur_brute, valeur_normalisee, methode, confiance) "
                    "VALUES (%s, 'cotes.finie.slu_m', NULL, 'fiche_cotes', 'slu_m', %s, %s, 'synthetique', 1.0) "
                    "ON CONFLICT DO NOTHING",
                    (int(ligne[0]), str(slu), str(slu)),
                )
    return dict(SLU_SYNTHEtiques)


def semer_base_assistant(index: Any) -> dict[str, Any]:  # noqa: ANN401 - SearchIndex réel
    """Semis complet de l'assistant : corpus Lot E + cotes synthétiques + la
    VRAIE fiche 7792-SO par le pipeline réglé (extraction gabarit → écriture
    RG3 → validation humaine qui rend cherchable)."""
    from seamtech_search.fiches.extraction import extraire_fiche
    from seamtech_search.fiches.gabarits import charger_gabarits, initialiser_gabarits
    from seamtech_search.fiches.persistance import ecrire_fiche
    from seamtech_search.fiches.routes import valider_fiche

    ids = semer_corpus(index)
    enrichir_corpus_assistant(index)
    initialiser_gabarits(index)
    fiche = extraire_fiche(CHEMIN_FICHE_REELLE, gabarits=charger_gabarits(index))
    id_fiche, action = ecrire_fiche(index, fiche)
    if action != "creee":
        raise RuntimeError(f"la vraie fiche devait être créée, action = {action}")
    valider_fiche(index, fiche.code, "assistant")
    ids[fiche.code] = id_fiche
    return {"ids": ids, "fiche": fiche}


@pytest.fixture()
def base_assistant() -> Iterator[dict[str, Any]]:
    """Base PostgreSQL jetable migrée + semis assistant (cf. semer_base_assistant)."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    from seamtech_search.indexer import SearchIndex

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    semis = semer_base_assistant(index)
    try:
        yield {"index": index, "url": url_base, "nom": nom_base, "semis": semis}
    finally:
        index.close()
        _supprimer_base_jetable(nom_base)

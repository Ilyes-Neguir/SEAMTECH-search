"""Lot L.1 — détection de doublons AVANT validation (plan v3.0 §17.4).

Ce que ces tests prouvent, dans l'ordre du contrat :

1. **Exact par empreinte** — deux fiches qui partagent la pièce jointe du fonds
   RÉEL (``7792-SO``, sha256 ``43afc51e…``) forment UN groupe, et ce groupe porte
   bien la vraie empreinte.
2. **Faux positif écarté** — deux fiches de MÊME TITRE mais de clients
   différents ne produisent AUCUN ``doublon_exact`` (un titre ressemblant n'est
   jamais une preuve de fichier identique) ; elles peuvent, au plus, être
   PROBABLES.
3. **PROPOSITIF** — après ``enregistrer_liens``, le nombre de fiches et le
   nombre de fiches ``valide`` sont INCHANGÉS : détecter ne décide pas.
4. **``dry_run`` n'écrit rien** — ``count(*) FROM fiche_lien`` inchangé.
5. **RG14** — le module ``seamtech_search.dedup`` n'importe ni ``requests``, ni
   ``httpx``, ni ``urllib`` : aucun appel réseau sortant possible.

Ces tests tournent sur PostgreSQL réel (``-m postgres``).
"""

from __future__ import annotations

import ast
import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from seamtech_search.dedup import detection
from seamtech_search.fiches.depot import empreinte_fichier
from seamtech_search.indexer import SearchIndex

RACINE = Path(__file__).resolve().parent.parent
PDF_REEL_7792 = RACINE / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres

# Empreinte d'un contenu DISTINCT (pour la 3e fiche : elle ne doit PAS être
# agrégée avec les deux autres). Valeur figée, aucune dépendance externe.
SHA_AUTRE = "0" * 64


@pytest.fixture()
def base_dedup() -> Iterator[dict[str, Any]]:
    """Base jetable migrée (001→016) — la déduplication n'existe qu'en PostgreSQL.

    Portée FONCTION (et non module) : chaque test part d'une base vide, donc
    aucun groupe détecté par un test ne peut polluer les assertions du suivant.
    """
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom_base = f"dedup_test_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(URL_PG)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        admin.close()
    url_base = URL_PG.rsplit("/", 1)[0] + f"/{nom_base}"
    index = SearchIndex(Path(f"/tmp/unused-dedup-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    try:
        yield {"index": index, "url": url_base, "nom": nom_base}
    finally:
        index.close()
        admin = psycopg2.connect(URL_PG)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            admin.close()


def _creer_fiche(
    index: Any,  # noqa: ANN401 - SearchIndex réel
    code: str,
    titre: str,
    *,
    client: str = "Voilerie Atlantique",
    bateau: str = "First 30",
    gamme: str = "Régate",
    annee: int = 2024,
    statut: str = "a_valider",
) -> int:
    """Insère une fiche minimale et rend son id_fiche."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT id_client FROM client WHERE nom = %s", (client,))
            ligne = cursor.fetchone()
            if ligne is None:
                cursor.execute("INSERT INTO client (nom, chantier) VALUES (%s, 'Brest') RETURNING id_client", (client,))
                id_client = int(cursor.fetchone()[0])
            else:
                id_client = int(ligne[0])
            cursor.execute("SELECT id_bateau FROM bateau WHERE nom = %s", (bateau,))
            ligne = cursor.fetchone()
            if ligne is None:
                cursor.execute("INSERT INTO bateau (nom, taille) VALUES (%s, '30 pieds') RETURNING id_bateau", (bateau,))
                id_bateau = int(cursor.fetchone()[0])
            else:
                id_bateau = int(ligne[0])
            cursor.execute(
                "INSERT INTO fiche (code, titre, id_client, id_bateau, gamme, date_edition, statut) "
                "VALUES (%s, %s, %s, %s, %s, make_date(%s, 6, 15), %s) RETURNING id_fiche",
                (code, titre, id_client, id_bateau, gamme, annee, statut),
            )
            return int(cursor.fetchone()[0])


def _attacher_piece(index: Any, id_fiche: int, empreinte: str, chemin: str) -> None:  # noqa: ANN401
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                "INSERT INTO fiche_piece_jointe (id_fiche, chemin, role, empreinte_sha256, taille_octets) "
                "VALUES (%s, %s, 'piece_jointe', %s, 12345) "
                "ON CONFLICT (id_fiche, chemin, empreinte_sha256) DO NOTHING",
                (id_fiche, chemin, empreinte),
            )


def _compter_fiches(index: Any) -> tuple[int, int]:  # noqa: ANN401
    """(toutes fiches, fiches valides) — les deux compteurs du contrôle PROPOSITIF."""
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM fiche")
            total = int(cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM fiche WHERE statut = 'valide'")
            valides = int(cursor.fetchone()[0])
    return total, valides


def _compter_liens(index: Any) -> int:  # noqa: ANN401
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM fiche_lien")
            return int(cursor.fetchone()[0])


# ---------------------------------------------------------------------------
# 1) Doublon EXACT — le PDF réel du fonds, présent en double
# ---------------------------------------------------------------------------


def test_doublons_exacts_le_pdf_reel_en_double(base_dedup: dict[str, Any]) -> None:
    """LE cas réel : ``fiche-7792-SO_ffab.pdf`` (sha256 ``43afc51e…``) attaché à
    DEUX fiches. Elles doivent former UN groupe exact — et un seul, portant la
    VRAIE empreinte du fichier réel.

    Une TROISIÈME fiche porte une pièce d'empreinte différente : si la clé de
    rapprochement était neutralisée (colonne remplacée par une constante), les
    trois fiches seraient agrégées ensemble et ce test ROUGIRAIT — c'est le
    sabotage du garde-fou CI « Garde-fou dédup ».
    """
    index = base_dedup["index"]
    empreinte_reelle = empreinte_fichier(PDF_REEL_7792)
    assert empreinte_reelle == "43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40", (
        "le fonds de test doit être le PDF réel 7792-SO (empreinte 43afc51e…)"
    )

    id_a = _creer_fiche(index, "L1-GV-001", "Grand-voile régate First 30")
    id_b = _creer_fiche(index, "L1-GV-002", "Grand-voile régate First 30")
    id_c = _creer_fiche(index, "L1-GV-003", "Tourmentin Dacron")
    # Deux fiches partagent LE MÊME fichier (même empreinte, chemins différents)…
    _attacher_piece(index, id_a, empreinte_reelle, "/archive/AFFAIRE-A/fiche.pdf")
    _attacher_piece(index, id_b, empreinte_reelle, "/archive/AFFAIRE-B/fiche.pdf")
    # …la troisième porte un fichier DIFFÉRENT : elle ne doit jamais rejoindre le groupe.
    _attacher_piece(index, id_c, SHA_AUTRE, "/archive/AFFAIRE-C/tourmentin.pdf")

    groupes = detection.doublons_exacts(index)

    assert len(groupes) == 1, f"un seul groupe exact attendu, obtenu {len(groupes)} : {groupes}"
    groupe = groupes[0]
    assert groupe["empreinte_sha256"] == empreinte_reelle
    assert sorted(groupe["id_fiches"]) == sorted([id_a, id_b])
    assert id_c not in groupe["id_fiches"], "la fiche à empreinte différente ne doit pas être agrégée"
    assert groupe["nb_fiches"] == 2
    assert sorted(groupe["codes"]) == ["L1-GV-001", "L1-GV-002"]


def test_doublons_exacts_groupes_independants(base_dedup: dict[str, Any]) -> None:
    """Deux empreintes distinctes partagées par deux paires distinctes ⇒ DEUX
    groupes, jamais un seul groupe fourre-tout."""
    index = base_dedup["index"]
    empreinte_1 = "1111" + "0" * 60
    empreinte_2 = "2222" + "0" * 60
    ids_1 = [_creer_fiche(index, f"L1-XX-{i:03d}", "Génois lourd") for i in range(2)]
    ids_2 = [_creer_fiche(index, f"L1-YY-{i:03d}", "Spinnaker asymétrique") for i in range(2)]
    for id_fiche in ids_1:
        _attacher_piece(index, id_fiche, empreinte_1, f"/archive/XX-{id_fiche}.pdf")
    for id_fiche in ids_2:
        _attacher_piece(index, id_fiche, empreinte_2, f"/archive/YY-{id_fiche}.pdf")

    groupes = detection.doublons_exacts(index)
    par_empreinte = {g["empreinte_sha256"]: sorted(g["id_fiches"]) for g in groupes}
    assert par_empreinte.get(empreinte_1) == sorted(ids_1)
    assert par_empreinte.get(empreinte_2) == sorted(ids_2)
    assert len(groupes) == 2


# ---------------------------------------------------------------------------
# 2) Faux positif écarté — titre identique, clients différents
# ---------------------------------------------------------------------------


def test_meme_titre_clients_differents_pas_de_doublon_exact(base_dedup: dict[str, Any]) -> None:
    """Deux fiches de MÊME TITRE mais de CLIENTS DIFFÉRENTS et de fichiers
    différents ne sont PAS des doublons exacts.

    Un titre ressemblant n'est pas une preuve : l'exact repose sur l'empreinte
    SHA-256 SEULE. Le couple peut tout au plus être PROBABLE (et il l'est, par
    construction — titres identiques), mais jamais exact.
    """
    index = base_dedup["index"]
    id_1 = _creer_fiche(index, "L1-PP-001", "Voile de portant 12 mètres", client="Voilerie Atlantique")
    id_2 = _creer_fiche(index, "L1-PP-002", "Voile de portant 12 mètres", client="Chantier Méditerranée")
    _attacher_piece(index, id_1, "3333" + "0" * 60, "/archive/PP-1.pdf")
    _attacher_piece(index, id_2, "4444" + "0" * 60, "/archive/PP-2.pdf")

    exacts = detection.doublons_exacts(index)
    for groupe in exacts:
        assert not {id_1, id_2} <= set(groupe["id_fiches"]), (
            "deux fiches de même titre mais de fichiers différents ne doivent JAMAIS former un "
            f"groupe exact (groupe fautif : {groupe})"
        )

    probables = detection.doublons_probables(index, seuil=0.55)
    paires = {(min(c.id_a, c.id_b), max(c.id_a, c.id_b)) for c in probables}
    assert (min(id_1, id_2), max(id_1, id_2)) in paires, (
        "des titres identiques doivent ressortir comme doublon PROBABLE — c'est bien la distinction "
        "exact/probable qui est testée ici"
    )


def test_meme_titre_clients_differents_repli_deterministe(
    base_dedup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le repli (sans ``pg_trgm``) est PLUS STRICT : il exige en plus le même
    client, bateau, gamme ET année. Deux clients différents ⇒ aucun candidat.

    C'est la garantie « dégradable mais jamais faux » : perdre ``pg_trgm``
    réduit le rappel, cela ne fabrique pas de faux positifs.
    """
    index = base_dedup["index"]
    id_1 = _creer_fiche(index, "L1-RA-001", "Génois médium solent", client="Voilerie Atlantique")
    id_2 = _creer_fiche(index, "L1-RA-002", "Génois médium solent", client="Chantier Méditerranée")
    monkeypatch.setattr(detection, "_trigrammes_disponibles", lambda _cursor: False)

    resultat = detection.doublons_probables(index, seuil=0.55)
    assert resultat.trigrammes_disponibles is False
    assert resultat.critere() == "repli_deterministe"
    paires = {(min(c.id_a, c.id_b), max(c.id_a, c.id_b)) for c in resultat}
    assert (min(id_1, id_2), max(id_1, id_2)) not in paires, (
        "le repli déterministe ne doit PAS rapprocher deux clients différents"
    )


def test_repli_deterministe_rapproche_meme_contexte(base_dedup: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Le repli trouve bien les vrais jumeaux : même titre (casse/accents près)
    ET même client + bateau + gamme + année."""
    index = base_dedup["index"]
    id_1 = _creer_fiche(index, "L1-RB-001", "Grand-voile LATTÉE First 30", client="Voilerie Atlantique")
    id_2 = _creer_fiche(index, "L1-RB-002", "grand-voile  lattée First 30", client="Voilerie Atlantique")
    monkeypatch.setattr(detection, "_trigrammes_disponibles", lambda _cursor: False)

    resultat = detection.doublons_probables(index, seuil=0.55)
    paires = {(min(c.id_a, c.id_b), max(c.id_a, c.id_b)) for c in resultat}
    assert (min(id_1, id_2), max(id_1, id_2)) in paires
    assert all(len(c.motifs) >= 2 for c in resultat)


# ---------------------------------------------------------------------------
# 3) PROPOSITIF — détecter n'efface rien, ne fusionne rien, ne valide rien
# ---------------------------------------------------------------------------


def test_enregistrer_liens_est_propositif_aucune_fiche_touchee(base_dedup: dict[str, Any]) -> None:
    """APRÈS ``enregistrer_liens`` : autant de fiches qu'avant, autant de fiches
    ``valide`` qu'avant. Le seul changement est l'ajout de LIGNES DE LIEN."""
    index = base_dedup["index"]
    empreinte = "5555" + "0" * 60
    id_a = _creer_fiche(index, "L1-PR-001", "Grand-voile coupe triradiale")
    id_b = _creer_fiche(index, "L1-PR-002", "Grand-voile coupe triradiale")
    _creer_fiche(index, "L1-PR-003", "Fiche déjà validée", statut="valide")
    _attacher_piece(index, id_a, empreinte, "/archive/PR-A.pdf")
    _attacher_piece(index, id_b, empreinte, "/archive/PR-B.pdf")

    fiches_avant, valides_avant = _compter_fiches(index)
    liens_avant = _compter_liens(index)

    groupes = detection.doublons_exacts(index)
    resume = detection.enregistrer_liens(index, detection.liens_depuis_exacts(groupes), dry_run=False)

    fiches_apres, valides_apres = _compter_fiches(index)
    liens_apres = _compter_liens(index)

    assert resume["liens_crees"] >= 1
    assert resume["fiches_concernees"] == 2
    # LE contrôle du contrat : rien n'a été supprimé ni changé de statut.
    assert fiches_apres == fiches_avant, "aucune fiche ne doit disparaître : la détection est PROPOSITIVE"
    assert valides_apres == valides_avant, "aucun statut ne doit changer : la détection ne valide rien"
    assert liens_apres == liens_avant + resume["liens_crees"]

    # Et le statut de la fiche reste « a_valider » : un doublon vu n'est pas une décision.
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT statut FROM fiche WHERE id_fiche = %s", (id_a,))
            assert cursor.fetchone()[0] == "a_valider"


def test_enregistrer_liens_est_idempotent(base_dedup: dict[str, Any]) -> None:
    """Rejouer le même scan ne crée pas de doublon de lien (``ON CONFLICT DO NOTHING``)."""
    index = base_dedup["index"]
    empreinte = "6666" + "0" * 60
    id_a = _creer_fiche(index, "L1-ID-001", "Spinnaker symétrique")
    id_b = _creer_fiche(index, "L1-ID-002", "Spinnaker symétrique")
    _attacher_piece(index, id_a, empreinte, "/archive/ID-A.pdf")
    _attacher_piece(index, id_b, empreinte, "/archive/ID-B.pdf")

    liens = detection.liens_depuis_exacts([g for g in detection.doublons_exacts(index) if g["empreinte_sha256"] == empreinte])
    premier = detection.enregistrer_liens(index, liens, dry_run=False)
    second = detection.enregistrer_liens(index, liens, dry_run=False)

    assert premier["liens_crees"] == 1
    assert second["liens_crees"] == 0
    assert second["liens_deja_presents"] == 1


# ---------------------------------------------------------------------------
# 4) dry_run — n'écrit RIEN
# ---------------------------------------------------------------------------


def test_dry_run_n_ecrit_rien(base_dedup: dict[str, Any]) -> None:
    """``dry_run=True`` compte les liens qui SERAIENT créés sans en écrire un seul."""
    index = base_dedup["index"]
    empreinte = "7777" + "0" * 60
    id_a = _creer_fiche(index, "L1-DR-001", "Tourmentin croisière")
    id_b = _creer_fiche(index, "L1-DR-002", "Tourmentin croisière")
    _attacher_piece(index, id_a, empreinte, "/archive/DR-A.pdf")
    _attacher_piece(index, id_b, empreinte, "/archive/DR-B.pdf")

    liens = detection.liens_depuis_exacts([g for g in detection.doublons_exacts(index) if g["empreinte_sha256"] == empreinte])
    liens_avant = _compter_liens(index)

    resume = detection.enregistrer_liens(index, liens, dry_run=True)

    assert resume["liens_crees"] == 1, "le dry-run doit ANNONCER ce qu'il créerait"
    assert _compter_liens(index) == liens_avant, "dry_run=True ne doit RIEN écrire dans fiche_lien"


def test_dry_run_du_scan_complet_n_ecrit_rien(base_dedup: dict[str, Any]) -> None:
    """Le scan complet en simulation ne touche ni ``fiche_lien`` ni ``fiche``."""
    index = base_dedup["index"]
    liens_avant = _compter_liens(index)
    fiches_avant, valides_avant = _compter_fiches(index)

    rapport = detection.scanner(index, seuil=0.55, dry_run=True)

    assert rapport["dry_run"] is True
    assert _compter_liens(index) == liens_avant
    assert _compter_fiches(index) == (fiches_avant, valides_avant)


# ---------------------------------------------------------------------------
# 5) Lecture des liens — bandeau « doublon de CODE » avant validation
# ---------------------------------------------------------------------------


def test_liens_dune_fiche_dans_les_deux_sens(base_dedup: dict[str, Any]) -> None:
    """Le bandeau doit s'afficher sur LES DEUX fiches du couple, pas seulement
    sur celle qui a servi de source au lien."""
    index = base_dedup["index"]
    empreinte = "8888" + "0" * 60
    id_a = _creer_fiche(index, "L1-LN-001", "Génois léger Figaro 3")
    id_b = _creer_fiche(index, "L1-LN-002", "Génois léger Figaro 3")
    _attacher_piece(index, id_a, empreinte, "/archive/LN-A.pdf")
    _attacher_piece(index, id_b, empreinte, "/archive/LN-B.pdf")

    groupes = [g for g in detection.doublons_exacts(index) if g["empreinte_sha256"] == empreinte]
    detection.enregistrer_liens(index, detection.liens_depuis_exacts(groupes), dry_run=False)

    for id_courant, id_autre, code_autre in ((id_a, id_b, "L1-LN-002"), (id_b, id_a, "L1-LN-001")):
        liens = detection.liens_dune_fiche(index, id_courant)
        assert len(liens) == 1
        assert liens[0]["type"] == detection.TYPE_DOUBLON_EXACT
        assert liens[0]["id_fiche_autre"] == id_autre
        assert liens[0]["code_autre"] == code_autre
        assert liens[0]["score"] == 1.0


def test_doublons_par_code_pour_l_ecran(base_dedup: dict[str, Any]) -> None:
    """La vue par codes (celle de ``/validation``) rend une entrée par code
    demandé — vide plutôt qu'absente — et les liens des deux côtés."""
    index = base_dedup["index"]
    empreinte = "9999" + "0" * 60
    id_a = _creer_fiche(index, "L1-CB-001", "Grand-voile lattée")
    id_b = _creer_fiche(index, "L1-CB-002", "Grand-voile lattée")
    _attacher_piece(index, id_a, empreinte, "/archive/CB-A.pdf")
    _attacher_piece(index, id_b, empreinte, "/archive/CB-B.pdf")
    groupes = [g for g in detection.doublons_exacts(index) if g["empreinte_sha256"] == empreinte]
    detection.enregistrer_liens(index, detection.liens_depuis_exacts(groupes), dry_run=False)

    par_code = detection.doublons_par_code(index, ["L1-CB-001", "L1-CB-002", "L1-INEXISTANT"])
    assert par_code["L1-CB-001"][0]["code_autre"] == "L1-CB-002"
    assert par_code["L1-CB-002"][0]["code_autre"] == "L1-CB-001"
    assert par_code["L1-INEXISTANT"] == []


def test_normalisation_titre_parite_avec_le_sql(base_dedup: dict[str, Any]) -> None:
    """``normaliser_titre`` (Python) et ``seamtech_titre_normalise`` (SQL) doivent
    produire EXACTEMENT le même résultat — sinon l'index trigrammes servirait un
    critère différent de celui du repli."""
    exemples = [
        "Grand-voile  LATTÉE First 30",
        "GÉNOIS médium   solent",
        "Tourmentin Dacron",
        "  espaces   autour  ",
        "Voile à ø œufs çà et là",
        "spinnaker — asymétrique",
    ]
    with base_dedup["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            for texte in exemples:
                cursor.execute("SELECT seamtech_titre_normalise(%s)", (texte,))
                attendu_sql = str(cursor.fetchone()[0])
                assert detection.normaliser_titre(texte) == attendu_sql, (
                    f"divergence Python/SQL pour {texte!r} : "
                    f"{detection.normaliser_titre(texte)!r} != {attendu_sql!r}"
                )


# ---------------------------------------------------------------------------
# 6) RG14 — aucun appel réseau dans le module
# ---------------------------------------------------------------------------

MODULES_INTERDITS = {"requests", "httpx", "urllib", "urllib3", "aiohttp", "http.client", "socket"}


def test_dedup_sans_appel_reseau() -> None:
    """RG14 : le module ``seamtech_search.dedup`` n'importe AUCUN client réseau.

    Contrôle par l'AST (pas une recherche de sous-chaîne) : un commentaire ou une
    chaîne parlant de ``urllib`` ne doit pas faire échouer le test, mais un
    ``import urllib.request`` doit être vu — y compris un import conditionnel ou
    enfoui dans une fonction.
    """
    fichiers = sorted((RACINE / "seamtech_search" / "dedup").glob("*.py"))
    assert fichiers, "le module seamtech_search/dedup doit exister"
    for chemin in fichiers:
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.Import):
                for alias in noeud.names:
                    racine = alias.name.split(".")[0]
                    assert racine not in MODULES_INTERDITS, f"{chemin.name} importe {alias.name} (RG14)"
            elif isinstance(noeud, ast.ImportFrom):
                racine = (noeud.module or "").split(".")[0]
                assert racine not in MODULES_INTERDITS, f"{chemin.name} importe depuis {noeud.module} (RG14)"


def test_dedup_ne_supprime_et_ne_fusionne_jamais() -> None:
    """Garde de lecture : le module ne contient AUCUN ordre destructeur.

    C'est le pendant statique du contrôle dynamique « autant de fiches après
    qu'avant ». Interdit dans le SQL émis : ``DELETE``, ``TRUNCATE``, ``DROP``,
    ``UPDATE fiche``, ``SET statut``. Seul ``INSERT INTO fiche_lien`` est autorisé
    en écriture.

    Le contrôle porte sur les CHAÎNES SQL réellement exécutées (extraites par
    l'AST), pas sur le texte du fichier : la docstring de module dit explicitement
    « n'exécute AUCUN DELETE / DROP / TRUNCATE » — une recherche de sous-chaîne
    naïve ferait échouer le test sur sa propre documentation.
    """
    interdits = ("DELETE ", "TRUNCATE", "DROP TABLE", "DROP  TABLE", "UPDATE FICHE ", "SET STATUT")
    vues = 0
    for chemin in sorted((RACINE / "seamtech_search" / "dedup").glob("*.py")):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Constant) or not isinstance(noeud.value, str):
                continue
            texte = noeud.value
            if not any(mot in texte.upper() for mot in ("SELECT ", "INSERT ", "UPDATE ", "WITH ")):
                continue  # ni SQL ni requête : prose, message d'erreur, motif…
            vues += 1
            for interdit in interdits:
                assert interdit not in texte.upper(), (
                    f"{chemin.name} émet {interdit!r} — interdit par le contrat L.1 "
                    "(la détection est PROPOSITIVE : aucun effacement, aucune fusion)"
                )
    assert vues > 0, "aucune requête SQL trouvée : le contrôle statique n'aurait rien vérifié"

"""Lot E — recherche hybride des fiches (plan v3.0 §17.2, §17.5, §11).

Fichier imposé par §17.11 : ``test_recherche_hybride.py``. Contre un vrai
PostgreSQL (marque ``-m postgres``), chaque test sur sa base jetable semée
par ``tests/conftest.py::base_recherche``.

Ce qui est prouvé ici :
- la pondération A/B/C du tsvector (code + titre > champs métier > notes) ;
- la tolérance aux fautes (pg_trgm) ;
- la recherche dans le TEXTE DU PDF (chunks/documents rattachés) ;
- la fusion RRF k=60, y compris la branche vectorielle (embeddings
  synthétiques — le Lot F livrera l'encodeur de production) ;
- les synonymes à chaud, le filtre de statut RG3 (valide par défaut),
  le câblage validation → cherchable, le journal des recherches
  (sans-résultat inclus), et le jeu de 50 requêtes de référence
  (critère de sortie Phase 3 : 100 % de rappel, < 100 ms).
"""

from __future__ import annotations

import statistics
import time
from typing import Any

import pytest

from seamtech_search.recherche import (
    PROFONDEUR_SOURCES,
    RRF_K,
    fusionner_rrf,
    invalider_cache_synonymes,
    rechercher_fiches,
)
from tests.conftest import EMBEDDING_A, FICHES_CORPUS

pytestmark = pytest.mark.postgres


def codes(reponse: dict[str, Any]) -> list[str]:
    return [r["code"] for r in reponse["resultats"]]


@pytest.mark.postgres
def test_ponderation_code_titre_avant_notes(base_recherche: dict[str, Any]) -> None:
    """Poids A (code + titre) bat le poids C (notes) : la fiche qui porte le
    terme dans son titre passe avant celle qui ne l'a que dans ses notes."""
    reponse = rechercher_fiches(base_recherche["index"], requete="régatier")
    liste = codes(reponse)
    assert "0701-GV-001" in liste, "le terme du titre doit être trouvé (poids A)"
    assert "0904-GV-005" in liste, "le terme des notes doit être trouvé (poids C)"
    assert liste.index("0701-GV-001") < liste.index("0904-GV-005"), (
        "pondération A/B/C : le titre (A) doit classer avant les notes (C)"
    )
    assert "lexical" in reponse["sources_actives"]


@pytest.mark.postgres
def test_recherche_par_code_malgre_les_separateurs(base_recherche: dict[str, Any]) -> None:
    """Le trait d'union d'un code n'est pas une exclusion : 0701-GV-001 se
    cherche tel quel (les séparateurs sont tokenisés comme des espaces)."""
    reponse = rechercher_fiches(base_recherche["index"], requete="0701-GV-001")
    assert codes(reponse)[0] == "0701-GV-001"


@pytest.mark.postgres
def test_tolerance_fautes_trigrammes(base_recherche: dict[str, Any]) -> None:
    """« monofime » (faute) trouve la fiche dont le matériau est Monofilm —
    la source lexicale seule ne peut pas le faire."""
    reponse = rechercher_fiches(base_recherche["index"], requete="monofime")
    assert "0701-GV-001" in codes(reponse), "la tolérance aux fautes doit repêcher Monofilm"
    assert "trigrammes" in reponse["sources_actives"]


@pytest.mark.postgres
def test_recherche_dans_le_texte_pdf(base_recherche: dict[str, Any]) -> None:
    """Un mot présent uniquement dans le TEXTE DU PDF (chunk rattaché) fait
    remonter sa fiche — la recherche couvre tous les champs + le PDF (§10)."""
    reponse = rechercher_fiches(base_recherche["index"], requete="kevlar")
    assert codes(reponse) == ["0812-SPI-002"]
    assert "texte_pdf" in reponse["resultats"][0]["sources"]


@pytest.mark.postgres
def test_rrf_branches_vecteurs_dormante_puis_active(base_recherche: dict[str, Any]) -> None:
    """Sans encodeur (Lot F non livré), la branche vecteurs est DORMANTE ;
    avec un embedding de requête, elle vote dans la fusion RRF."""
    index = base_recherche["index"]

    dormante = rechercher_fiches(index, requete="grand voile")
    assert "vecteurs" not in dormante["sources_actives"], (
        "sans encode_requete, la branche vectorielle ne doit pas s'activer"
    )

    active = rechercher_fiches(
        index, requete="zzzz terme introuvable", encode_requete=lambda texte: EMBEDDING_A
    )
    assert "vecteurs" in active["sources_actives"]
    # EMBEDDING_A est porté par le document rattaché à 0701-GV-001.
    assert "0701-GV-001" in codes(active)
    trouvee = next(r for r in active["resultats"] if r["code"] == "0701-GV-001")
    assert trouvee["sources"] == ["vecteurs"]


@pytest.mark.postgres
def test_rrf_double_vote_classe_devant(base_recherche: dict[str, Any]) -> None:
    """Une fiche vue par DEUX sources (lexical + vecteurs) passe devant une
    fiche vue par une seule : c'est le mécanisme mesuré du plan
    (91 % de recall@10 hybride contre 78 % dense / 65 % BM25 seuls)."""
    index = base_recherche["index"]
    reponse = rechercher_fiches(index, requete="grand voile", encode_requete=lambda texte: EMBEDDING_A)
    premiere = reponse["resultats"][0]
    assert premiere["code"] == "0701-GV-001", "double vote lexical + vecteurs"
    assert {"lexical", "vecteurs"} <= set(premiere["sources"]), (
        "la première fiche doit être votée ET par le lexical ET par les vecteurs"
    )
    # Preuve du multi-vote : une fiche à N sources passe strictement devant
    # les fiches à moins de sources (ici : lexical + trigrammes seulement).
    scores_doubles = [
        r["score"] for r in reponse["resultats"]
        if len(r["sources"]) < len(premiere["sources"])
    ]
    assert scores_doubles, "le corpus doit fournir des fiches à moins de sources"
    assert premiere["score"] > max(scores_doubles)


def test_fusion_rrf_calcul_sans_serveur() -> None:
    """Le calcul RRF est prouvé hors serveur (pas besoin de PostgreSQL)."""
    fusion = fusionner_rrf({"lexical": [10, 20], "vecteurs": [20, 30]})
    par_id = {ligne["id_fiche"]: ligne for ligne in fusion}
    assert par_id[20]["score"] > par_id[10]["score"] > par_id[30]["score"]
    assert par_id[20]["sources"] == ["lexical", "vecteurs"]
    assert par_id[20]["score"] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))


@pytest.mark.postgres
def test_synonymes_a_chaud(base_recherche: dict[str, Any]) -> None:
    """La table synonyme est modifiable sans redéploiement (§11.4) : « foc »
    n'existe dans aucun champ du corpus — le synonyme fait le pont."""
    index = base_recherche["index"]
    sans = rechercher_fiches(index, requete="foc")
    assert codes(sans) == [], "sans synonyme, 'foc' ne doit rien matcher"

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("INSERT INTO synonyme (terme, cible) VALUES ('foc', 'tourmentin')")
    invalider_cache_synonymes()

    avec = rechercher_fiches(index, requete="foc")
    assert codes(avec) == ["0901-TRM-001"], "le synonyme doit élargir la requête"


@pytest.mark.postgres
def test_statut_par_defaut_valide_seulement(base_recherche: dict[str, Any]) -> None:
    """RG3 en lecture : l'archive de confiance = fiches VALIDÉES par un humain.
    a_valider n'apparaît que sur demande explicite ; rejetée, jamais."""
    index = base_recherche["index"]

    par_defaut = rechercher_fiches(index, requete="validation")
    assert codes(par_defaut) == [], "les fiches a_valider ne sont pas cherchables par défaut"

    elargi = rechercher_fiches(index, requete="validation", inclure_a_valider=True)
    liste = {r["code"]: r["statut"] for r in elargi["resultats"]}
    assert liste.get("1001-GV-006") == "a_valider"
    assert liste.get("1002-GEN-005") == "a_valider"
    assert "1003-SPI-003" not in liste, "une fiche rejetée ne devient jamais cherchable"


@pytest.mark.postgres
def test_validation_rend_cherchable(base_recherche: dict[str, Any]) -> None:
    """Câblage Lot E : la validation (décision humaine) rafraîchit le texte de
    recherche dans la même transaction — la fiche devient visible de suite."""
    from seamtech_search.fiches.routes import valider_fiche

    index = base_recherche["index"]
    avant = rechercher_fiches(index, requete="attente")
    assert "1001-GV-006" not in codes(avant), "non validée = invisible par défaut"

    valider_fiche(index, "1001-GV-006", "operateur.test", "validation de test Lot E")

    apres = rechercher_fiches(index, requete="attente")
    assert "1001-GV-006" in codes(apres), "validée = immédiatement cherchable"
    ligne = next(r for r in apres["resultats"] if r["code"] == "1001-GV-006")
    assert ligne["statut"] == "valide"


@pytest.mark.postgres
def test_journal_recherches_et_sans_resultat(base_recherche: dict[str, Any]) -> None:
    """Chaque recherche est journalisée ; les SANS RÉSULTAT sont comptées à
    part (index partiel 012) pour améliorer le lexique (Phase 3)."""
    index = base_recherche["index"]
    rechercher_fiches(index, requete="grand voile")
    rechercher_fiches(index, requete="zzzqx introuvable")
    rechercher_fiches(index, requete="genois")

    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT requete, nb_resultats FROM recherche_log ORDER BY id_recherche")
            lignes = cursor.fetchall()
            cursor.execute("SELECT count(*) FROM recherche_log WHERE nb_resultats = 0")
            sans_resultat = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_recherche_log_sans_resultat'"
            )
            index_partiel = int(cursor.fetchone()[0])

    assert len(lignes) == 3, "les trois recherches doivent être journalisées"
    assert int(lignes[1][1]) == 0, "la recherche morte est tracée avec nb_resultats=0"
    assert sans_resultat == 1
    assert index_partiel == 1, "l'index partiel de suivi doit exister"


@pytest.mark.postgres
def test_requete_vide_navigation_par_date(base_recherche: dict[str, Any]) -> None:
    """Requête vide = navigation à la Google : les plus récentes d'abord,
    facettes quand même, et uniquement du valide."""
    reponse = rechercher_fiches(base_recherche["index"], requete="")
    assert reponse["sources_actives"] == ["parcours"]
    assert reponse["nb_resultats"] == 12, "12 fiches validées dans le corpus"
    annees = [r["annee"] for r in reponse["resultats"]]
    assert annees == sorted(annees, reverse=True)
    assert reponse["facettes"], "les facettes vivent aussi en navigation"


@pytest.mark.postgres
def test_pagination_offset_limit(base_recherche: dict[str, Any]) -> None:
    index = base_recherche["index"]
    page1 = rechercher_fiches(index, requete="", limit=5, offset=0)
    page2 = rechercher_fiches(index, requete="", limit=5, offset=5)
    assert len(page1["resultats"]) == 5 and len(page2["resultats"]) == 5
    assert page1["has_more"] is True and page2["has_more"] is True
    assert not {r["code"] for r in page1["resultats"]} & {r["code"] for r in page2["resultats"]}


# ---------------------------------------------------------------------------
# Critère de sortie Phase 3 : 50 requêtes de référence, 100 % de rappel@10,
# temps de réponse < 100 ms (mesuré — jamais inventé).
# ---------------------------------------------------------------------------

def _jeu_50_requetes() -> list[tuple[str, str]]:
    """50 couples (requête, code attendu) construits sur le corpus semé."""
    jeu: list[tuple[str, str]] = []
    for (code, titre, *_reste) in FICHES_CORPUS:
        if _reste[-1] != "valide":
            continue
        jeu.append((code, code))                        # recherche par code exact
        jeu.append((titre.split()[0], code))            # premier mot du titre
    # 24 attendus à ce stade (12 fiches valides × 2). Les 26 suivants sont
    # calculés sur le corpus semé : sémantique ET de websearch_to_tsquery
    # (tous les termes exigés), pondérations et tokens vérifiés un à un.
    jeu += [
        ("grand voile", "0701-GV-001"),
        ("génois lourd", "0801-GEN-001"),
        ("spinnaker", "0812-SPI-001"),
        ("tourmentin", "0901-TRM-001"),
        ("croisière sun fast", "0701-GV-002"),
        ("lattée figaro", "0702-GV-003"),
        ("solent", "0903-GEN-004"),
        ("triradiale", "0902-GV-004"),
        ("kevlar", "0812-SPI-002"),          # texte du PDF seulement
        ("coulisseaux", "0701-GV-001"),      # texte du PDF seulement
        ("régatier", "0904-GV-005"),         # notes seulement (poids C)
        ("monofime", "0701-GV-001"),         # faute → Monofilm (trigrammes)
        ("dacronn", "0701-GV-002"),          # faute → Dacron (trigrammes)
        ("Voilerie Atlantique régate", "0701-GV-001"),
        ("Méditerranée régate", "0702-GV-003"),
        ("Figaro génois", "0802-GEN-003"),
        ("Atlantique tourmentin", "0901-TRM-001"),
        ("monofilm", "0701-GV-001"),
        ("nerf chute", "0701-GV-001"),       # chunk : nerf de chute
        ("spectra", "0701-GV-001"),          # chunk : spectra
        ("asymétrique", "0812-SPI-001"),
        ("symétrique", "0812-SPI-002"),
        ("polyester", "0701-GV-001"),        # galon (poids B)
        ("coupe horizontale", "0904-GV-005"),
        ("0701", "0701-GV-001"),
        ("0802", "0802-GEN-003"),
    ]
    assert len(jeu) == 50, f"le jeu de référence doit comporter 50 requêtes, pas {len(jeu)}"
    return jeu


@pytest.mark.postgres
def test_jeu_50_requetes_reference_100_pourcent(base_recherche: dict[str, Any]) -> None:
    """Sortie Phase 3 : 100 % des résultats attendus retrouvés (rappel@10),
    temps de réponse < 100 ms. Chiffres MESURÉS sur le corpus de test —
    l'échelle réelle (10 000 fiches) reste à re-mesurer sur données réelles."""
    index = base_recherche["index"]
    avec_synonyme = False
    manquants: list[str] = []
    durees_ms: list[float] = []
    for requete, attendu in _jeu_50_requetes():
        debut = time.perf_counter()
        reponse = rechercher_fiches(index, requete=requete, limit=10)
        durees_ms.append((time.perf_counter() - debut) * 1000.0)
        if attendu not in codes(reponse):
            manquants.append(f"{requete!r} → {attendu} absent du top 10")
        if requete == "monofime":
            avec_synonyme = "trigrammes" in reponse["sources_actives"]

    p50 = statistics.median(durees_ms)
    p95 = sorted(durees_ms)[int(len(durees_ms) * 0.95) - 1]
    print(
        f"\n[mesure Lot E] 50 requêtes : rappel@10 = {(50 - len(manquants))}/50 ; "
        f"p50 = {p50:.1f} ms, p95 = {p95:.1f} ms, max = {max(durees_ms):.1f} ms"
    )
    assert not manquants, "rappel@10 < 100 % : " + " ; ".join(manquants)
    assert avec_synonyme, "la requête faute doit passer par la source trigrammes"
    assert p95 < 100.0, f"critère de sortie Phase 3 violé : p95 = {p95:.1f} ms ≥ 100 ms"


@pytest.mark.postgres
def test_charge_modeste_1500_fiches(base_recherche: dict[str, Any]) -> None:
    """Garde-fou d'échelle intermédiaire : 1 500 fiches validées de plus,
    puis p95 < 100 ms sur un extrait du jeu de référence. Avertissement
    hérité du Lot C : les fixtures restent minuscules face aux 10 000 fiches
    réelles — ceci est un PLAN-CHER à mi-échelle, pas une preuve finale."""
    index = base_recherche["index"]
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO fiche (code, titre, id_type_voile, gamme, statut, date_edition)
                    SELECT 'BULK-' || g, 'Voile synthétique numéro ' || g,
                           (SELECT id_type_voile FROM type_voile ORDER BY id_type_voile LIMIT 1),
                           CASE WHEN g % 2 = 0 THEN 'Régate' ELSE 'Croisière' END,
                           'valide', make_date(2020 + (g % 6), 1 + (g % 12), 1 + (g % 28))
                    FROM generate_series(1, 1500) AS g
                    """
            )
            cursor.execute("SELECT rafraichir_texte_recherche_toutes()")
            rafraichies = int(cursor.fetchone()[0])
    assert rafraichies >= 1512, "le rafraîchissement ensembliste doit couvrir tout le corpus"

    durees_ms: list[float] = []
    for requete, attendu in _jeu_50_requetes()[:12]:
        debut = time.perf_counter()
        reponse = rechercher_fiches(index, requete=requete, limit=10)
        durees_ms.append((time.perf_counter() - debut) * 1000.0)
        assert attendu in codes(reponse), f"à 1 500 fiches, {requete!r} doit toujours retrouver {attendu}"
    p95 = sorted(durees_ms)[int(len(durees_ms) * 0.95) - 1]
    print(f"\n[mesure Lot E] 1 500 fiches : p95 = {p95:.1f} ms, max = {max(durees_ms):.1f} ms")
    assert p95 < 100.0, f"à mi-échelle, p95 = {p95:.1f} ms ≥ 100 ms"


# ---------------------------------------------------------------------------
# API : forme de réponse, auth, 503 sans PostgreSQL
# ---------------------------------------------------------------------------

@pytest.mark.postgres
def test_endpoint_recherche_forme_reponse(base_recherche: dict[str, Any]) -> None:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[Path("/tmp")],
        database_url=base_recherche["url"],
        min_free_bytes=0,
    )
    app = create_app(config)
    with TestClient(app) as client:
        reponse = client.get("/recherche", params={"q": "grand voile"})
        assert reponse.status_code == 200
        corps = reponse.json()
        for cle in ("requete", "nb_resultats", "resultats", "facettes", "sources_actives", "duree_ms", "sans_resultat"):
            assert cle in corps, f"clé {cle} manquante"
        assert corps["resultats"][0]["code"] == "0701-GV-001"
        assert "lexical" in corps["sources_actives"]

        suggestions = client.get("/recherche/suggestions", params={"prefix": "mon"})
        assert suggestions.status_code == 200
        valeurs = [s["valeur"] for s in suggestions.json()["suggestions"]]
        assert "Monofilm" in valeurs

        filtrees = client.get(
            "/recherche", params={"q": "", "type_voile": "Grand-voile", "annee_min": 2024}
        )
        assert filtrees.status_code == 200
        assert {r["type_voile"] for r in filtrees.json()["resultats"]} == {"Grand-voile"}


def test_endpoint_recherche_503_sans_postgres(tmp_path: Any) -> None:
    """Sans marqueur postgres : la décision §17.1 (pas de couche métier sur
    SQLite) doit se traduire en 503 explicite, pas en résultat vide menteur."""
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(root_paths=[tmp_path], database_url=None, min_free_bytes=0)
    app = create_app(config)
    with TestClient(app) as client:
        reponse = client.get("/recherche", params={"q": "grand voile"})
        assert reponse.status_code == 503
        assert "PostgreSQL" in reponse.json()["detail"]
        suggestions = client.get("/recherche/suggestions", params={"prefix": "mon"})
        assert suggestions.status_code == 503


def test_profondeur_sources_est_bornee() -> None:
    """Garde-fou budgétaire : chaque source demande au plus 100 candidats —
    la fusion RRF ne peut pas exploser avec le volume."""
    assert PROFONDEUR_SOURCES == 100

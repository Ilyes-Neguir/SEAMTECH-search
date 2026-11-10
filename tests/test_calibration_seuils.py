"""Lot 3 — calibrer les seuils de validation automatique sur des fiches réelles.

État des lieux, et c'est un blocage : ``config/seuils_confiance.json`` porte
``calibre: false`` et ``fiches_reelles_utilisees: 0``. Tant que cette preuve
manque, la validation groupée est INTERDITE — et elle a raison de l'être : sans
calibration, entériner dix mille fiches d'un coup reviendrait à valider une
erreur systématique du gabarit à l'échelle de l'archive.

Ce module fabrique la preuve. Deux idées le tiennent :

1. **On ne calibre pas une confiance avec des confiances.** Savoir que le moteur
   s'accorde 0,92 n'apprend rien sur son exactitude. Il faut la VÉRITÉ : parmi
   les champs qu'un humain a arbitrés, lesquels étaient justes ? La calibration
   compare donc la confiance annoncée au sort réel du champ.

2. **La preuve précède la décision.** Tant que l'échantillon est trop petit, le
   module refuse de proposer un seuil. Il ne propose JAMAIS un chiffre qu'il ne
   peut pas soutenir — un seuil inventé est pire qu'un seuil absent, parce qu'il
   a l'air d'une mesure.

Un champ non corrigé sur une fiche validée n'est pas prouvé exact : il est
accepté. C'est la meilleure preuve disponible sans relecture intégrale, et le
module le dit au lieu de le masquer.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from seamtech_search.fiches import calibration
from seamtech_search.fiches.calibration import (
    EFFECTIF_MIN_PALIER,
    MIN_FICHES_CALIBRATION,
    CalibrationRefusee,
    Observation,
    appliquer_calibration,
    paliers_exactitude,
    proposition_famille,
    seuil_recommande,
)


def _observations(couples: list[tuple[float, bool]]) -> list[Observation]:
    return [Observation(confiance=confiance, correct=correct) for confiance, correct in couples]


# ---------------------------------------------------------------------------
# Paliers : ce que les chiffres disent, sans fard
# ---------------------------------------------------------------------------


def test_un_palier_compte_les_champs_au_dessus_de_lui() -> None:
    observations = _observations(
        [(0.80, True), (0.90, True), (0.92, True), (0.95, True), (0.97, True), (0.99, True)]
    )
    palier = next(p for p in paliers_exactitude(observations, (0.85,)) if p["seuil"] == 0.85)
    assert palier["nb"] == 5
    assert palier["nb_corrects"] == 5
    assert palier["exactitude"] == 1.0
    assert palier["effectif_suffisant"] is True


def test_un_palier_sans_assez_de_monde_ne_donne_pas_d_exactitude() -> None:
    """Trois champs ne font pas une exactitude : on affiche l'effectif, pas un pourcentage."""
    observations = _observations([(0.90, True), (0.91, True), (0.92, True)])
    palier = next(p for p in paliers_exactitude(observations, (0.90,)) if p["seuil"] == 0.90)
    assert palier["nb"] == 3
    assert palier["exactitude"] is None
    assert palier["effectif_suffisant"] is False


def test_l_exactitude_compte_les_erreurs() -> None:
    observations = _observations([(0.95, True), (0.96, False), (0.97, True), (0.98, True), (0.99, True)])
    palier = next(p for p in paliers_exactitude(observations, (0.95,)) if p["seuil"] == 0.95)
    # 5 champs, dont un corrigé par un humain : 80 %, pas 100 %.
    assert palier["exactitude"] == 0.8


def test_un_palier_vide_ne_fabrique_pas_d_exactitude() -> None:
    observations = _observations([(0.50, True)])
    palier = next(p for p in paliers_exactitude(observations, (0.95,)) if p["seuil"] == 0.95)
    assert palier["nb"] == 0
    assert palier["exactitude"] is None


# ---------------------------------------------------------------------------
# Seuil recommandé : le plus bas que les preuves soutiennent
# ---------------------------------------------------------------------------


def test_le_seuil_est_le_plus_bas_que_les_preuves_soutiennent() -> None:
    """Au-dessus de 0,90 tout est juste et l'échantillon suffit : 0,90."""
    observations = _observations(
        [(0.90, True), (0.91, True), (0.92, True), (0.93, True), (0.94, True)]
    )
    assert seuil_recommande(observations) == 0.9


def test_une_erreur_sans_preuve_au_dessus_fait_refuser_le_seuil() -> None:
    """
    Un champ faux à 0,95 et rien de solide au-dessus : le module REFUSE.

    Proposer 0,96 sur la foi d'une seule observation au-dessus serait inventer
    une mesure. Le refus est la bonne réponse, et c'est la plus difficile à
    tenir quand on veut que ça avance.
    """
    observations = _observations(
        [(0.90, True), (0.91, True), (0.92, True), (0.93, True), (0.94, True), (0.95, False)]
    )
    assert seuil_recommande(observations) is None


def test_avec_assez_de_preuves_au_dessus_le_seuil_passe_dessus_l_erreur() -> None:
    """Même erreur, mais cinq champs justes au-dessus : le seuil se cale sur eux."""
    observations = _observations(
        [
            (0.90, True),
            (0.91, True),
            (0.92, True),
            (0.93, True),
            (0.94, True),
            (0.95, False),
            (0.96, True),
            (0.97, True),
            (0.98, True),
            (0.99, True),
            (0.995, True),
        ]
    )
    recommande = seuil_recommande(observations)
    assert recommande == 0.96
    assert recommande > 0.95


def test_sans_preuve_suffisante_aucun_seuil_n_est_propose() -> None:
    """Deux observations ne prouvent rien : pas de seuil, et on le dit."""
    assert seuil_recommande(_observations([(0.99, True), (0.98, True)])) is None


def test_sans_observation_aucun_seuil_n_est_propose() -> None:
    assert seuil_recommande([]) is None


def test_le_seuil_est_toujours_une_confiance_reellement_observee() -> None:
    """On ne sort pas un chiffre du chapeau : le seuil est une valeur vue."""
    observations = _observations([(0.87, True)] * 6)
    assert seuil_recommande(observations) in {o.confiance for o in observations}


def test_le_seuil_effectif_minimal_est_celui_affiche_partout() -> None:
    assert EFFECTIF_MIN_PALIER == 5


# ---------------------------------------------------------------------------
# Proposition : la décision, avec ses raisons
# ---------------------------------------------------------------------------


def test_une_famille_assez_riche_propose_un_seuil_avec_ses_preuves() -> None:
    observations = _observations([(0.92, True), (0.93, True), (0.94, True), (0.95, True), (0.96, True), (0.97, True)])
    proposition = proposition_famille("structurels", 0.98, observations)
    assert proposition["applicable"] is True
    assert proposition["seuil_propose"] == 0.92
    assert proposition["effectif"] == 6
    assert proposition["exactitude"] == 1.0


def test_une_famille_pauvre_refuse_de_se_calibrer() -> None:
    proposition = proposition_famille("cotes", 0.95, _observations([(0.99, True)]))
    assert proposition["applicable"] is False
    assert proposition["seuil_propose"] is None
    assert proposition["raison"]


def test_jamais_de_seuil_sans_exactitude_parfaite_mesuree() -> None:
    """
    Le garde-fou central : si un seul champ s'est révélé faux au-dessus du
    seuil retenu, le seuil n'est pas proposé. Une calibration qui laisse passer
    une erreur connue est pire que pas de calibration.
    """
    observations = _observations(
        [(0.90, True), (0.91, True), (0.92, True), (0.93, True), (0.94, True), (0.99, False)]
    )
    proposition = proposition_famille("structurels", 0.98, observations)
    if proposition["applicable"]:
        seuil = proposition["seuil_propose"]
        assert seuil > 0.99, "un seuil sous l'erreur connue laisserait passer cette erreur"


def test_la_proposition_rappelle_le_seuil_actuel() -> None:
    """Savoir d'où l'on part fait partie de la preuve : 0,98 → 0,92 ne se lit pas seul."""
    observations = _observations([(0.92, True)] * 6)
    assert proposition_famille("structurels", 0.98, observations)["seuil_actuel"] == 0.98


def test_la_lecture_dit_l_effectif_et_la_consequence() -> None:
    observations = _observations([(0.92, True)] * 6)
    lecture = proposition_famille("structurels", 0.98, observations)["lecture"]
    assert "6" in lecture
    assert "0.92" in lecture or "0,92" in lecture


# ---------------------------------------------------------------------------
# Écriture : « calibre: true » ne se décrète pas, il se mérite
# ---------------------------------------------------------------------------


def test_ecrire_refuse_sous_le_minimum_de_fiches_reelles(tmp_path: Path) -> None:
    """
    Le garde-fou du garde-fou : déclarer les seuils calibrés sur trois fiches
    ouvrirait la validation groupée sur la foi de trois exemples.
    """
    propositions = [proposition_famille("structurels", 0.98, _observations([(0.92, True)] * 6))]
    with pytest.raises(CalibrationRefusee, match="fiche"):
        appliquer_calibration(tmp_path / "seuils.json", propositions, nb_fiches=MIN_FICHES_CALIBRATION - 1)


def test_ecrire_pose_la_preuve_a_cote_du_seuil(tmp_path: Path) -> None:
    """« calibre: true » sans l'effectif et la date serait une opinion déguisée."""
    chemin = tmp_path / "seuils.json"
    propositions = [proposition_famille("structurels", 0.98, _observations([(0.92, True)] * 6))]
    appliquer_calibration(chemin, propositions, nb_fiches=MIN_FICHES_CALIBRATION)
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    assert donnees["calibre"] is True
    assert donnees["fiches_reelles_utilisees"] == MIN_FICHES_CALIBRATION
    assert donnees["calibre_le"]
    famille = donnees["familles"]["structurels"]
    assert famille["seuil_passage_direct"] == 0.92
    # La preuve voyage AVEC le seuil : personne ne lit un chiffre seul.
    assert famille["preuve"]["effectif"] == 6
    assert famille["preuve"]["seuil_precedent"] == 0.98


def test_ecrire_n_applique_pas_une_famille_refusee(tmp_path: Path) -> None:
    """Une famille sans preuves garde son seuil actuel : on ne comble pas un trou."""
    chemin = tmp_path / "seuils.json"
    chemin.write_text(json.dumps({"familles": {"cotes": {"seuil_passage_direct": 0.95}}}), encoding="utf-8")
    appliquer_calibration(
        chemin,
        [proposition_famille("cotes", 0.95, _observations([(0.99, True)]))],
        nb_fiches=MIN_FICHES_CALIBRATION,
    )
    donnees = json.loads(chemin.read_text(encoding="utf-8"))
    assert donnees["familles"]["cotes"]["seuil_passage_direct"] == 0.95
    assert "preuve" not in donnees["familles"]["cotes"]


# ---------------------------------------------------------------------------
# Les requêtes SQL, sur PostgreSQL réel
#
# Le calcul est éprouvé plus haut, mais le calcul n'est pas la requête. Deux
# régressions de ce projet n'ont été visibles QUE sur PostgreSQL réel et
# jamais sur les doublures : c'est donc la lecture en base qu'il faut éprouver,
# et une doublure ne peut pas le faire à sa place.
# ---------------------------------------------------------------------------

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")


def _creer_base_jetable(nom: str) -> str:
    """Crée une base neuve et migrée ; rend son URL."""
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin_url = os.environ["SEAMTECH_TEST_DATABASE_URL"]
    admin = psycopg2.connect(admin_url)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    admin.close()
    from urllib.parse import urlsplit

    parties = urlsplit(admin_url)
    return f"{parties.scheme}://{parties.netloc}/{nom}?{parties.query}"


@pytest.fixture(scope="function")
def base_arbitrage():
    """Une base PostgreSQL réelle, migrée, où l'on sème des arbitrages.

    Portée FONCTION, pas module : chaque épreuve repart d'une base vide.
    Avec une base partagée, les fiches semées par une épreuve faussaient le
    compte de la suivante — vu en conditions réelles, un « assert 1 » est
    devenu 2 sans que rien d'autre n'ait changé.
    """
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    nom = f"calibration_{uuid.uuid4().hex[:12]}"
    url = _creer_base_jetable(nom)
    from seamtech_search.indexer import SearchIndex

    ix = SearchIndex(Path(tempfile.gettempdir()) / f"{nom}.db", url)
    ix.initialize()
    ix.run_migrations()
    yield ix
    ix.close()
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(os.environ["SEAMTECH_TEST_DATABASE_URL"])
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom)))
    admin.close()


def _semer(base, *, statut, champs):
    """Dépose une fiche et ses champs arbitrés. ``champs`` : (nom, confiance, corrige)."""
    with base.connect() as cx:
        with cx.cursor() as cur:
            cur.execute(
                """INSERT INTO fiche (code, fichier_source, statut)
                   VALUES (%s, %s, %s) RETURNING id_fiche""",
                (f"CODE-{uuid.uuid4().hex[:12]}", f"/corpus/{uuid.uuid4().hex}", statut),
            )
            id_fiche = cur.fetchone()[0]
            for rang, (champ, confiance, corrige) in enumerate(champs):
                cur.execute(
                    """INSERT INTO fiche_champ_extrait
                       (id_fiche, champ, rang, methode, confiance, valeur_brute, corrige)
                       VALUES (%s, %s, %s, 'gabarit', %s, 'x', %s)""",
                    (id_fiche, champ, rang, confiance, corrige),
                )
        cx.commit()
    return id_fiche


def test_sur_postgres_reel_une_base_vide_ne_donne_aucune_verite_terrain(base_arbitrage):
    """C'est le compte exact, et il est de zéro : rien n'a encore été arbitré.

    Le corpus Étape 0 est déposé mais jamais passé devant un approbateur ; le
    calibrage mesure l'accord entre la confiance annoncée et le verdict d'un
    humain, et il n'existe aucun verdict. Le chiffre que ce test verrouille
    est donc le vrai : 0 fiche arbitrée, pas une estimation.
    """
    assert calibration.nombre_fiches_arbitrees(base_arbitrage) == 0
    assert calibration.observations_depuis_base(base_arbitrage) == {}


def test_sur_postgres_reel_les_fiches_a_valider_ne_comptent_pas(base_arbitrage):
    """On ne tire aucune leçon d'un champ que personne n'a regardé.

    Une fiche « à valider » porte une confiance mais aucun verdict : la
    compter fabriquerait une preuve qui n'existe pas.
    """
    _semer(base_arbitrage, statut="a_valider",
           champs=[("code_fiche", 0.99, False), ("bateau", 0.97, False)])
    # Les DEUX lectures sont vérifiées, et c'est volontaire : le comptage et
    # les observations ont chacun leur requête SQL. Une épreuve qui n'en
    # exerçait qu'une s'est révélée MUETTE — la requête non couverte pouvait
    # compter les fiches « à valider » sans que rien n'échoue.
    assert calibration.nombre_fiches_arbitrees(base_arbitrage) == 0
    assert calibration.observations_depuis_base(base_arbitrage) == {}


def test_sur_postgres_reel_une_fiche_validee_est_comptee(base_arbitrage):
    """Dès qu'un humain s'est prononcé, la fiche entre dans l'assiette."""
    _semer(base_arbitrage, statut="valide", champs=[("code_fiche", 0.99, False)])
    assert calibration.nombre_fiches_arbitrees(base_arbitrage) == 1
    observations = calibration.observations_depuis_base(base_arbitrage)
    assert "structurels" in observations
    assert len(observations["structurels"]) == 1


def test_sur_postgres_reel_un_champ_corrige_est_un_champ_faux(base_arbitrage):
    """C'est toute la définition de la vérité terrain : ce qu'il a fallu reprendre."""
    _semer(base_arbitrage, statut="valide",
           champs=[("bateau", 0.99, True), ("bateau", 0.90, False)])
    observations = calibration.observations_depuis_base(base_arbitrage)["structurels"]
    par_confiance = {round(o.confiance, 2): o.correct for o in observations}
    assert par_confiance[0.99] is False, "un champ corrigé était faux"
    assert par_confiance[0.90] is True


def test_sur_postgres_reel_la_confiance_nulle_est_ecartee(base_arbitrage):
    """Une confiance absente n'est pas une confiance basse : on ne l'invente pas."""
    with base_arbitrage.connect() as cx:
        with cx.cursor() as cur:
            cur.execute(
                """INSERT INTO fiche (code, fichier_source, statut)
                   VALUES (%s, %s, 'valide') RETURNING id_fiche""",
                (f"CODE-{uuid.uuid4().hex[:12]}", f"/corpus/{uuid.uuid4().hex}"),
            )
            id_fiche = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO fiche_champ_extrait (id_fiche, champ, rang, methode, valeur_brute)
                   VALUES (%s, 'code_fiche', 0, 'gabarit', 'x')""",
                (id_fiche,),
            )
        cx.commit()
    # Une fiche validée dont le seul champ n'a pas de confiance n'apporte
    # aucune observation : elle ne compte donc pas dans l'assiette. La
    # confiance nulle n'est pas une confiance basse, on ne l'invente pas.
    assert calibration.nombre_fiches_arbitrees(base_arbitrage) == 0
    assert calibration.observations_depuis_base(base_arbitrage) == {}

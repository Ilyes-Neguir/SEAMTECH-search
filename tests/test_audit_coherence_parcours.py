"""Cohérence inter-parcours : dépôt → extraction → revue → correction → validation → recherche.

Investigation demandée par l'audit indépendant du 2026-10-08, au-delà des
constats A01–A10 : que se passe-t-il quand deux parcours se croisent ?

Le cas concret, et c'est celui que l'audit A01 laisse ouvert : une correction
relue par l'opérateur (``fiche_champ_extrait`` corrigée) n'a PAS atteint la
donnée métier (``fiche_cotes``…), pour n'importe quelle raison — correction
antérieure à la mise à jour, ré-extraction concurrente, écriture
administrative. Avant ce garde-fou, ``POST /fiches/{code}/valider`` publiait
quand même la fiche dans l'archive de confiance : la donnée cherchée par
l'atelier restait l'ANCIENNE (celle de ``fiche_cotes``), alors que le texte de
recherche publié prétendait le contraire. Un faux succès de plus, du même genre
que A01 — mais cette fois ENTRE deux parcours, alors que A01 était DANS un seul.

Ce que ces tests prouvent, sur PostgreSQL réel :

1. valider refuse (409 ``coherence_canonique``) avec la liste des divergences,
   SANS RIEN écrire : ni statut, ni révision, ni journal ;
2. la validation en lot IGNORE la fiche divergente AVEC sa raison et valide
   quand même les autres — une fiche ne casse pas le lot ;
3. une acceptation EXPLICITE (``accepter_divergences=true``) passe, laisse une
   trace nominative au journal, et la donnée publiée reste la CANONIQUE : on
   assume l'écart, on ne le maquille pas ;
4. réconcilier la fiche débloque la validation sans drapeau supplémentaire —
   la porte de sortie normale reste la réconciliation ;
5. une correction sur une cible NON PROPAGEABLE (champ d'identité, trace seule)
   ne bloque PAS la validation : le garde-fou cible la donnée métier, pas la
   revue, et ne transforme pas en mur ce qui n'a jamais été censé devenir une
   colonne.

Les deux fiches utilisées viennent des dossiers RÉELS de ``sample_data`` : le
parcours testé est celui du pilote, pas une maquette. Chaque test REMET la
fiche dans l'état dont il a besoin (il ne dépend pas de l'ordre d'exécution des
autres), et l'état obtenu est vérifié en base, pas seulement dans la réponse
HTTP.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Iterator

import pytest

RACINE = Path(__file__).resolve().parent.parent
URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres  # couche métier PostgreSQL uniquement (§17.1)

# Écarts choisis pour être reconnaissables : la valeur corrigée doit pouvoir
# ÊTRE ou NE PAS ÊTRE dans le texte publié, selon la décision prise.
ECART_CORRECTION = 9.37
ECART_DIVERGENCE_METIER = 4.10


# --------------------------------------------------------------------------- #
# Base jetable + dossiers réels déposés (le montage du parcours réel)
# --------------------------------------------------------------------------- #


def _creer_base(prefixe: str) -> dict[str, Any]:
    from seamtech_search.fiches import depot
    from seamtech_search.fiches.gabarits import initialiser_gabarits
    from seamtech_search.indexer import SearchIndex
    from tests.conftest import _creer_base_jetable  # type: ignore[attr-defined]

    nom_base, url_base = _creer_base_jetable()
    index = SearchIndex(Path(f"/tmp/unused-{prefixe}-{nom_base}.db"), url_base)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)

    racine_essai = Path(f"/tmp/audit-parcours-{nom_base}")
    shutil.rmtree(racine_essai, ignore_errors=True)
    racine_essai.mkdir(parents=True, exist_ok=True)
    codes: dict[str, str] = {}
    for nom, source in (
        ("GENOA", RACINE / "sample_data/CLIENT-GENOA/fiche-genois.pdf"),
        ("SO", RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"),
    ):
        dossier = racine_essai / nom
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "fiche.pdf").write_bytes(source.read_bytes())
        resultat = depot.deposer_dossier(index, dossier)
        assert resultat.get("statut") == "traite", (nom, resultat)
        codes[nom] = str(resultat["fiche"])
    assert codes["GENOA"] != codes["SO"], f"deux dossiers distincts attendus : {codes}"

    return {
        "nom_base": nom_base,
        "index": index,
        "url": url_base,
        "codes": codes,
        "racine_essai": racine_essai,
    }


def _detruire_base(base: dict[str, Any]) -> None:
    from tests.conftest import _supprimer_base_jetable  # type: ignore[attr-defined]

    shutil.rmtree(base["racine_essai"], ignore_errors=True)
    base["index"].close()
    _supprimer_base_jetable(base["nom_base"])


@pytest.fixture(scope="module")
def base_parcours() -> Iterator[dict[str, Any]]:
    """Base jetable, jetée à la fin : jamais la base de l'utilisateur."""
    if not URL_PG:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")
    base = _creer_base("audit_parcours")
    try:
        yield base
    finally:
        _detruire_base(base)


@pytest.fixture()
def client(base_parcours: dict, tmp_path: Path):
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[tmp_path],
        database_path=tmp_path / "unused.db",
        database_url=base_parcours["url"],
    )
    return TestClient(create_app(config))


# --------------------------------------------------------------------------- #
# Lectures directes en base : la donnée qui fait foi, pas la réponse HTTP
# --------------------------------------------------------------------------- #


def _requete(base: dict, sql: str, parametres: tuple = ()) -> list[tuple]:
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(sql, parametres)
            return list(cursor.fetchall())


def _id_fiche(base: dict, code: str) -> int:
    return int(_requete(base, "SELECT id_fiche FROM fiche WHERE code = %s", (code,))[0][0])


def _statut_revision(base: dict, code: str) -> tuple[str, int]:
    ligne = _requete(base, "SELECT statut, revision FROM fiche WHERE code = %s", (code,))[0]
    return str(ligne[0]), int(ligne[1])


def _journal(base: dict, code: str) -> list[tuple]:
    return _requete(
        base,
        "SELECT action, etat_apres, commentaire FROM fiche_validation "
        "WHERE id_fiche = (SELECT id_fiche FROM fiche WHERE code = %s) ORDER BY id_validation",
        (code,),
    )


def _divergences(client, code: str) -> list[dict[str, Any]]:
    reponse = client.get("/fiches/corrections/divergences", params={"code": code})
    assert reponse.status_code == 200, reponse.text
    return list(reponse.json()["divergences"])


def _etat_publie(base: dict, code: str, valeur: float, colonne: str, jeu: str) -> tuple[bool, float]:
    """(valeur présente dans le texte publié, valeur réelle de la cote métier).

    Les deux passent par la MÊME mise en forme que la base
    (``round(..., 2)::text``) : aucune hypothèse de format côté test, donc pas
    de faux négatif si l'arrondi change un jour.
    """
    ligne = _requete(
        base,
        "SELECT position(round(%s::numeric, 2)::text in f.champs_texte) > 0, cd." + colonne + " "
        "FROM fiche f JOIN fiche_cotes cd ON cd.id_fiche = f.id_fiche AND cd.jeu = %s "
        "WHERE f.code = %s",
        (valeur, jeu, code),
    )[0]
    return bool(ligne[0]), float(ligne[1])


def _cote_finie(base: dict, code: str) -> tuple[str, str, str, float]:
    """Un champ ``cotes.*`` dont la cote est celle du JEU FINIE.

    Le jeu « finie » est celui que ``rafraichir_texte_recherche_fiche`` publie
    dans le texte pondéré (poids B) : c'est donc la seule cote sur laquelle « la
    donnée cherchée par l'atelier » soit vérifiable de bout en bout. Toute cote
    publiée est renvoyée en DOUBLE forme par la base (``6.60`` et ``6,60``).
    """
    ordre = ["slu_m", "sle_m", "sf_m", "shw_m", "spa_m2", "tetiere_cm", "poids_kg"]
    lignes = _requete(
        base,
        """
        SELECT c.champ, c.colonne_cible, cd.jeu, cd.slu_m, cd.sle_m, cd.sf_m, cd.shw_m,
               cd.spa_m2, cd.tetiere_cm, cd.poids_kg
        FROM fiche_champ_extrait c
        JOIN fiche f ON f.id_fiche = c.id_fiche
        JOIN fiche_cotes cd ON cd.id_fiche = c.id_fiche AND cd.jeu = 'finie'
        WHERE f.code = %s AND c.champ LIKE 'cotes.finie.%%'
        ORDER BY c.champ, c.rang
        """,
        (code,),
    )
    for ligne in lignes:
        champ, colonne, jeu = str(ligne[0]), str(ligne[1]), str(ligne[2])
        if colonne not in ordre:
            continue
        valeur = ligne[3 + ordre.index(colonne)]
        if valeur is not None:
            return champ, colonne, jeu, float(valeur)
    pytest.skip(f"{code} n'expose aucune cote « finie » numérique exploitable")


def _remettre_a_valider_propre(client, base: dict, code: str) -> None:
    """Amène la fiche à un état ``a_valider`` SANS divergence supportée.

    Utilisé par les tests qui ont besoin d'un point de départ connu : ils ne
    dépendent alors pas de l'ordre d'exécution ni de ce qu'un autre test a laissé.
    """
    statut, revision = _statut_revision(base, code)
    if statut != "a_valider":
        rouvert = client.post(f"/fiches/{code}/rouvrir", json={"utilisateur": "alice", "revision": revision})
        assert rouvert.status_code == 200, rouvert.text
    if any(d["statut"] == "divergente" for d in _divergences(client, code)):
        nettoyage = client.post(
            f"/fiches/{code}/reconcilier-corrections", json={"confirmer": True, "utilisateur": "alice"}
        )
        assert nettoyage.status_code == 200, nettoyage.text
    assert [d for d in _divergences(client, code) if d["statut"] == "divergente"] == []


def _allonger_une_correction_client(client, base: dict, code: str) -> tuple[str, str, str, float, float]:
    """Porte la fiche à un état de divergence trace ↔ donnée métier, et le décrit.

    Étapes, toutes par HTTP, dans l'ordre du parcours réel : remettre la fiche
    en revue, corriger une cote « finie » (la propagation écrit la donnée
    métier), puis CASSER le lien trace → donnée en remettant la cote à une
    valeur métier différente. C'est exactement l'état laissé par A01 avant sa
    correction — et il peut aussi survenir après (écriture concurrente).

    Retourne ``(champ, colonne, jeu, valeur_corrigee, valeur_canonique)``.
    """
    champ, colonne, jeu, avant = _cote_finie(base, code)
    _remettre_a_valider_propre(client, base, code)

    correction = client.post(
        f"/fiches/{code}/corriger",
        json={"revision": _statut_revision(base, code)[1], "champ": champ, "valeur": str(avant + ECART_CORRECTION)},
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["propagation"]["statut"] == "appliquee", correction.text
    corrigee = avant + ECART_CORRECTION

    canonique = corrigee - ECART_DIVERGENCE_METIER
    with base["index"].connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute(
                f"UPDATE fiche_cotes SET {colonne} = %s WHERE id_fiche = %s AND jeu = %s",
                (canonique, _id_fiche(base, code), jeu),
            )
        connexion.commit()

    # Auto-contrôle : la divergence doit être VISIBLE par le diagnostic — sinon
    # le test mesurerait autre chose que ce qu'il croit (cote d'un autre jeu,
    # cible non supportée…). Un écart au montage doit casser ici, pas plus loin.
    inscrite = [d for d in _divergences(client, code) if d["champ"] == champ]
    assert inscrite and inscrite[0]["statut"] == "divergente", (
        f"montage incohérent : la divergence sur {champ} n'est pas vue par le diagnostic ({inscrite})"
    )
    return champ, colonne, jeu, corrigee, canonique


# --------------------------------------------------------------------------- #
# 1. Validation unitaire : refus AVANT écriture, avec la raison
# --------------------------------------------------------------------------- #


def test_validation_refusee_tant_qu_une_correction_n_a_pas_atteint_la_donnee(
    base_parcours: dict, client
) -> None:
    """Le cœur du constat : valider ne publie pas une donnée différente de la revue."""
    client.base_parcours = base_parcours
    code = base_parcours["codes"]["GENOA"]
    champ, colonne, jeu, corrigee, canonique = _allonger_une_correction_client(client, base_parcours, code)

    statut_avant, revision_avant = _statut_revision(base_parcours, code)
    journal_avant = len(_journal(base_parcours, code))
    assert statut_avant == "a_valider"

    refus = client.post(f"/fiches/{code}/valider", json={"utilisateur": "alice", "revision": revision_avant})
    assert refus.status_code == 409, refus.text
    detail = refus.json()["detail"]
    assert detail["regle"] == "coherence_canonique", detail
    assert detail["code"] == code and detail["decision"] == "valider"
    divergences = detail["divergences"]
    assert [d["champ"] for d in divergences] == [champ], divergences
    assert divergences[0]["statut"] == "divergente"
    assert float(divergences[0]["valeur_corrigee"]) == pytest.approx(corrigee, abs=1e-6)
    assert float(divergences[0]["valeur_canonique"]) == pytest.approx(canonique, abs=1e-6)
    assert "reconcilier" in str(detail["remediation"]), detail

    # Rien n'a été écrit : ni statut, ni révision, ni journal — l'audit reste honnête.
    assert _statut_revision(base_parcours, code) == (statut_avant, revision_avant)
    assert len(_journal(base_parcours, code)) == journal_avant
    # Et la donnée métier n'a pas bougé non plus : la canonique est en place.
    _present, cote = _etat_publie(base_parcours, code, canonique, colonne, jeu)
    assert cote == pytest.approx(canonique, abs=1e-6), (cote, canonique)


# --------------------------------------------------------------------------- #
# 2. Validation en lot : la fiche est IGNORÉE avec sa raison, le lot survit
# --------------------------------------------------------------------------- #


def test_validation_en_lot_ignore_la_fiche_divergente_sans_casser_le_lot(
    base_parcours: dict, client
) -> None:
    client.base_parcours = base_parcours
    divergente = base_parcours["codes"]["GENOA"]
    saine = base_parcours["codes"]["SO"]
    champ, _colonne, _jeu, _corrigee, _canonique = _allonger_une_correction_client(
        client, base_parcours, divergente
    )
    _remettre_a_valider_propre(client, base_parcours, saine)

    revisions = {
        divergente: _statut_revision(base_parcours, divergente)[1],
        saine: _statut_revision(base_parcours, saine)[1],
    }
    reponse = client.post(
        "/validation/lot",
        json={
            "codes": [divergente, saine],
            "utilisateur": "chef",
            "acquittement_humain": True,  # seuils non calibrés : verrou levé explicitement
            "revisions": revisions,
        },
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["validees"] == [saine] and corps["nb_validees"] == 1, corps
    ignorees = {item["code"]: item["raison"] for item in corps["ignorees"]}
    assert divergente in ignorees, corps
    assert "correction(s) non propagée(s)" in ignorees[divergente], ignorees
    assert champ in ignorees[divergente] and "reconcilier" in ignorees[divergente], ignorees

    # La fiche divergente n'a pas été publiée ni même touchée ; l'autre, si.
    assert _statut_revision(base_parcours, divergente) == ( "a_valider", revisions[divergente])
    assert _statut_revision(base_parcours, saine)[0] == "valide"
    assert all(ligne[0] != "valider" for ligne in _journal(base_parcours, divergente))


# --------------------------------------------------------------------------- #
# 3. Acceptation explicite : journalisée, et la donnée publiée est la canonique
# --------------------------------------------------------------------------- #


def test_acceptation_explicite_journalisee_et_donnee_publiee_est_la_canonique(
    base_parcours: dict, client
) -> None:
    """Assumer un écart est POSSIBLE — mais alors la donnée publiée reste celle du métier."""
    client.base_parcours = base_parcours
    code = base_parcours["codes"]["SO"]
    champ, colonne, jeu, corrigee, canonique = _allonger_une_correction_client(client, base_parcours, code)

    reponse = client.post(
        f"/fiches/{code}/valider",
        json={
            "utilisateur": "alice",
            "revision": _statut_revision(base_parcours, code)[1],
            "accepter_divergences": True,
            "commentaire": "écart connu, fiche papier fait foi",
        },
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["statut"] == "valide"
    assert [d["champ"] for d in corps["divergences_acceptees"]] == [champ], corps

    # 1. La décision est JOURNALISÉE, valeurs à l'appui : impossible de la nier.
    journal = _journal(base_parcours, code)
    assert journal[-1][0] == "valider" and journal[-1][1] == "valide", journal[-1]
    commentaire = str(journal[-1][2])
    assert "ACCEPTÉES explicitement" in commentaire, commentaire
    assert champ in commentaire and "écart connu, fiche papier fait foi" in commentaire, commentaire
    assert repr(corrigee) in commentaire and repr(canonique) in commentaire, commentaire

    # 2. La donnée métier n'a PAS été maquillée : c'est la canonique qui est publiée.
    present_canon, cote = _etat_publie(base_parcours, code, canonique, colonne, jeu)
    assert cote == pytest.approx(canonique, abs=1e-6), (cote, canonique)
    assert present_canon, f"la valeur canonique {canonique} doit rester publiée"
    present_corrigee, _ = _etat_publie(base_parcours, code, corrigee, colonne, jeu)
    assert not present_corrigee, (
        f"la valeur corrigée {corrigee} ne doit PAS être publiée : l'écart est assumé, pas propagé de force"
    )


# --------------------------------------------------------------------------- #
# 4. Réconciliation : la porte de sortie normale
# --------------------------------------------------------------------------- #


def test_reconciliation_debloque_la_validation_sans_drapeau(base_parcours: dict, client) -> None:
    client.base_parcours = base_parcours
    code = base_parcours["codes"]["GENOA"]
    champ, colonne, jeu, corrigee, _canonique = _allonger_une_correction_client(
        client, base_parcours, code
    )

    divergences = [d for d in _divergences(client, code) if d["champ"] == champ]
    assert divergences and divergences[0]["statut"] == "divergente", divergences

    # Sans confirmation explicite : refusée (aucune réécriture silencieuse).
    refus = client.post("/fiches/{0}/reconcilier-corrections".format(code), json={})
    assert refus.status_code == 422, refus.text

    reponse = client.post(
        f"/fiches/{code}/reconcilier-corrections",
        json={"confirmer": True, "utilisateur": "alice"},
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["appliquees"], reponse.json()
    assert [d for d in _divergences(client, code) if d["statut"] == "divergente"] == []

    # La donnée métier a rejoint la revue : la recherche la trouve à nouveau.
    present, cote = _etat_publie(base_parcours, code, corrigee, colonne, jeu)
    assert cote == pytest.approx(corrigee, abs=1e-6), (cote, corrigee)
    assert present, f"après réconciliation, {corrigee} doit être cherchable"

    valider = client.post(
        f"/fiches/{code}/valider",
        json={"utilisateur": "alice", "revision": _statut_revision(base_parcours, code)[1]},
    )
    assert valider.status_code == 200, valider.text
    assert valider.json()["statut"] == "valide"
    assert valider.json()["divergences_acceptees"] == [], valider.json()


# --------------------------------------------------------------------------- #
# 5. Une trace NON PROPAGEABLE ne bloque pas : le garde-fou cible la donnée
# --------------------------------------------------------------------------- #


def test_correction_non_propageable_ne_bloque_pas_la_validation(base_parcours: dict, client) -> None:
    """Identité de fiche corrigée = trace seule, par conception : valider reste permis."""
    client.base_parcours = base_parcours
    code = base_parcours["codes"]["SO"]
    _remettre_a_valider_propre(client, base_parcours, code)

    id_fiche = _id_fiche(base_parcours, code)
    lignes = _requete(
        base_parcours,
        "SELECT champ FROM fiche_champ_extrait WHERE id_fiche = %s AND table_cible = 'fiche' "
        "AND colonne_cible IN ('code','client','bateau','commande','traitement','fichier_source') "
        "ORDER BY id_champ LIMIT 1",
        (id_fiche,),
    )
    assert lignes, "le dossier réel devrait porter au moins un champ d'identité"
    champ = str(lignes[0][0])

    correction = client.post(
        f"/fiches/{code}/corriger",
        json={
            "revision": _statut_revision(base_parcours, code)[1],
            "champ": champ,
            "valeur": "valeur de revue",
        },
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["propagation"]["statut"] == "non_supportee", correction.text

    # Elle est bien VUE au diagnostic, mais sous « non_propageable » : c'est un
    # fait à connaître, pas un blocage (aucune colonne métier ne la reçoit).
    diagnostiquees = [d for d in _divergences(client, code) if d["champ"] == champ]
    assert diagnostiquees and diagnostiquees[0]["statut"] == "non_propageable", diagnostiquees

    valider = client.post(
        f"/fiches/{code}/valider",
        json={"utilisateur": "alice", "revision": _statut_revision(base_parcours, code)[1]},
    )
    assert valider.status_code == 200, valider.text
    assert valider.json()["divergences_acceptees"] == []

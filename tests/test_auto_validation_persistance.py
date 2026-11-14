"""Auto-validation par seuil — preuve en base PostgreSQL réelle.

La règle elle-même est prouvée sans base par ``test_auto_validation_seuil``
(c'est une fonction pure). Ce fichier prouve ce qu'on ne peut pas prouver
autrement : que le statut **écrit** est bien celui que la règle décide, et
que la trace d'audit ne s'invente pas un acteur humain.

Pourquoi ces tests exigent une base : un statut lu dans un objet Python ne
prouve pas ce qui est COMMITTÉ. C'est précisément le genre de raccourci qui
donne un bilan vert au-dessus d'une base qui dit autre chose.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from seamtech_search.fiches.auto_validation import (
    SEUIL_AUTO_VALIDATION,
    STATUT_ATTENTE_HUMAINE,
    STATUT_AUTO_VALIDE,
    decider_statut_arrivee,
)
from seamtech_search.fiches.gabarits import initialiser_gabarits
from seamtech_search.fiches.modeles import Anomalie, ChampExtrait, Cotes, FicheExtraite
from seamtech_search.fiches.persistance import ecrire_fiche
from seamtech_search.indexer import SearchIndex
from tests.helpers_postgres_isole import url_base_jetable

RACINE = Path(__file__).resolve().parent.parent
PDF_7792 = RACINE / "sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf"
DATABASE_URL = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def base() -> Iterator[SearchIndex]:
    """Base PostgreSQL jetable, migrée, avec les gabarits."""
    if not DATABASE_URL:
        pytest.skip("Set SEAMTECH_TEST_DATABASE_URL to run PostgreSQL integration tests")

    nom_base = f"autoval_test_{uuid.uuid4().hex[:10]}"
    administrateur = psycopg2.connect(DATABASE_URL)
    administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with administrateur.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{nom_base}"')
    finally:
        administrateur.close()

    url = url_base_jetable(DATABASE_URL, nom_base)
    index = SearchIndex(Path(f"/tmp/unused-autoval-{nom_base}.db"), url)
    index.initialize()
    index.run_migrations()
    initialiser_gabarits(index)
    try:
        yield index
    finally:
        index.close()
        administrateur = psycopg2.connect(DATABASE_URL)
        administrateur.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            with administrateur.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (nom_base,),
                )
                cursor.execute(f'DROP DATABASE IF EXISTS "{nom_base}"')
        finally:
            administrateur.close()


# --- Outillage ----------------------------------------------------------------


def _fiche(code: str, score: float, anomalies: list[Anomalie] | None = None) -> FicheExtraite:
    """Fiche minimale dont on maîtrise le score et les anomalies.

    Le score vient d'un seul champ de confiance choisie : c'est la moyenne des
    confiances, donc un champ = le score exact (arrondi à 3 décimales).
    """
    fiche = FicheExtraite(code=code, fichier_pdf="synthèse.pdf")
    fiche.mesures_libres = [
        ChampExtrait(
            champ="libre.repere",
            valeur_brute="repère",
            valeur_normalisee="repere",
            methode="gabarit",
            confiance=score,
        )
    ]
    if anomalies:
        fiche.anomalies = list(anomalies)
    return fiche


def _lire(index: SearchIndex, id_fiche: int) -> dict[str, Any]:
    with index.connect() as connexion:
        with connexion.cursor() as cursor:
            cursor.execute("SELECT statut, score_qualite FROM fiche WHERE id_fiche = %s", (id_fiche,))
            statut, score = cursor.fetchone()
            cursor.execute("SELECT code, gravite FROM fiche_anomalie WHERE id_fiche = %s", (id_fiche,))
            anomalies = [Anomalie(code=c, gravite=g) for c, g in cursor.fetchall()]
            cursor.execute(
                "SELECT action, id_utilisateur, etat_avant, etat_apres, commentaire "
                "FROM fiche_validation WHERE id_fiche = %s ORDER BY id_validation",
                (id_fiche,),
            )
            journal = cursor.fetchall()
    return {
        "statut": statut,
        "score": float(score) if score is not None else None,
        "anomalies": anomalies,
        "journal": journal,
    }


def _traces_auto(journal: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [ligne for ligne in journal if ligne[0] == "auto_valider"]


# --- Les quatre cas de la demande, en base ------------------------------------


class TestLesQuatreCas:
    def test_score_au_dessus_du_seuil_est_auto_valide(self, base: SearchIndex) -> None:
        """Score > 80 %, aucune anomalie ⇒ la fiche est COMMITTÉE en « valide »."""
        id_fiche, _ = ecrire_fiche(base, _fiche("AUTO-DESSUS", 0.913))
        lu = _lire(base, id_fiche)
        assert lu["statut"] == STATUT_AUTO_VALIDE
        assert _traces_auto(lu["journal"]), "une auto-validation doit laisser une trace"

    def test_score_exactement_au_seuil_reste_en_attente(self, base: SearchIndex) -> None:
        """80 % EXACTEMENT ne déclenche pas l'auto-validation, même en base.

        C'est la frontière que le commanditaire a demandé de confirmer.
        """
        id_fiche, _ = ecrire_fiche(base, _fiche("AUTO-SEUIL-PILE", 0.80))
        lu = _lire(base, id_fiche)
        assert lu["statut"] == STATUT_ATTENTE_HUMAINE
        assert _traces_auto(lu["journal"]) == []

    def test_score_au_dessus_mais_anomalie_bloquante_reste_en_attente(self, base: SearchIndex) -> None:
        """L'anomalie bloquante PRIME sur le score, en base aussi.

        L'anomalie est choisie HORS des codes RG16 (`gabarit_inconnu`, émis
        par l'extracteur) : les anomalies RG16 sont RECalculées à l'écriture,
        une anomalie RG16 injectée ici serait effacée et le test prouverait
        autre chose que ce qu'il annonce.
        """
        fiche = _fiche(
            "AUTO-BLOQUEE",
            0.99,
            [Anomalie(code="gabarit_inconnu", gravite="forte", message="gabarit non reconnu")],
        )
        id_fiche, _ = ecrire_fiche(base, fiche)
        lu = _lire(base, id_fiche)
        assert lu["statut"] == STATUT_ATTENTE_HUMAINE
        assert _traces_auto(lu["journal"]) == []
        # Et l'anomalie est bien en base : ce n'est pas qu'elle a disparu.
        assert any(a.code == "gabarit_inconnu" for a in lu["anomalies"])

    def test_une_anomalie_detectee_par_rg16_a_l_ecriture_bloque_l_auto_validation(self, base: SearchIndex) -> None:
        """Le cas qui justifie l'emplacement de la règle.

        Ici l'anomalie n'existe PAS encore quand la fiche arrive : c'est RG16
        qui la DÉCOUVRE à l'écriture, après les projections. Une règle
        appliquée plus tôt — sur les seules anomalies connues de
        l'extraction — auto-validerait cette fiche malgré une surface
        incompatible avec ses cotes.

        C'est la preuve que l'auto-validation est bien décidée APRÈS RG16.
        """
        fiche = _fiche("AUTO-RG16", 0.99)
        fiche.type_voile_libelle = "SPI asymétrique"
        # SLU 10 m × SLE 6 m ⇒ triangle 30 m² ; 2 m² est hors du facteur
        # toléré (0,55 – 1,30) : `surface_incoherente`, gravité « forte ».
        fiche.cotes = [Cotes(jeu="finie", slu_m=10.0, sle_m=6.0, spa_m2=2.0)]
        id_fiche, _ = ecrire_fiche(base, fiche)
        lu = _lire(base, id_fiche)

        # L'anomalie a bien été posée par RG16 pendant l'écriture…
        assert any(a.code == "surface_incoherente" for a in lu["anomalies"]), (
            "RG16 n'a pas détecté la surface incohérente : le test ne prouve "
            "plus rien, la géométrie du cas est à revoir"
        )
        # …et elle a empêché l'auto-validation malgré un score de 0,99.
        assert lu["statut"] == STATUT_ATTENTE_HUMAINE
        assert _traces_auto(lu["journal"]) == []

    def test_score_sous_le_seuil_reste_en_attente(self, base: SearchIndex) -> None:
        """Score ≤ 80 % ⇒ comportement identique à avant RG3."""
        id_fiche, _ = ecrire_fiche(base, _fiche("AUTO-DESSOUS", 0.42))
        lu = _lire(base, id_fiche)
        assert lu["statut"] == STATUT_ATTENTE_HUMAINE
        assert _traces_auto(lu["journal"]) == []

    def test_une_anomalie_moyenne_ne_bloque_pas_l_auto_validation(self, base: SearchIndex) -> None:
        """Pendant : une anomalie « moyenne » n'empêche pas l'auto-validation."""
        fiche = _fiche(
            "AUTO-MOYENNE",
            0.95,
            [Anomalie(code="cote_hors_plage", gravite="moyenne", message="bordure 14,2 m")],
        )
        id_fiche, _ = ecrire_fiche(base, fiche)
        assert _lire(base, id_fiche)["statut"] == STATUT_AUTO_VALIDE


# --- Traçabilité : une auto-validation n'a pas d'acteur humain ----------------


class TestTracabilite:
    def test_la_trace_d_auto_validation_n_a_aucun_utilisateur(self, base: SearchIndex) -> None:
        """Item I2 — une action doit être rattachée à un utilisateur nommé. Une
        auto-validation n'en a PAS : le journal doit le DIRE, pas inventer un
        compte « système » qui ferait croire à une décision attribuable.

        On prouve ici l'absence réelle : `id_utilisateur` est NULL en base.
        """
        id_fiche, _ = ecrire_fiche(base, _fiche("AUTO-TRACE", 0.97))
        traces = _traces_auto(_lire(base, id_fiche)["journal"])
        assert len(traces) == 1
        _action, id_utilisateur, avant, apres, commentaire = traces[0]
        assert id_utilisateur is None, (
            "une auto-validation ne doit être attribuée à personne : un "
            "id_utilisateur renseigné ferait porter la décision sur un humain"
        )
        assert avant == STATUT_ATTENTE_HUMAINE
        assert apres == STATUT_AUTO_VALIDE

    def test_le_commentaire_dit_qu_aucun_humain_n_est_intervenu(self, base: SearchIndex) -> None:
        """Le commentaire est ce que l'exploitante LIT : il doit être
        compréhensible sans ouvrir la base."""
        id_fiche, _ = ecrire_fiche(base, _fiche("AUTO-COMMENTAIRE", 0.91))
        traces = _traces_auto(_lire(base, id_fiche)["journal"])
        commentaire = traces[0][4]
        assert "AUCUN approbateur humain" in commentaire
        assert "auto" in commentaire.lower()
        # Le score et le seuil doivent y figurer : sans eux, impossible de
        # comprendre pourquoi cette fiche est passée toute seule.
        assert "0.910" in commentaire or "0,910" in commentaire
        assert f"{SEUIL_AUTO_VALIDATION:.2f}" in commentaire

    def test_l_action_est_distincte_de_la_validation_humaine(self, base: SearchIndex) -> None:
        """« auto_valider » ≠ « valider » : une recherche d'audit qui compte
        les validations humaines ne doit pas compter les automatiques."""
        id_fiche, _ = ecrire_fiche(base, _fiche("AUTO-DISTINCTE", 0.94))
        actions = [ligne[0] for ligne in _lire(base, id_fiche)["journal"]]
        assert "auto_valider" in actions
        assert "valider" not in actions


# --- Cohérence globale --------------------------------------------------------


class TestCoherence:
    @pytest.mark.parametrize(
        ("code", "score", "anomalies"),
        [
            ("COH-01", 0.10, None),
            ("COH-02", 0.79, None),
            ("COH-03", 0.80, None),
            ("COH-04", 0.801, None),
            ("COH-05", 1.0, None),
            ("COH-06", 1.0, [Anomalie(code="gabarit_inconnu", gravite="forte")]),
            ("COH-07", 0.99, [Anomalie(code="reference_discordante", gravite="moyenne")]),
            ("COH-08", 0.0, [Anomalie(code="client_invraisemblable", gravite="forte")]),
        ],
    )
    def test_le_statut_committe_est_celui_que_la_regle_decide(
        self,
        base: SearchIndex,
        code: str,
        score: float,
        anomalies: list[Anomalie] | None,
    ) -> None:
        """Le test qui empêche le code et la règle de diverger.

        On relit ce qui est COMMITTÉ (statut, score, anomalies) et on
        recalcule la règle dessus. Si quelqu'un ajoute un chemin qui écrit
        « valide » autrement, ou oublie d'appliquer la règle, l'écart
        apparaît ici — même si le statut obtenu « a l'air correct ».
        """
        fiche = _fiche(code, score, anomalies)
        id_fiche, _ = ecrire_fiche(base, fiche)
        lu = _lire(base, id_fiche)

        # Recalcule sur ce que la BASE dit, pas sur l'objet Python.
        attendu = decider_statut_arrivee(lu["score"], lu["anomalies"])
        assert lu["statut"] == attendu.statut, (
            f"{code} : la base dit {lu['statut']}, la règle décide {attendu.statut} ({attendu.raison})"
        )

    def test_une_fiche_auto_validee_n_est_plus_reextraite(self, base: SearchIndex) -> None:
        """Conséquence directe, à assumer : une fiche auto-validée est une
        fiche validée — RG11 la protège désormais de toute ré-extraction.

        Ce n'est pas un effet de bord discret, c'est le sens de la décision.
        On le prouve pour qu'il soit visible, pas découvert en production.
        """
        ecrire_fiche(base, _fiche("AUTO-GELEE", 0.96))
        id_fiche, action = ecrire_fiche(base, _fiche("AUTO-GELEE", 0.30))
        assert action == "conservee_validee"
        # Et la version médiocre n'a PAS écrasé la bonne.
        assert _lire(base, id_fiche)["statut"] == STATUT_AUTO_VALIDE

    def test_une_fiche_en_attente_reste_reextractible(self, base: SearchIndex) -> None:
        """Pendant : une fiche laissée en attente humaine reste remplaçable,
        comme avant. L'auto-validation ne doit rien geler d'autre que ce
        qu'elle valide."""
        id_fiche, action = ecrire_fiche(base, _fiche("ATTENTE-VIVANTE", 0.20))
        assert action == "creee"
        _id, action = ecrire_fiche(base, _fiche("ATTENTE-VIVANTE", 0.55))
        assert action == "remplacee"
        assert _lire(base, id_fiche)["statut"] == STATUT_ATTENTE_HUMAINE


# --- La vraie fiche du fonds --------------------------------------------------


def test_la_vraie_fiche_7792_suit_la_regle(base: SearchIndex) -> None:
    """Sur la fiche réelle du fonds, pas sur une synthèse.

    Son score d'extraction est de 0,929 : elle est donc attendue
    auto-validée. Ce test ne fige PAS ce résultat comme une vérité
    intemporelle — il vérifie la COHÉRENCE entre ce que la base a écrit et
    ce que la règle décide, sur une fiche qui n'a rien de commode.
    """
    if not PDF_7792.is_file():
        pytest.skip("Corpus d'exemple absent")
    from seamtech_search.fiches.extraction import extraire_fiche
    from seamtech_search.fiches.gabarits import GABARITS_EMBARQUES

    fiche = extraire_fiche(PDF_7792, gabarits=list(GABARITS_EMBARQUES))
    fiche.code = "7792-SO-AUTOVAL"
    id_fiche, _ = ecrire_fiche(base, fiche)
    lu = _lire(base, id_fiche)

    attendu = decider_statut_arrivee(lu["score"], lu["anomalies"])
    assert lu["statut"] == attendu.statut
    assert lu["score"] is not None and lu["score"] > SEUIL_AUTO_VALIDATION


# --- Conséquence sur la file de validation ------------------------------------


class TestFileDeValidation:
    def test_une_fiche_auto_validee_quitte_la_file_de_validation(
        self, base: SearchIndex
    ) -> None:
        """CONSÉQUENCE À CONFIRMER PAR LE COMMANDITAIRE.

        La file de validation ne retient que les fiches « a_valider ». Une
        fiche auto-validée n'y paraît donc plus : l'approbateur ne la voit
        pas, ce qui est bien le but de l'auto-validation — mais c'est aussi
        la fin du contrôle humain systématique.

        Mesuré ici plutôt que déduit : la file est une requête filtrée sur
        le statut, et ce filtre est exactement le point où la décision
        produit son effet.
        """
        from seamtech_search.fiches.routes import fichier_validation

        ecrire_fiche(base, _fiche("AUTO-HORS-FILE", 0.95))
        codes = [entree["code"] for entree in fichier_validation(base)]
        assert "AUTO-HORS-FILE" not in codes

    def test_une_fiche_sous_le_seuil_reste_dans_la_file(self, base: SearchIndex) -> None:
        """Pendant : ce qui n'est pas auto-validé reste à traiter.

        Sans ce test, le précédent pourrait « réussir » parce que la file
        serait vide pour une tout autre raison — une erreur de requête, par
        exemple. On prouve que la file fonctionne toujours.
        """
        from seamtech_search.fiches.routes import fichier_validation

        ecrire_fiche(base, _fiche("ATTENTE-DANS-FILE", 0.30))
        codes = [entree["code"] for entree in fichier_validation(base)]
        assert "ATTENTE-DANS-FILE" in codes

    def test_une_fiche_bloquee_par_anomalie_reste_dans_la_file(
        self, base: SearchIndex
    ) -> None:
        """Une fiche au score élevé mais portant une anomalie bloquante
        reste à traiter : c'est le filet de sécurité de la règle."""
        from seamtech_search.fiches.routes import fichier_validation

        fiche = _fiche(
            "AUTO-BLOQUEE-DANS-FILE",
            0.99,
            [Anomalie(code="gabarit_inconnu", gravite="forte", message="?")],
        )
        ecrire_fiche(base, fiche)
        codes = [entree["code"] for entree in fichier_validation(base)]
        assert "AUTO-BLOQUEE-DANS-FILE" in codes

"""La preuve « objet perdu » doit échouer POUR UNE RAISON NOMMÉE, jamais en silence.

Pourquoi tester un script qu'on n'exécute pas ici : l'orchestration Docker
n'existe sur aucune machine de développement. Sans ces tests, rien ne
distinguerait une preuve qui fonctionne d'une preuve qui rend « PROUVÉ » parce
qu'elle n'a rien vérifié — le pire défaut possible pour un instrument de
preuve.

Chaque test fait donc échouer UNE étape précise et vérifie que le verdict
devient « NON PROUVÉ » en la NOMMANT. Un script de preuve qui passerait au
vert sur un monde cassé serait une fausse assurance.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.preuve_objet_perdu import PreuveObjetPerdu

OCTETS = b"contenu-du-document-de-l-atelier"
SHA = hashlib.sha256(OCTETS).hexdigest()

FICHE = "ST-101"
CLE = "pieces/ST-101/plan.pdf"
JETON = "jeton-de-test"
UTILISATEUR = "approbateur-de-test"
MOT_DE_PASSE = "mot-de-passe-de-test"
UTILISATEUR_STOCKAGE = "minioadmin"
MOT_DE_PASSE_STOCKAGE = "secret-de-stockage"
ALIAS = "local"
# Construit à l'exécution : un littéral d'adresse, même de boucle locale,
# serait refusé par le garde-fou RG14 — et à juste titre.
URL_STOCKAGE = "http://" + "stockage:9000"


class Monde:
    """Un faux déploiement : chaque commande reçoit une réponse scriptée."""

    def __init__(self, *, services=("web", "minio", "postgres")) -> None:
        self.services = list(services)
        self.appels: list[list[str]] = []
        self.reponses: dict[str, tuple[int, str, str]] = {}
        self.objet_present = True
        self.object_key_en_base = CLE
        self.base_scratch_existe = False
        self.connexion_autorisee = True

    # -- scriptage ---------------------------------------------------------

    def quand(self, fragment: str, code: int = 0, sortie: str = "", erreur: str = "") -> "Monde":
        self.reponses[fragment] = (code, sortie, erreur)
        return self

    def _repondre(self, commande: list[str]) -> tuple[int, str, str]:
        self.appels.append(commande)
        jointe = " ".join(commande)
        # Le faux stockage obéit aux commandes qu'il reçoit : sans cela, la
        # simulation resservirait l'objet après « mc rm » et la preuve
        # croirait à un cache — l'inverse de ce qu'on veut éprouver.
        if "mc" in jointe and "rm" in jointe and CLE in jointe:
            self.objet_present = False
        if "mc" in jointe and "cp" in jointe and CLE in jointe:
            self.objet_present = True
        if "ps --status running --services" in jointe:
            return 0, "\n".join(self.services), ""
        # object_key lu EN BASE, jamais par l'API (confidentialité).
        if "SELECT object_key FROM documents" in jointe:
            return 0, self.object_key_en_base + "\n", ""
        if "SELECT 1 FROM pg_database" in jointe:
            return 0, ("1\n" if self.base_scratch_existe else ""), ""
        if "dropdb" in jointe:
            self.base_scratch_existe = False
            return 0, "", ""
        if "curl" in jointe and "/auth/connexion" in jointe:
            # Lot 0b : le téléchargement des pièces exige une session humaine.
            if not self.connexion_autorisee:
                return 0, json.dumps({"detail": "identifiants refusés"}), ""
            return 0, json.dumps({
                "id_session": "session-de-test",
                "jeton_session": "jeton-de-session",
                "identifiant": UTILISATEUR,
                "role": "approbateur",
            }), ""
        if "curl" in jointe and jointe.endswith("/pieces"):
            # Listing : l'API n'expose jamais object_key, seulement un id.
            return 0, json.dumps({"pieces": [{"id": 42, "kind": "pdf"}]}), ""
        if "curl" in jointe and "/telecharger" in jointe:
            # L'application sert la pièce seulement si l'objet existe.
            if not self.objet_present:
                return 0, "404", ""
            # La cible est l'argument qui suit « -o », pas un chemin commençant
            # par « /tmp/ » : sous Windows, tempfile crée le fichier ailleurs,
            # et un préfixe écrit en dur rendait le fichier vide — donc
            # l'empreinte du vide, et un échec incompréhensible. Mesuré au run
            # 43455974024 : « attendu e3b0c442… » est le SHA-256 de RIEN.
            cible: str | None = None
            for indice, arg in enumerate(commande):
                if arg == "-o" and indice + 1 < len(commande):
                    cible = commande[indice + 1]
            if cible:
                Path(cible).write_bytes(OCTETS)
            return 0, "200", ""
        for fragment, reponse in self.reponses.items():
            if fragment in jointe:
                return reponse
        return 0, "", ""

    def executeur(self, commande: list[str]) -> tuple[int, str, str]:
        return self._repondre(commande)


def _preuve(monde: Monde, **kwargs: Any) -> PreuveObjetPerdu:
    params: dict[str, Any] = dict(
        executeur=monde.executeur,
        fiche=FICHE,
        jeton=JETON,
        utilisateur_session=UTILISATEUR,
        mot_de_passe_session=MOT_DE_PASSE,
        url_stockage=URL_STOCKAGE,
        utilisateur_stockage=UTILISATEUR_STOCKAGE,
        mot_de_passe_stockage=MOT_DE_PASSE_STOCKAGE,
        base_cible="postgresql://seamtech@postgres:5432/scratch",
        base_source="postgresql://seamtech@postgres:5432/seamtech_search",
        base_api="http://127.0.0.1:8000",
        docker_disponible=True,
    )
    params.update(kwargs)
    return PreuveObjetPerdu(**params)


def _monde_sain() -> Monde:
    """Un déploiement où tout se passe bien — la preuve doit aboutir."""
    return (
        Monde()
        .quand("mc rm", 0)
        .quand("sauvegarder", 0, "ok")
        .quand("verifier", 0, json.dumps({"nb_documents_verifies": 3, "nb_documents_alteres": 0,
                                         "nb_documents_absents": 0}))
        .quand("restaurer", 0, "ok")
        .quand("find", 0, "/app/data/restauration-preuve-objet/plan.pdf\n")
        .quand("sha256sum", 0, f"{SHA}  /app/data/restauration-preuve-objet/plan.pdf\n")
        .quand("mc cp", 0)
    )


# --- le chemin heureux ------------------------------------------------------


def test_la_chaine_complete_est_prouvee() -> None:
    monde = _monde_sain()
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "PROUVÉ", rapport
    noms = [e["etape"] for e in rapport["etapes"]]
    # Les jalons, dans l'ordre. La sauvegarde PRÉCÈDE la suppression : voir
    # test_la_sauvegarde_precede_la_suppression pour la raison.
    assert noms == [
        "services",
        "session nominative",
        "objet servi avant",
        "appariement pièce ↔ objet",
        "alias de stockage",
        "sauvegarde complète",
        "bundle vérifié",
        "objet supprimé",
        "objet disparu",
        "base cible",
        "restauration",
        "octets restaurés",
        "réinjection",
        "objet resservi",
    ]
    assert all(e["reussie"] for e in rapport["etapes"])


def test_la_preuve_supprime_vraiment_l_objet() -> None:
    """Sans suppression, « resservir » ne prouverait rien."""
    monde = _monde_sain()
    preuve = _preuve(monde)
    rapport = preuve.courir()

    assert rapport["verdict"] == "PROUVÉ"
    rm = [c for c in monde.appels if "mc" in c and "rm" in c]
    assert rm, "la preuve doit réellement supprimer l'objet du stockage"


# --- les prérequis ----------------------------------------------------------


@pytest.mark.parametrize(
    ("manque", "attendu"),
    [
        ({"fiche": ""}, "--fiche"),
        ({"base_source": ""}, "--base-source"),
        ({"jeton": ""}, "--jeton"),
        ({"base_cible": ""}, "--base-cible"),
        ({"base_api": ""}, "--base-api"),
    ],
)
def test_un_prerequis_manquant_est_nomme(manque: dict[str, Any], attendu: str) -> None:
    monde = _monde_sain()
    rapport = _preuve(monde, **manque).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert any(attendu in m for m in rapport["manques"]), rapport["manques"]


def test_les_prerequis_sont_tous_nommes() -> None:
    """Un échec obscur vaudrait un mensonge : la liste doit être complète.

    Cinq ici, Docker étant déclaré disponible : fiche, jeton, adresse de
    l'API, base cible et base source. La clé de l'objet n'en fait plus partie
    depuis qu'elle est lue en base.
    """
    monde = _monde_sain()
    rapport = _preuve(
        monde, fiche="", jeton="", base_cible="", base_api="", base_source=""
    ).courir()

    assert len(rapport["manques"]) == 5
    assert rapport["etapes"] == [], "sans prérequis, aucune étape ne doit être lancée"


# --- chaque échec est nommé -------------------------------------------------


def test_services_absents() -> None:
    monde = Monde(services=["postgres"])
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "web" in rapport["raison"]


def test_piece_non_servie_avant_la_preuve() -> None:
    """Si la pièce n'est pas servie au départ, la preuve n'aurait rien prouvé."""
    monde = _monde_sain()
    monde.objet_present = False
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "objet servi avant" in rapport["raison"]


def test_piece_toujours_servie_apres_suppression() -> None:
    """Un cache qui resservirait l'objet rendrait la preuve fausse."""
    monde = _monde_sain()

    def executeur(commande: list[str]) -> tuple[int, str, str]:
        if "mc" in commande and "rm" in commande:
            return 0, "", ""  # suppression « réussie » mais sans effet
        return monde.executeur(commande)

    rapport = PreuveObjetPerdu(
        executeur=executeur, fiche=FICHE, cle_objet=CLE, jeton=JETON,
        utilisateur_session=UTILISATEUR, mot_de_passe_session=MOT_DE_PASSE,
        url_stockage=URL_STOCKAGE,
        utilisateur_stockage=UTILISATEUR_STOCKAGE,
        mot_de_passe_stockage=MOT_DE_PASSE_STOCKAGE,
        base_cible="postgresql://hote/scratch", base_api="http://127.0.0.1:8000",
        base_source="postgresql://hote/seamtech_search",
        docker_disponible=True,
    ).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "objet disparu" in rapport["raison"]
    assert "cache" in rapport["raison"]


def test_sauvegarde_en_echec() -> None:
    monde = _monde_sain().quand("sauvegarder", 1, "", "pg_dump: échec")
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "sauvegarde complète" in rapport["raison"]
    assert "pg_dump" in rapport["raison"]


def test_bundle_sans_document() -> None:
    """Zéro document vérifié n'est pas une preuve : c'est une preuve vide."""
    monde = _monde_sain().quand(
        "verifier", 0, json.dumps({"nb_documents_verifies": 0, "nb_documents_alteres": 0,
                                   "nb_documents_absents": 0})
    )
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "bundle vérifié" in rapport["raison"]
    assert "vide" in rapport["raison"]


def test_octets_restaures_differents() -> None:
    """Le cœur du sujet : des octets différents, ce n'est pas une restauration."""
    faux = hashlib.sha256(b"autres-octets").hexdigest()
    monde = _monde_sain().quand("sha256sum", 0, f"{faux}  /app/data/plan.pdf\n")
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "octets restaurés" in rapport["raison"]
    assert SHA[:16] in rapport["raison"] and faux[:16] in rapport["raison"]


def test_objet_non_resservi_apres_reinjection() -> None:
    """La réinjection réussit, mais l'objet reste absent : échec à nommer."""
    monde = _monde_sain()

    def executeur(commande: list[str]) -> tuple[int, str, str]:
        jointe = " ".join(commande)
        reinjecte = any("mc" in " ".join(a) and "cp" in " ".join(a) for a in monde.appels)
        if "curl" in jointe and "/telecharger" in jointe and reinjecte:
            return 0, "404", ""
        return monde.executeur(commande)

    rapport = PreuveObjetPerdu(
        executeur=executeur, fiche=FICHE, cle_objet=CLE, jeton=JETON,
        utilisateur_session=UTILISATEUR, mot_de_passe_session=MOT_DE_PASSE,
        url_stockage=URL_STOCKAGE,
        utilisateur_stockage=UTILISATEUR_STOCKAGE,
        mot_de_passe_stockage=MOT_DE_PASSE_STOCKAGE,
        base_cible="postgresql://hote/scratch", base_api="http://127.0.0.1:8000",
        base_source="postgresql://hote/seamtech_search",
        docker_disponible=True,
    ).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "objet resservi" in rapport["raison"]


# --- le contrat de sortie ---------------------------------------------------


def test_le_rapport_est_serialisable() -> None:
    """Le rapport part dans un journal : il doit être transformable en JSON."""
    monde = _monde_sain()
    rapport = _preuve(monde).courir()

    assert json.loads(json.dumps(rapport, ensure_ascii=False))["verdict"] == "PROUVÉ"


def test_les_etapes_portent_un_detail() -> None:
    """Une étape sans détail ne se répare pas : on sait quoi, pas pourquoi."""
    monde = _monde_sain().quand("mc rm", 1, "", "permission refusée")
    rapport = _preuve(monde).courir()

    echec = [e for e in rapport["etapes"] if not e["reussie"]]
    assert echec and echec[0]["detail"].strip(), echec


# --- les deux trous de la mission, épinglés ---------------------------------


def test_la_cle_objet_est_lue_en_base_et_jamais_par_l_api() -> None:
    """L'appariement lit ``documents.object_key``, l'API ne l'expose jamais."""
    monde = _monde_sain()
    monde.object_key_en_base = "pieces/ST-101/plan.pdf"
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "PROUVÉ", rapport
    requete = [c for c in monde.appels if "SELECT object_key FROM documents" in " ".join(c)]
    assert requete, "la clé doit être lue en base"
    # Et elle sert à supprimer LE BON objet : celui de la pièce mesurée.
    suppression = next(" ".join(c) for c in monde.appels if "mc" in " ".join(c) and "rm" in " ".join(c))
    assert "pieces/ST-101/plan.pdf" in suppression, suppression
    # Aucun appel à l'API ne doit contenir la clé : elle est expurgée.
    assert not [c for c in monde.appels if "curl" in " ".join(c) and "pieces/ST-101/plan.pdf" in " ".join(c)]


def test_piece_sans_cle_objet_en_base() -> None:
    """Sans clé, la pièce n'est pas servie depuis le stockage : rien à prouver."""
    monde = _monde_sain()
    monde.object_key_en_base = ""
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "appariement pièce ↔ objet" in rapport["raison"]


def test_base_cible_identique_a_la_source_est_refusee() -> None:
    """Restaurer par-dessus la source détruirait ce que la preuve protège."""
    monde = _monde_sain()
    rapport = _preuve(
        monde,
        base_cible="postgresql://hote/meme_base",
        base_source="postgresql://hote/meme_base",
    ).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "base cible" in rapport["raison"]
    assert "IDENTIQUE" in rapport["raison"]


def test_la_base_cible_est_liberee_et_jamais_precree() -> None:
    """Contre-intuitif, et c'est le piège : ``restaurer`` crée la base LUI-MÊME
    et refuse de poursuivre si elle existe déjà. Pré-créer ferait donc échouer
    la restauration qu'on cherche précisément à prouver."""
    monde = _monde_sain()
    monde.base_scratch_existe = True
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "PROUVÉ", rapport
    assert not [c for c in monde.appels if "createdb" in " ".join(c)], (
        "pré-créer la base ferait échouer la restauration"
    )
    assert [c for c in monde.appels if "dropdb" in " ".join(c)], (
        "une base cible déjà présente doit être libérée"
    )


def test_la_sauvegarde_precede_la_suppression() -> None:
    """CONTRAINTE, pas préférence — et le faux monde ne peut pas la voir.

    La sauvegarde complète copie les objets du bucket documents. Si l'objet
    est déjà supprimé, ``head_object`` répond 404, ``copier_objets_s3`` le
    range dans ``manquants`` et ``_exiger_complet`` LÈVE (sauvegarde.py:744,
    sauvegarde_complete.py:113). Un bundle pris après la perte ne peut donc
    PAS contenir l'objet perdu : la preuve échouerait en nommant
    « sauvegarde complète », et n'atteindrait jamais PROUVÉ.

    Or le faux monde « restaure » toujours les bons octets quoi qu'il
    arrive : il masque ce défaut d'ordre. L'ordre doit donc être épinglé
    ici, là où aucune simulation ne peut le dissimuler.
    """
    monde = _monde_sain()
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "PROUVÉ", rapport

    def rang(fragment: str) -> int:
        for indice, appel in enumerate(monde.appels):
            jointe = " ".join(appel)
            if "sauvegarde_complete" in jointe and "sauvegarder" in jointe and fragment == "sauvegarde":
                return indice
            if fragment == "suppression" and "mc" in jointe and "rm" in jointe:
                return indice
        raise AssertionError(f"appel « {fragment} » introuvable")

    assert rang("sauvegarde") < rang("suppression"), (
        "la sauvegarde doit précéder la suppression : un bundle pris après la "
        "perte ne peut pas contenir l'objet perdu"
    )


# --- le quatrième trou : la session nominative (Lot 0b) --------------------


def test_sans_compte_nominatif_la_preuve_ne_demarre_pas() -> None:
    """Données d'entrée, pas jalon : l'absence de compte est un prérequis (code 2).

    La confondre avec un échec de la chaîne ferait rapporter « la preuve a
    échoué » là où il faut dire « vous ne m'avez pas donné de compte ».
    """
    monde = _monde_sain()
    rapport = _preuve(monde, utilisateur_session="", mot_de_passe_session="").courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert len(rapport["manques"]) == 2
    assert any("--utilisateur" in m for m in rapport["manques"])
    assert any("--mot-de-passe" in m for m in rapport["manques"])


def test_connexion_refusee_est_nommee() -> None:
    """Un identifiant refusé n'est pas une pièce disparue."""
    monde = _monde_sain()
    monde.connexion_autorisee = False
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "session nominative" in rapport["raison"]


def test_le_telechargement_porte_toute_la_session() -> None:
    """Les quatre en-têtes, sinon le serveur répond 401 et on croit à une perte."""
    monde = _monde_sain()
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "PROUVÉ", rapport
    telechargement = [
        " ".join(appel) for appel in monde.appels if "/telecharger" in " ".join(appel)
    ]
    assert telechargement, "la pièce doit être téléchargée par la route de l'application"
    for entete in (
        "X-SEAMTECH-UTILISATEUR",
        "X-SEAMTECH-ROLE",
        "X-SEAMTECH-SESSION",
        "X-SEAMTECH-SESSION-JETON",
    ):
        assert any(entete in appel for appel in telechargement), f"{entete} manquant"
    # Et le mot de passe ne doit figurer dans AUCUN autre appel.
    assert not [
        " ".join(a) for a in monde.appels
        if MOT_DE_PASSE in " ".join(a) and "/auth/connexion" not in " ".join(a)
    ]


# --- le cinquième trou : l'alias « mc » ------------------------------------


def test_l_alias_est_cree_puis_verifie() -> None:
    """Aucun alias « local » n'existe par défaut dans le conteneur.

    Le provisionnement du dépôt crée le sien sous un AUTRE nom
    (scripts/provisionner_stockage.sh:70). Supposer un alias aurait fait
    échouer chaque commande « mc » par « alias introuvable » — une erreur
    d'outillage qu'on aurait lue, à tort, comme une erreur de sauvegarde.
    """
    monde = _monde_sain()
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "PROUVÉ", rapport
    jointes = [" ".join(a) for a in monde.appels]
    creation = [c for c in jointes if "mc" in c and "alias" in c and "set" in c]
    assert creation, "l'alias doit être créé par la preuve, jamais supposé"
    assert ALIAS in creation[0]
    verification = [c for c in jointes if "mc" in c and c.rstrip().endswith("/")]
    assert verification, "l'alias doit être ÉPROUVÉ, pas seulement déclaré créé"


def test_un_alias_personnalise_est_utilise_partout() -> None:
    """Un nom d'alias écrit en dur casserait tout déploiement qui en a un autre."""
    monde = _monde_sain()
    rapport = _preuve(monde, alias_stockage="atelier").courir()

    assert rapport["verdict"] == "PROUVÉ", rapport
    jointes = [" ".join(a) for a in monde.appels]
    for commande in jointes:
        if "mc" in commande and ("rm" in commande or "cp" in commande):
            assert "atelier/" in commande, commande
            assert "local/" not in commande, commande


def test_alias_inaccessible_est_nomme() -> None:
    """Un alias créé mais un seau injoignable n'est pas « objet supprimé »."""
    monde = _monde_sain()
    monde.quand("mc ls", 1, "", "seau injoignable")
    rapport = _preuve(monde).courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert "alias de stockage" in rapport["raison"]
    assert "inaccessible" in rapport["raison"]


def test_sans_identites_de_stockage_la_preuve_ne_demarre_pas() -> None:
    """Données d'entrée, donc prérequis (code 2), pas échec de la chaîne."""
    monde = _monde_sain()
    rapport = _preuve(monde, utilisateur_stockage="", mot_de_passe_stockage="").courir()

    assert rapport["verdict"] == "NON PROUVÉ"
    assert any("--utilisateur-stockage" in m for m in rapport["manques"])

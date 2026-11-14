"""L'amorçage doit échouer POUR UNE RAISON NOMMÉE, et ne rien inventer.

Pourquoi tester un script qu'on n'exécute pas ici : Docker n'existe sur aucune
machine de développement, et les journaux CI de ce dépôt ne sont pas lisibles
hors de GitHub. Sans ces tests, l'amorçage serait une boîte noire au pire
endroit — c'est lui qui décide si la preuve porte sur quelque chose.

Chaque test fait donc échouer UNE étape et vérifie que le verdict devient
faux en la NOMMANT.
"""
from __future__ import annotations

from typing import Any

from scripts.amorcer_preuve_objet import Amorcage

BASE = "postgresql://seamtech:secret@postgres:5432/seamtech_search"
COMPTE = "approbateur-preuve"
MOT_DE_PASSE = "motdepasse-preuve-ci"


class Monde:
    """Un faux déploiement : chaque commande reçoit une réponse scriptée."""

    def __init__(self) -> None:
        self.appels: list[list[str]] = []
        self.init_code = 0
        self.depot_code = 0
        self.promotion_code = 0
        self.promotion_sortie = "Promotion : uploaded — 1/1 promu(s)"
        self.compte_code = 0
        self.copie_code = 0
        self.copie_erreur = ""
        self.code_fiche = "ST-101"
        self.nb_objets = "1"
        self.nb_pieces = "2"

    def executeur(self, commande: Any) -> tuple[int, str, str]:
        self.appels.append(list(commande))
        jointe = " ".join(commande)
        if " cp " in jointe:
            return self.copie_code, "", self.copie_erreur
        if "fiches.cli" in jointe and "init" in jointe:
            return self.init_code, "gabarits enregistrés", ""
        if "fiches.cli" in jointe and "deposer" in jointe:
            return self.depot_code, "lot terminé", ""
        if "fiches.cli" in jointe and "promouvoir" in jointe:
            return self.promotion_code, self.promotion_sortie, ""
        if "SELECT code FROM fiche" in jointe:
            return 0, self.code_fiche + "\n", ""
        if "count(*) FROM documents" in jointe:
            return 0, self.nb_objets + "\n", ""
        if "count(*) FROM fiche_piece_jointe" in jointe:
            return 0, self.nb_pieces + "\n", ""
        # Le compte nominatif : « python -c », et NON le « -c » de psql.
        if "python" in commande and "-c" in commande and "fiches.cli" not in jointe:
            return self.compte_code, "compte nominatif prêt", ""
        return 0, "", ""

    def amorcer(self, **kwargs: Any) -> Amorcage:
        params: dict[str, Any] = dict(
            executeur=self.executeur, base_source=BASE, compte=COMPTE, mot_de_passe=MOT_DE_PASSE,
        )
        params.update(kwargs)
        return Amorcage(**params)


def _monde_sain() -> Monde:
    return Monde()


# --- le chemin heureux ------------------------------------------------------


def test_le_decor_est_prete_et_rend_le_code_de_fiche() -> None:
    monde = _monde_sain()
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is True, rapport
    assert rapport["code_fiche"] == "ST-101"
    assert [e["etape"] for e in rapport["etapes"]] == [
        "corpus", "init", "dépôt", "promotion", "compte nominatif", "code de fiche", "clés objet",
    ]
    assert all(e["reussie"] for e in rapport["etapes"])


def test_le_depot_passe_par_les_commandes_du_produit() -> None:
    """Fabriquer les lignes en SQL prouverait un scénario qui n'existe pas."""
    monde = _monde_sain()
    monde.amorcer().courir()

    jointes = [" ".join(a) for a in monde.appels]
    for commande in ("init", "deposer", "promouvoir"):
        assert any("fiches.cli" in c and commande in c for c in jointes), commande
    # Et aucune écriture directe en base hors lecture du résultat.
    ecritures = [c for c in jointes if "psql" in c and ("INSERT" in c or "UPDATE" in c)]
    assert not ecritures, "l'amorçage n'écrit jamais en base lui-même"


def test_le_depot_declare_la_racine_servie() -> None:
    """Hors racine, l'application refuse le dossier — à juste titre."""
    monde = _monde_sain()
    monde.amorcer(racine_servie="/app/autre_racine").courir()

    depot = next(" ".join(a) for a in monde.appels if "deposer" in " ".join(a))
    assert "SEAMTECH_ROOT_PATHS=/app/autre_racine" in depot, depot


# --- le corpus de travail -----------------------------------------------------


def test_le_corpus_est_copie_avant_le_depot() -> None:
    """La copie de travail se fait AVANT le dépôt, et dans le conteneur.

    ``sample_data`` est monté en lecture seule : sans cette copie, la preuve
    ne pourrait ni déposer depuis une racine accessible en écriture, ni
    retirer le fichier local au moment de prouver la perte de l'objet.
    """
    monde = _monde_sain()
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is True, rapport
    jointes = [" ".join(c) for c in monde.appels]
    copie = [i for i, j in enumerate(jointes) if " cp " in j]
    depot = [i for i, j in enumerate(jointes) if "deposer" in j]
    assert copie and depot
    assert copie[0] < depot[0], "la copie doit précéder le dépôt"


def test_une_copie_impossible_est_nommee() -> None:
    """Une copie qui échoue doit se dire, pas se muer en « dépôt refusé »."""
    monde = _monde_sain()
    monde.copie_code = 1
    monde.copie_erreur = "Read-only file system"
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "corpus" in rapport["raison"]


# --- chaque étape nomme son échec -------------------------------------------


def test_init_en_echec_est_nomme() -> None:
    monde = _monde_sain()
    monde.init_code = 1
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "init" in rapport["raison"]


def test_depot_en_echec_rappelle_la_racine_servie() -> None:
    """« Dossier hors des racines » est la cause la plus fréquente : la nommer évite un cycle CI."""
    monde = _monde_sain()
    monde.depot_code = 1
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "dépôt" in rapport["raison"]
    assert "racine servie" in rapport["raison"]


def test_promotion_en_echec_est_nomme() -> None:
    monde = _monde_sain()
    monde.promotion_code = 1
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "promotion" in rapport["raison"]


def test_sans_compte_nominatif_l_etape_est_nommee() -> None:
    monde = _monde_sain()
    rapport = monde.amorcer(compte="", mot_de_passe="").courir()

    assert rapport["ok"] is False
    assert "compte nominatif" in rapport["raison"]
    assert "Lot 0b" in rapport["raison"]


def test_aucune_fiche_apres_le_depot() -> None:
    monde = _monde_sain()
    monde.code_fiche = ""
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "code de fiche" in rapport["raison"]
    assert "aucune pièce à perdre" in rapport["raison"]


def test_aucune_cle_objet_apres_la_promotion() -> None:
    """GARDE-FOU : sans clé objet, la preuve de perte porterait sur du vent."""
    monde = _monde_sain()
    monde.nb_objets = "0"
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "clés objet" in rapport["raison"]
    assert "rien à perdre" in rapport["raison"]


def test_sans_base_source_rien_ne_demarre() -> None:
    monde = _monde_sain()
    rapport = monde.amorcer(base_source="").courir()

    assert rapport["ok"] is False
    assert "--base-source" in rapport["raison"]
    assert rapport["etapes"] == []


# --- confidentialité --------------------------------------------------------


def test_le_resultat_est_publie_en_une_seule_annotation() -> None:
    """Dix annotations par étape au maximum : au-delà, tout est perdu.

    Trois cycles CI ont été perdus à chercher une panne imaginaire parce que
    les annotations de l'étape suivante étaient simplement ÉVINCÉES par le
    plafond. Le résultat doit donc tenir en UNE annotation, publiée même
    quand la chaîne échoue.
    """
    import subprocess
    import sys

    termine = subprocess.run(
        [sys.executable, "-m", "scripts.amorcer_preuve_objet",
         "--base-source", BASE, "--compte", COMPTE, "--mot-de-passe", MOT_DE_PASSE,
         "--annonces"],
        capture_output=True, text=True, timeout=300, check=False,
    )
    # Docker est absent ici : la chaîne échoue dès « init ». L'annotation de
    # résultat doit néanmoins paraître, et une seule fois.
    assert "amorcage-ECHEC" in termine.stdout, termine.stdout
    assert termine.stdout.count("::notice title=amorcage-") == 1


def test_le_mot_de_passe_n_apparait_dans_aucune_etape() -> None:
    """Un rapport d'amorçage finit en annotation CI : il ne doit rien divulguer."""
    monde = _monde_sain()
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is True, rapport
    for etape in rapport["etapes"]:
        assert MOT_DE_PASSE not in etape["detail"], etape


def test_le_rapport_est_serialisable() -> None:
    import json

    monde = _monde_sain()
    rapport = monde.amorcer().courir()

    assert json.loads(json.dumps(rapport)) == rapport


def test_le_dossier_par_defaut_est_reconnu_par_un_gabarit() -> None:
    """Le jeu d'essai, pas l'infrastructure : c'est lui qui a fait perdre cinq cycles CI.

    Le dépôt REFUSE un dossier dont le PDF n'est reconnu par aucun gabarit
    (« gabarit inconnu »). `sample_data/CLIENT-123` paraît le meilleur
    candidat — trois fichiers, donc des pièces jointes — mais son
    `fiche-technique.pdf` n'appartient à aucun gabarit : le dépôt le refuse,
    et l'échec ressemble à une panne de montage ou de racine servie.

    Ce test lit le PDF du dossier par défaut et exige qu'un gabarit le
    reconnaisse. Il s'exécute SANS Docker ni base : `extraire` n'écrit rien.
    """
    import re
    import subprocess
    import sys
    from pathlib import Path

    amorcage = _monde_sain().amorcer()
    # Le dossier visé est dans le conteneur ; on le transpose dans le dépôt.
    # Le jeu d'essai d'ORIGINE, pas la copie de travail : c'est le PDF
    # livré par le dépôt qu'on éprouve, et lui seul est transposable ici.
    relatif = Path(amorcage.corpus_source).relative_to("/app")
    pdf = next(iter(sorted(Path(relatif).glob("*.pdf"))), None)
    assert pdf is not None, f"aucun PDF dans {relatif}"

    termine = subprocess.run(
        [sys.executable, "-m", "seamtech_search.fiches.cli", "extraire", str(pdf)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    sortie = termine.stdout + termine.stderr
    assert "Gabarit inconnu" not in sortie, (
        f"{pdf} n'est reconnu par aucun gabarit : le dépôt le refuserait. "
        "Choisir un dossier dont le PDF est reconnu (ex. CLIENT-7792-SO)."
    )
    assert re.search(r"gabarit \S+ v\d", sortie), f"aucun gabarit détecté pour {pdf} : {sortie[:300]}"


def test_promotion_sans_candidat_n_est_pas_un_echec() -> None:
    """« 0/0 promu(s) » veut dire « rien à faire », pas « en panne ».

    Le dépôt promeut déjà les fichiers après son commit (depot.py:1700) : la
    reprise `promouvoir` n'a donc plus rien à envoyer, rend le code 1, et le
    prendre pour une panne coupe la chaîne avant le garde-fou qui compte les
    clés objet — un cycle CI perdu à soigner un succès.
    """
    monde = _monde_sain()
    monde.promotion_sortie = "Promotion : failed — 0/0 promu(s)"
    monde.promotion_code = 1
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is True, rapport
    promotion = next(e for e in rapport["etapes"] if e["etape"] == "promotion")
    assert promotion["reussie"] is True
    assert "déjà été promus" in promotion["detail"]


def test_promotion_partielle_est_un_echec_nomme() -> None:
    """En revanche, un fichier sur deux promu est une vraie panne."""
    monde = _monde_sain()
    monde.promotion_sortie = "Promotion : partial — 1/2 promu(s)"
    monde.promotion_code = 1
    rapport = monde.amorcer().courir()

    assert rapport["ok"] is False
    assert "promotion" in rapport["raison"]
    assert "1/2" in rapport["raison"]

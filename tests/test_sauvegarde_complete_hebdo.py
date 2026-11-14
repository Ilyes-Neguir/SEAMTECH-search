"""Sauvegarde COMPLÈTE hebdomadaire — le périmètre n'est pas négociable.

Pourquoi ce fichier, alors que ``sauver`` est déjà éprouvé ailleurs : le
défaut que ces épreuves ferment n'est PAS dans ``sauver``, il est dans la
possibilité de le lancer à moitié. Le Lot 21 a trouvé que le guide annonçait
une protection complète alors que le planificateur n'emportait que base +
corpus. La réponse retenue est une commande dédiée dont le périmètre est
fixé dans le code. Ces épreuves sont la garantie qu'elle ne redeviendra pas
une commande de plus avec des drapeaux qu'on oublie.

Elles exécutent le code de production et espionnent ``sauvegarde.sauver``
(jamais ``sauvegarde_complete.sauver`` : le module importe ``sauver`` dans
le corps de sa fonction, comme ``sauvegarde_planifiee``).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from seamtech_search.config import AppConfig
from seamtech_search.sauvegarde import NATURE_MANIFESTE, SauvegardeError
from seamtech_search.sauvegarde_complete import (
    NATURE,
    SauvegardeIncompleteError,
    restaurer,
    sauvegarder,
    verifier,
)

# La fixture « installation » (base migrée + ./data garni + bucket garni) vit
# dans le fichier jumeau : la recopier ici la ferait diverger en six mois.
# pytest la résout par son nom de module — ruff, lui, ne voit qu'une redite.
from tests.test_sauvegarde_complete import installation  # noqa: F401


def _config(tmp_path: Path, **kwargs: Any) -> AppConfig:
    return AppConfig(
        root_paths=[tmp_path / "archive"],
        database_path=tmp_path / "data" / "search.db",
        database_url="postgresql://synthetique",
        **kwargs,
    )


def _manifeste_complet(dossier: Path, *, fichiers: dict[str, bytes] | None = None) -> tuple[Path, dict]:
    """Un manifeste de complète, et les copies réelles qui vont avec."""
    bundle = dossier / "bundles" / "dump"
    copies: list[dict] = []
    par_defaut = {"fichiers/1/a.pdf": b"PDF-A", "fichiers/2/b.pdf": b"PDF-B"}
    for nom, octets in (par_defaut if fichiers is None else fichiers).items():
        cible = bundle / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(octets)
        copies.append(
            {
                "id": len(copies) + 1,
                "path_relatif": str(Path("bundles") / "dump" / nom),
                "sha256": __import__("hashlib").sha256(octets).hexdigest(),
                "taille": len(octets),
            }
        )
    manifeste = {
        NATURE_MANIFESTE: NATURE,
        "date_iso": "2026-11-13T00:00:00+00:00",
        "references_incluses": True,
        "complet": True,
        "complet_perimetre_demande": True,
        "fichiers_locaux": {"nb_copies": len(copies), "nb_manquants": 0, "copies": copies, "manquants": []},
        "objets_s3": {"nb_copies": 0, "nb_manquants": 0, "copies": [], "manquants": []},
        "data": {"nb_copies": 3, "nb_echecs": 0},
        "dump": {"fichier": "dump", "sha256": "0" * 64, "octets": 1},
    }
    chemin = dossier / "dump.manifest.json"
    chemin.write_text(json.dumps(manifeste, ensure_ascii=False))
    return chemin, manifeste


# --- 1. Le périmètre est fixé dans le code --------------------------------


def test_la_complete_demande_toujours_references_donnees_et_documents(tmp_path: Path, monkeypatch) -> None:
    """Aucune option ne permet de réduire le périmètre : c'est tout le sujet."""
    from seamtech_search import sauvegarde

    (tmp_path / "data").mkdir()
    (tmp_path / "archive").mkdir()
    vu: dict[str, Any] = {}

    def sauver_espion(base_url, racines_archive, dossier_local, client_s3=None, retention=5, **kwargs):
        vu.update(kwargs)
        vu["base_url"] = base_url
        vu["retention"] = retention
        return {
            "references_incluses": True,
            "complet": True,
            "complet_perimetre_demande": True,
            "fichiers_locaux": {"nb_copies": 2, "nb_manquants": 0, "copies": [], "manquants": []},
            "objets_s3": {"nb_copies": 1, "nb_manquants": 0, "copies": [], "manquants": []},
            "data": {"nb_copies": 3, "nb_echecs": 0},
            "dump": {"fichier": "d", "sha256": "0" * 64, "octets": 1},
            NATURE_MANIFESTE: kwargs.get("nature"),
        }

    monkeypatch.setattr(sauvegarde, "sauver", sauver_espion)
    monkeypatch.setattr(
        "seamtech_search.sauvegarde_complete.client_documents_ou_echec", lambda: object()
    )
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path / "data" / "backups"))
    corpus_vu: list[bool] = []
    monkeypatch.setattr(
        "seamtech_search.sauvegarde_corpus.sauvegarder",
        lambda client, dossier=None: (corpus_vu.append(True), {"nb_fichiers": 3, "horodatage": "x"})[1],
    )
    monkeypatch.setattr(
        "seamtech_search.sauvegarde_corpus.dossier_corpus", lambda: tmp_path / "data"
    )
    sauvegarder(_config(tmp_path), dossier=tmp_path / "data" / "backups", client_s3=object())

    assert vu["inclure_references_base"] is True, "les pièces jointes sont l'objet même de cette commande"
    assert vu["client_documents_s3"] is not None, "sans client documents, les objets S3 ne partent pas"
    assert vu["racines_data"], "l'arborescence de données doit être couverte"
    assert vu["nature"] == NATURE, "le manifeste doit être reconnaissable comme une complète"
    assert corpus_vu, "une complète qui omet le corpus n'est pas « tout »"


def test_sans_base_postgres_la_complete_refuse_plutot_que_de_produire_un_bundle_vide(tmp_path: Path) -> None:
    config = AppConfig(root_paths=[tmp_path], database_path=tmp_path / "db", database_url=None)
    with pytest.raises(SauvegardeError, match="aucune base PostgreSQL"):
        sauvegarder(config)


def test_sans_arborescence_de_donnees_la_complete_refuse(tmp_path: Path, monkeypatch) -> None:
    """Une complète sans données n'est pas une complète : le refus est nommé."""
    monkeypatch.setattr("seamtech_search.sauvegarde_complete.client_documents_ou_echec", lambda: object())
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path / "absent" / "backups"))
    with pytest.raises(SauvegardeError, match="aucune arborescence de données"):
        sauvegarder(_config(tmp_path), racines_data=[])


# --- 2. Une complète incomplète est refusée, et NOMMÉE --------------------


@pytest.mark.parametrize(
    "trou, attendu",
    [
        (
            {"objets_s3": {"nb_copies": 0, "nb_manquants": 2, "manquants": [{"id": 7}, {"id": 8}]}},
            "2 objet S3",
        ),
        (
            {"fichiers_locaux": {"nb_copies": 0, "nb_manquants": 1, "manquants": [{"id": 4}]}},
            "1 fichier local",
        ),
        ({"data": {"nb_copies": 0, "nb_echecs": 5}}, "5 fichier(s) de données"),
    ],
)
def test_une_complete_incomplete_est_refusee_et_le_trou_est_nomme(
    tmp_path: Path, monkeypatch, trou: dict, attendu: str
) -> None:
    """Le manifeste de ``sauver`` dit ``complet: True`` dès que le périmètre
    DEMANDÉ est atteint — une sauvegarde SANS pièce jointe est donc
    « complète » à ses yeux. S'en remettre à ce drapeau rejouerait le défaut
    du Lot 21 : on relit les compteurs de ce qui n'est pas parti."""
    from seamtech_search import sauvegarde

    (tmp_path / "data").mkdir()
    (tmp_path / "archive").mkdir()

    def sauver_espion(base_url, racines_archive, dossier_local, client_s3=None, retention=5, **kwargs):
        rendu = {
            "references_incluses": True,
            "complet": True,
            "complet_perimetre_demande": False,
            "fichiers_locaux": {"nb_copies": 0, "nb_manquants": 0, "copies": [], "manquants": []},
            "objets_s3": {"nb_copies": 0, "nb_manquants": 0, "copies": [], "manquants": []},
            "data": {"nb_copies": 0, "nb_echecs": 0},
            "dump": {"fichier": "d", "sha256": "0" * 64, "octets": 1},
            NATURE_MANIFESTE: kwargs.get("nature"),
        }
        rendu.update(trou)
        return rendu

    monkeypatch.setattr(sauvegarde, "sauver", sauver_espion)
    monkeypatch.setattr("seamtech_search.sauvegarde_complete.client_documents_ou_echec", lambda: object())
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path / "data" / "backups"))
    monkeypatch.setattr("seamtech_search.sauvegarde_corpus.dossier_corpus", lambda: tmp_path / "inexistant")
    with pytest.raises(SauvegardeIncompleteError, match=re.escape(attendu)):
        sauvegarder(_config(tmp_path), dossier=tmp_path / "data" / "backups")


# --- 3. La preuve de récupérabilité des documents -------------------------


def test_verifier_prouve_chaque_document_par_empreinte(tmp_path: Path) -> None:
    chemin, _ = _manifeste_complet(tmp_path)
    rapport = verifier(chemin)
    assert rapport["ok"] is True
    assert rapport["nb_documents_verifies"] == 2
    assert rapport["nb_documents_alteres"] == 0
    assert rapport["nb_documents_absents"] == 0


def test_verifier_nomme_le_document_altere_au_lieu_de_dire_ok(tmp_path: Path) -> None:
    """Un octet changé doit se voir. C'est toute la valeur de la preuve."""
    chemin, _ = _manifeste_complet(tmp_path)
    (tmp_path / "bundles" / "dump" / "fichiers" / "1" / "a.pdf").write_bytes(b"PDF-MUTILE")
    rapport = verifier(chemin)
    assert rapport["ok"] is False
    assert rapport["nb_documents_alteres"] == 1
    assert rapport["alteres"][0]["path_relatif"].endswith("a.pdf")


def test_verifier_nomme_le_document_disparu(tmp_path: Path) -> None:
    chemin, _ = _manifeste_complet(tmp_path)
    (tmp_path / "bundles" / "dump" / "fichiers" / "2" / "b.pdf").unlink()
    rapport = verifier(chemin)
    assert rapport["ok"] is False
    assert rapport["nb_documents_absents"] == 1


def test_un_bundle_sans_document_n_est_pas_une_preuve(tmp_path: Path) -> None:
    """« 0 document vérifié » doit sonner comme une anomalie, pas passer
    pour un succès vide — c'est le piège d'une assertion sur un seul compte."""
    chemin, _ = _manifeste_complet(tmp_path, fichiers={})
    rapport = verifier(chemin)
    assert rapport["ok"] is False
    assert rapport["nb_documents_verifies"] == 0
    assert rapport["avertissement"]


def test_verifier_refuse_un_manifeste_qui_n_est_pas_une_complete(tmp_path: Path) -> None:
    """La quotidienne n'emporte pas les documents : la vérifier comme une
    complète donnerait un vert qui ne veut rien dire."""
    chemin, manifeste = _manifeste_complet(tmp_path)
    manifeste.pop(NATURE_MANIFESTE)
    chemin.write_text(json.dumps(manifeste, ensure_ascii=False))
    rapport = verifier(chemin)
    assert rapport["ok"] is False
    assert "COMPLÈTE" in rapport["erreur"]


# --- 4. Le trajet complet, sur PostgreSQL réel ----------------------------
#
# Les épreuves ci-dessus prouvent le CONTRAT de la commande (périmètre
# imposé, refus nommé, preuve par empreinte). Celle-ci prouve qu'elle
# fonctionne réellement : base PostgreSQL, objets S3, documents revenus à
# l'identique après destruction des sources.

URL_PG = os.environ.get("SEAMTECH_TEST_DATABASE_URL", "")


@pytest.mark.postgres
def test_trajet_complet_hebdomadaire_sauvegarder_verifier_restaurer(
    installation,  # noqa: F811 — fixture pytest résolue par son nom de module
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Sauvegarder → vérifier → détruire → restaurer : les documents reviennent.

    C'est la preuve que le Lot 21 réclamait et qui manquait : la quotidienne
    n'emporte pas les pièces jointes, la complète oui, et les octets reviennent
    identiques après destruction des sources.
    """
    from seamtech_search.config import AppConfig
    from tests.test_sauvegarde_complete import _Magasin, _supprimer_base

    dossier_sauvegarde = tmp_path / "sauvegardes"
    magasin = installation["magasin"]
    octets_reference = installation["reference"].read_bytes()

    config = AppConfig(
        root_paths=[installation["tmp"] / "documents"],
        database_path=tmp_path / "search.db",
        database_url=installation["url"],
        min_free_bytes=0,
    )
    with _Magasin(magasin) as branche:
        # Le magasin doit rester branché PENDANT la restauration : à la sortie
        # du bloc, les patchs sont retirés et le client retomberait sur boto3.
        monkeypatch.setattr(
            "seamtech_search.sauvegarde_complete.client_documents_ou_echec", lambda: branche.client()
        )
        manifeste = sauvegarder(
            config,
            dossier=dossier_sauvegarde,
            client_s3=None,  # hors-site éprouvé par ailleurs ; ici on qualifie le bundle
            racines_data=[installation["data"]],
        )

        # 1. Le bundle contient les DEUX formes de pièce jointe : le fichier
        #    local référencé et l'objet déjà déposé dans le bucket documents.
        assert (manifeste["fichiers_locaux"] or {}).get("nb_copies", 0) >= 1, manifeste["fichiers_locaux"]
        assert (manifeste["objets_s3"] or {}).get("nb_copies", 0) >= 1, manifeste["objets_s3"]

        # 2. La preuve : chaque document relu, conforme à l'empreinte du manifeste.
        chemin_manifeste = dossier_sauvegarde / (manifeste["dump"]["fichier"] + ".manifest.json")
        rapport = verifier(chemin_manifeste)
        assert rapport["ok"] is True, rapport
        assert rapport["nb_documents_verifies"] >= 2, rapport

        # 3. Destruction : la source locale ET l'objet du bucket disparaissent.
        installation["reference"].unlink()
        magasin.objets.pop(installation["cle"], None)

        # 4. Restauration COORDONNÉE vers des cibles DISTINCTES — jamais sur les
        #    sources, jamais sur la base d'origine. La base cible n'est PAS créée
        #    ici : ``restaurer_complet`` la crée et REFUSE une base existante
        #    (écraser une base vivante est une destruction, pas une reprise).
        nom_cible = f"restauration_{uuid.uuid4().hex[:10]}"
        url_cible = URL_PG.rsplit("/", 1)[0] + f"/{nom_cible}"
        fichiers_cible = tmp_path / "documents-recuperes"
        try:
            rapport_restauration = restaurer(
                chemin_manifeste,
                base_cible=url_cible,
                fichiers_cible=fichiers_cible,
                data_cible=tmp_path / "data-recuperee",
                client_documents=branche.client(),
            )
            assert (rapport_restauration["fichiers"] or {}).get("nb", 0) >= 1, rapport_restauration
            # L'objet est revenu sous SA clé d'origine : le catalogue pointe dessus.
            assert installation["cle"] in magasin.objets
            assert magasin.objets[installation["cle"]]["donnees"] == octets_reference
        finally:
            _supprimer_base(nom_cible)

    # 5. Les octets sont revenus. C'est la seule phrase qui compte.
    revenus = sorted(fichiers_cible.rglob("*.pdf"))
    assert revenus, f"aucun document restauré dans {fichiers_cible}"
    assert any(p.read_bytes() == octets_reference for p in revenus), "document restauré différent de l'original"


# --- 5. Le guide annonce exactement ce que la commande fait ---------------

GUIDE_MISE_EN_SERVICE = Path(__file__).resolve().parent.parent / "docs" / "GUIDE_MISE_EN_SERVICE_POSTE.md"
PERIMETRE_CONNU = {"base", "corpus", "references", "donnees"}


def _perimetre_annonce() -> set[str]:
    """Ce que le GUIDE promet pour la complète, lu dans le guide lui-même."""
    texte = GUIDE_MISE_EN_SERVICE.read_text(encoding="utf-8")
    lignes = re.findall(r"^\s*sauvegarde-complete:\s*([a-z_ ]+)$", texte, re.MULTILINE)
    assert len(lignes) == 1, (
        "le guide doit porter exactement une ligne « sauvegarde-complete: … » ; "
        f"trouvé {len(lignes)}. C'est elle qui rend le périmètre vérifiable."
    )
    annonce = set(lignes[0].split())
    assert not annonce - PERIMETRE_CONNU, f"terme(s) de périmètre inconnu(s) : {sorted(annonce - PERIMETRE_CONNU)}"
    return annonce


def _manifeste_complet_espion(**kwargs) -> dict:
    return {
        "references_incluses": True,
        "complet": True,
        "complet_perimetre_demande": True,
        "fichiers_locaux": {"nb_copies": 2, "nb_manquants": 0, "copies": [], "manquants": []},
        "objets_s3": {"nb_copies": 1, "nb_manquants": 0, "copies": [], "manquants": []},
        "data": {"nb_copies": 3, "nb_echecs": 0},
        "dump": {"fichier": "d", "sha256": "0" * 64, "octets": 1},
        NATURE_MANIFESTE: kwargs.get("nature"),
    }


def test_le_guide_annonce_exactement_le_perimetre_de_la_complete(tmp_path: Path, monkeypatch) -> None:
    """Le miroir de l'épreuve existante, pour la complète.

    La quotidienne a sa ligne ``sauvegarde-auto:`` ; la complète a la sienne.
    Sans ce test, le guide pourrait annoncer « tout » pour la complète alors
    qu'elle n'emporte rien de plus que la quotidienne — et l'exploitante
    croirait ses documents protégés sept jours sur sept.
    """
    from seamtech_search import sauvegarde

    (tmp_path / "data").mkdir()
    (tmp_path / "archive").mkdir()
    vu: set[str] = set()

    def sauver_espion(base_url, racines_archive, dossier_local, client_s3=None, retention=5, **kwargs):
        if base_url:
            vu.add("base")
        if kwargs.get("inclure_references_base"):
            vu.add("references")
        if kwargs.get("racines_data"):
            vu.add("donnees")
        return _manifeste_complet_espion(**kwargs)

    monkeypatch.setattr(sauvegarde, "sauver", sauver_espion)
    monkeypatch.setattr("seamtech_search.sauvegarde_complete.client_documents_ou_echec", lambda: object())
    monkeypatch.setattr(
        "seamtech_search.sauvegarde_corpus.sauvegarder",
        lambda client, dossier=None: (vu.add("corpus"), {"nb_fichiers": 3, "horodatage": "x"})[1],
    )
    monkeypatch.setattr("seamtech_search.sauvegarde_corpus.dossier_corpus", lambda: tmp_path / "data")
    monkeypatch.setenv("SEAMTECH_BACKUP_DIR", str(tmp_path / "data" / "backups"))

    sauvegarder(_config(tmp_path), dossier=tmp_path / "data" / "backups", client_s3=object())

    annonce = _perimetre_annonce()
    assert annonce <= vu, f"le guide promet {sorted(annonce - vu)} que la complète ne fait pas"
    assert annonce == vu, f"la complète couvre {sorted(vu - annonce)} de plus que ce que le guide annonce"

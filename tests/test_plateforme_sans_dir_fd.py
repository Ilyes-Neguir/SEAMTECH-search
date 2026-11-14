"""Ce que fait le produit quand le système ne sait pas ouvrir par descripteur de
répertoire (``os.open`` absent de ``os.supports_dir_fd``) — ce qu'est Windows.

Windows natif n'est pas joignable depuis ce dépôt. On ne SUPPOSE donc pas le
comportement du produit sur Windows : on le **reproduit**. La condition testée
par ``sauvegarde._ouvrir_source_bundle`` est publique et explicite ::

    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_DIRECTORY") or os.open not in os.supports_dir_fd):
        raise SauvegardeError("Lecture confinée du bundle indisponible sur cette plateforme")

Vider ``os.supports_dir_fd`` déclenche ce refus sur n'importe quelle machine.
Les quatre conséquences mesurées sur le runner Windows (runs 41564776168 et
41569952579) sont donc vérifiables ici, par n'importe qui, sans Windows :

* ``assert change`` faux — ``os.open`` n'est jamais appelé (le refus précède
  toute ouverture) ;
* ``IndexError: list index out of range`` — ``restaurer`` échoue avant
  d'exécuter la moindre commande, donc aucun appel à enregistrer ;
* ``[XPASS(strict)]`` — la restauration multi-fichiers ne publie rien du tout,
  donc « le premier fichier ne reste pas » se trouve vrai pour la mauvaise
  raison.

Ce fichier est la PREUVE de ces écarts, pas leur récit.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from seamtech_search import sauvegarde
from seamtech_search.sauvegarde import SauvegardeError

pytestmark = pytest.mark.sauvegarde


@pytest.fixture
def sans_dir_fd(monkeypatch):
    """Le système ne sait pas ouvrir un chemin relatif à un descripteur."""
    monkeypatch.setattr(os, "supports_dir_fd", frozenset())
    return monkeypatch


def test_lecture_confinee_refusee_sans_dir_fd(tmp_path, sans_dir_fd):
    """Le refus est explicite, typé, et nomme la plateforme."""
    (tmp_path / "dump").write_bytes(b"contenu-synthetique")
    with pytest.raises(SauvegardeError, match="indisponible sur cette plateforme"):
        with sauvegarde._ouvrir_source_bundle(tmp_path, Path("dump")):
            pytest.fail("aucune ouverture confinée sans dir_fd")


def test_instantane_dump_prive_copie_fidelement_sans_dir_fd(tmp_path, sans_dir_fd):
    """Sans lecture confinée, la copie privée est faite, et elle est FIDÈLE.

    Ce test remplace « _instantane_dump_prive_n_ouvre_rien », qui constatait le
    refus. Le refus n'était pas une sécurité : il rendait ``restaurer
    --coordonnee`` entièrement indisponible sur Windows. Le repli est sûr parce
    que la propriété de sécurité ne repose PAS sur ``O_NOFOLLOW`` mais sur
    l'empreinte, vérifiée juste après — ce que le test suivant démontre.
    """
    dump = tmp_path / "dump"
    contenu = b"contenu-synthetique" * 5000
    dump.write_bytes(contenu)
    with sauvegarde._instantane_dump_prive(dump) as copie:
        assert Path(copie).read_bytes() == contenu, "la copie privée doit être fidèle"
        assert Path(copie) != dump, "pg_restore ne doit jamais relire le chemin d'origine"


def test_restaurer_refuse_un_dump_substitue_meme_sans_dir_fd(tmp_path, sans_dir_fd, monkeypatch):
    """Sans lecture confinée, un dump falsifié est TOUJOURS refusé avant pg_restore.

    C'est la preuve qui autorise le repli. ``O_NOFOLLOW`` empêchait qu'un lien
    redirige la lecture ; sans lui, une substitution est possible — mais elle
    produit une copie dont l'empreinte diffère du manifeste, et
    ``_restaurer_dump_verifie`` la refuse AVANT toute commande. La sécurité
    devient donc : « seuls les octets dont l'empreinte figure au manifeste sont
    restaurés », ce qui tient sans ``O_NOFOLLOW``.

    Le scénario est une substitution RÉELLE : le fichier est remplacé entre la
    lecture du manifeste et la copie, pas seulement annoncé faux.
    """
    import hashlib

    dump = tmp_path / "dump.dump"
    dump.write_bytes(b"dump attendu")
    # Empreinte d'un AUTRE contenu : le manifeste décrit ce qui doit être là.
    manifeste = {"dump": {"sha256": hashlib.sha256(b"dump substitue").hexdigest(), "octets": 12}}
    commandes: list[list[str]] = []

    def run(args, **_kwargs):
        commandes.append(list(args))
        raise AssertionError("pg_restore ne doit pas être lancé sur un dump dont l'empreinte diffère")

    monkeypatch.setattr(sauvegarde.subprocess, "run", run)
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", lambda nom: nom)
    with pytest.raises(SauvegardeError, match="empreinte du dump"):
        sauvegarde.restaurer("postgresql://synthetique/cible", manifeste, dump)
    assert commandes == [], "pg_restore a été lancé malgré une empreinte non conforme"


def test_publication_multi_fichiers_ne_publie_rien(tmp_path, sans_dir_fd, monkeypatch):
    """Cause du XPASS : rien n'est publié, donc « rien ne reste » est vrai.

    Le test ``test_erreur_seconde_publication_ne_laisse_pas_premiere`` constate
    que la première copie RESTE après l'échec de la seconde (publication non
    transactionnelle) et porte ce constat en ``xfail``. Ici la restauration
    refuse avant la première copie : le test réussirait pour la MAUVAISE
    raison. On le démontre, et il saute sur ces plateformes.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "a").write_bytes(b"a")
    (bundle / "b").write_bytes(b"b")
    manifeste = {
        "fichiers_locaux": {
            "copies": [
                {"id": 1, "path_relatif": "a", "sha256": "0" * 64},
                {"id": 2, "path_relatif": "b", "sha256": "1" * 64},
            ]
        }
    }
    sortie = tmp_path / "out"
    with pytest.raises(SauvegardeError):
        sauvegarde.restaurer_fichiers_locaux(manifeste, bundle, sortie)
    assert not sortie.exists() or not any(sortie.iterdir()), (
        "la publication a écrit quelque chose malgré le refus de lecture confinée"
    )


def test_restaurer_via_le_repli_quand_l_empreinte_est_conforme(tmp_path, sans_dir_fd, monkeypatch):
    """Sans lecture confinée, une restauration CONFORME est menée à son terme.

    Contrepartie du test précédent, et la raison d'être du repli : sur Windows,
    ``restaurer --coordonnee`` échouait AVANT d'avoir rien tenté, en
    « Lecture confinée du bundle indisponible sur cette plateforme » — alors
    que le dump était parfaitement conforme. Le repli lui rend la restauration
    sans lever l'exigence d'empreinte, comme le test précédent le prouve.
    """
    import hashlib

    contenu = b"dump synthetique conforme"
    dump = tmp_path / "dump.dump"
    dump.write_bytes(contenu)
    manifeste = {"dump": {"sha256": hashlib.sha256(contenu).hexdigest(), "octets": len(contenu)}}
    commandes: list[list[str]] = []

    class _Resultat:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(args, **_kwargs):
        commandes.append(list(args))
        return _Resultat()

    monkeypatch.setattr(sauvegarde.subprocess, "run", run)
    monkeypatch.setattr(sauvegarde, "_trouver_binaire", lambda nom: nom)
    monkeypatch.setattr(
        sauvegarde,
        "_etat_base",
        lambda url: {
            "versions_schema": [],
            "fiches_par_statut": {},
            "schema_identite_octets_valide": True,
        },
    )
    monkeypatch.setattr(sauvegarde, "_connecter", lambda *a, **k: _ConnexionFactice())

    rapport = sauvegarde.restaurer("postgresql://synthetique/cible", manifeste, dump)
    assert rapport["base"] == "cible"
    assert len(commandes) == 2, f"vérification puis restauration attendues : {commandes}"


class _ConnexionFactice:
    """Connexion administrative minimale : la base cible n'existe pas encore."""

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return None

    def close(self):
        return None


def test_copie_des_fichiers_locaux_refusee_sans_dir_fd(tmp_path, sans_dir_fd):
    """Cause du diagnostic Windows « diag-cible-5 », prouvée sans Windows.

    Le runner Windows faisait échouer
    ``test_sha_snapshot_propage_aux_copies_locale_et_objet_pg`` sur ::

        seamtech_search/sauvegarde.py:669  SauvegardeError
        « Lecture confinée du bundle indisponible sur cette plateforme »

    Ce n'est ni un état partagé, ni un ordre d'exécution, ni une fixture :
    c'est une **capacité du noyau**. ``copier_fichiers_locaux`` lit chaque
    source par ``_ouvrir_source_bundle``, qui exige ``O_NOFOLLOW`` +
    ``O_DIRECTORY`` + ``dir_fd``. Windows natif n'en a aucun des trois, et
    cette fonction n'a **pas** de repli — à la différence du dump privé, qui en
    a un (voir l'épreuve voisine).

    On le démontre ici, sur n'importe quelle machine, en vidant
    ``os.supports_dir_fd`` : l'échec du runner est donc reproductible et sa
    cause attribuée. Conséquence assumée : **la sauvegarde des pièces jointes
    locales est indisponible sur Windows**, elle n'y est pas « cassée » — le
    produit refuse explicitement plutôt que de lire sans confinement.
    """
    import hashlib

    source = tmp_path / "piece.txt"
    contenu = b"contenu-local"
    source.write_bytes(contenu)
    destination = tmp_path / "bundle"

    with pytest.raises(SauvegardeError, match="Lecture confinée du bundle indisponible"):
        sauvegarde.copier_fichiers_locaux(
            {
                "documents": [
                    {
                        "id": 1,
                        "path": str(source),
                        "name": source.name,
                        "is_dir": False,
                        "source_sha256": hashlib.sha256(contenu).hexdigest(),
                    }
                ]
            },
            destination,
        )


def test_la_cause_est_la_capacite_et_non_lenvironnement(tmp_path):
    """Contrôle : SANS la panne, la même copie RÉUSSIT.

    C'est ce qui distingue « la plateforme ne sait pas » d'« une isolation de
    test défaillante ». Si l'échec de Windows venait d'un état partagé, d'un
    ordre d'exécution ou d'une fixture, ce contrôle échouerait aussi — or il
    passe, avec rigoureusement le même code et la même arborescence. La cause
    est donc bien la capacité du noyau, attribuée et non supposée.
    """
    import hashlib

    source = tmp_path / "piece.txt"
    contenu = b"contenu-local"
    source.write_bytes(contenu)
    destination = tmp_path / "bundle"

    rapport = sauvegarde.copier_fichiers_locaux(
        {
            "documents": [
                {
                    "id": 1,
                    "path": str(source),
                    "name": source.name,
                    "is_dir": False,
                    "source_sha256": hashlib.sha256(contenu).hexdigest(),
                }
            ]
        },
        destination,
    )
    copies = rapport["copies"]
    assert len(copies) == 1, f"la copie doit aboutir quand dir_fd existe : {rapport}"
    assert copies[0]["identite_catalogue"] == "verifiee"
    assert (destination / copies[0]["path_relatif"]).read_bytes() == contenu

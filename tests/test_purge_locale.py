"""P0 — purge de la copie locale : on ne supprime que ce qui est PROUVÉ, MAINTENANT.

Deux pannes se sont succédé sur ce sujet, et ce fichier garde les deux en
mémoire pour qu'aucune ne revienne :

* la panne « souvenir » : supprimer la copie locale parce qu'un envoi a réussi
  hier. Une preuve d'envoi ne couvre ni un fichier ajouté après l'inventaire,
  ni un fichier modifié depuis, ni la disparition de l'objet. C'est pour cette
  panne que toute purge avait été suspendue ;
* la panne « suspension » : ne jamais rien supprimer. Le serveur se remplit
  alors indéfiniment, sans qu'aucun indicateur ne le dise.

Le protocole retenu est testé ici dans ses deux sens : ce qui est prouvé à
l'instant de la purge part, et TOUT le reste reste — en étant nommé.

Les tests passent par ``S3StorageClient.verifier_integrite`` branché sur un
magasin en mémoire : la preuve exercée est la vraie (relecture des octets),
seul le transport est remplacé.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from seamtech_search.purge_locale import BilanPurge, purger_copie_prouvee
from seamtech_search.storage import S3StorageClient
from tests.s3_en_memoire import S3EnMemoire


def _sha(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()


def _sha_fichier(chemin: Path) -> str:
    return _sha(chemin.read_bytes())


def _entree(
    chemin: Path,
    *,
    cle: str = "objets/fiche.pdf",
    empreinte: str | None = None,
    statut: str = "uploaded",
) -> dict[str, Any]:
    """Une entrée de manifeste d'import, telle que la chaîne la produit."""
    return {
        "path": str(chemin),
        "object_key": cle,
        "upload_status": statut,
        "source_sha256": empreinte if empreinte is not None else _sha_fichier(chemin),
    }


@contextmanager
def _magasin() -> Iterator[tuple[S3StorageClient, S3EnMemoire]]:
    """Un ``S3StorageClient`` réel branché sur un magasin en mémoire."""
    from seamtech_search.config import AppConfig

    magasin = S3EnMemoire()
    patchs = [
        patch.object(S3StorageClient, "_get_client", lambda self, probe_timeout=None: magasin),
        patch.object(S3StorageClient, "ensure_bucket_exists", lambda self: True),
        patch.object(S3StorageClient, "first_free_key", lambda self, cle: cle),
        patch.object(S3StorageClient, "_enable_versioning_once", lambda self: None),
    ]
    for p in patchs:
        p.start()
    try:
        client = S3StorageClient(
            config=AppConfig(
                root_paths=[Path.cwd()],
                min_free_bytes=0,
                s3_endpoint_url="https://s3.invalide",
                s3_bucket="seamtech-documents",
                s3_access_key="cle-fictive",
                s3_secret_key="secret-fictif",
            )
        )
        yield client, magasin
    finally:
        for p in patchs:
            p.stop()


def _verificateur(client: S3StorageClient, *, relire: bool = True):
    def verifier(cle: str, chemin: Path) -> Any:
        return client.verifier_integrite(cle, chemin, relire=relire)

    return verifier


# --------------------------------------------------------------------------- #
# 1. Appartenance : jamais hors staging, jamais un lien
# --------------------------------------------------------------------------- #


def test_hors_staging_rien_n_est_supprime(tmp_path: Path) -> None:
    dossier = tmp_path / "archive-du-commanditaire"
    dossier.mkdir()
    piece = dossier / "fiche.pdf"
    piece.write_bytes(b"%PDF-1.4 original")

    bilan = purger_copie_prouvee(
        racine=dossier, entrees=[_entree(piece)], sous_staging=False, verifier_objet=lambda *_: None
    )

    assert bilan.purge is False
    assert "hors staging" in bilan.raison
    assert piece.exists()


def test_un_lien_symbolique_n_est_jamais_suivi(tmp_path: Path) -> None:
    dossier = tmp_path / "staging"
    dossier.mkdir()
    piece = dossier / "fiche.pdf"
    piece.write_bytes(b"%PDF-1.4 a envoyer")
    cible = tmp_path / "hors-staging.txt"
    cible.write_text("je n'appartiens pas au staging", encoding="utf-8")
    lien = dossier / "raccourci.txt"
    try:
        lien.symlink_to(cible)
    except (OSError, NotImplementedError):  # pragma: no cover - plateforme sans liens
        pytest.skip("liens symboliques indisponibles sur cette plateforme")

    with _magasin() as (client, magasin):
        magasin.put_object(Bucket="seamtech-documents", Key="objets/fiche.pdf", Body=piece.read_bytes())
        bilan = purger_copie_prouvee(
            racine=dossier,
            entrees=[_entree(piece)],
            sous_staging=True,
            verifier_objet=_verificateur(client),
        )

    assert bilan.purge is True
    assert not piece.exists()
    # Le lien est conservé (signalé), et surtout la cible est intacte.
    assert lien.is_symlink()
    assert cible.exists()
    assert any("lien symbolique" in motif for _, motif in bilan.conserves)


# --------------------------------------------------------------------------- #
# 2. Sans vérificateur, RIEN ne part
# --------------------------------------------------------------------------- #


def test_sans_verification_distante_rien_n_est_supprime(tmp_path: Path) -> None:
    dossier = tmp_path / "staging"
    dossier.mkdir()
    piece = dossier / "fiche.pdf"
    piece.write_bytes(b"%PDF-1.4 a envoyer")

    bilan = purger_copie_prouvee(
        racine=dossier, entrees=[_entree(piece)], sous_staging=True, verifier_objet=None
    )

    assert bilan.purge is False
    assert bilan.verification_indisponible is True
    assert piece.exists(), "« je n'ai pas pu vérifier » n'autorise aucune suppression"


def test_verificateur_en_panne_conserve_la_copie(tmp_path: Path) -> None:
    dossier = tmp_path / "staging"
    dossier.mkdir()
    piece = dossier / "fiche.pdf"
    piece.write_bytes(b"%PDF-1.4 a envoyer")

    def verificateur_casse(_cle: str, _chemin: Path) -> Any:
        raise RuntimeError("fournisseur injoignable")

    bilan = purger_copie_prouvee(
        racine=dossier, entrees=[_entree(piece)], sous_staging=True, verifier_objet=verificateur_casse
    )

    assert bilan.purge is False
    assert piece.exists()
    assert len(bilan.objets_illisibles) == 1


# --------------------------------------------------------------------------- #
# 3. Ce qui est prouvé part ; le dossier ne part que vidé
# --------------------------------------------------------------------------- #


def test_tout_prouve_est_supprime_et_le_dossier_efface(tmp_path: Path) -> None:
    dossier = tmp_path / "staging" / "envoi-1"
    dossier.mkdir(parents=True)
    fiche = dossier / "fiche.pdf"
    cotes = dossier / "sous-dossier" / "cotes.xlsx"
    cotes.parent.mkdir()
    fiche.write_bytes(b"%PDF-1.4 fiche")
    cotes.write_bytes(b"PK\x03\x04 cotes")

    with _magasin() as (client, magasin):
        magasin.put_object(Bucket="seamtech-documents", Key="objets/fiche.pdf", Body=fiche.read_bytes())
        magasin.put_object(Bucket="seamtech-documents", Key="objets/cotes.xlsx", Body=cotes.read_bytes())
        bilan = purger_copie_prouvee(
            racine=dossier,
            entrees=[
                _entree(fiche, cle="objets/fiche.pdf"),
                _entree(cotes, cle="objets/cotes.xlsx"),
            ],
            sous_staging=True,
            verifier_objet=_verificateur(client),
        )

    assert bilan.purge is True
    assert not fiche.exists()
    assert not cotes.exists()
    assert bilan.dossier_supprime is True
    assert not dossier.exists()
    assert bilan.octets_liberes == len(b"%PDF-1.4 fiche") + len(b"PK\x03\x04 cotes")


def test_fichier_sans_preuve_conserve_le_dossier(tmp_path: Path) -> None:
    dossier = tmp_path / "staging" / "envoi-2"
    dossier.mkdir(parents=True)
    fiche = dossier / "fiche.pdf"
    fiche.write_bytes(b"%PDF-1.4 fiche")
    ajoute = dossier / "note-ajoutee-apres-inventaire.txt"
    ajoute.write_text("ajouté après l'inventaire : jamais envoyé", encoding="utf-8")

    with _magasin() as (client, magasin):
        magasin.put_object(Bucket="seamtech-documents", Key="objets/fiche.pdf", Body=fiche.read_bytes())
        bilan = purger_copie_prouvee(
            racine=dossier,
            entrees=[_entree(fiche)],
            sous_staging=True,
            verifier_objet=_verificateur(client),
        )

    assert bilan.purge is True, "une purge PARTIELLE est un état normal, pas un échec"
    assert not fiche.exists()
    assert ajoute.exists(), "un fichier sans preuve d'envoi n'a aucune copie ailleurs"
    assert dossier.exists()
    assert bilan.dossier_supprime is False
    assert bilan.non_prouves == (str(ajoute),)


def test_entree_hors_dossier_ignoree(tmp_path: Path) -> None:
    dossier = tmp_path / "staging" / "envoi-3"
    dossier.mkdir(parents=True)
    dehors = tmp_path / "ailleurs.pdf"
    dehors.write_bytes(b"%PDF-1.4 hors dossier")

    with _magasin() as (client, magasin):
        magasin.put_object(Bucket="seamtech-documents", Key="objets/dehors.pdf", Body=dehors.read_bytes())
        bilan = purger_copie_prouvee(
            racine=dossier,
            entrees=[_entree(dehors, cle="objets/dehors.pdf")],
            sous_staging=True,
            verifier_objet=_verificateur(client),
        )

    assert bilan.purge is False
    assert dehors.exists(), "le manifeste ne donne pas mandat d'effacer hors du dossier de purge"


# --------------------------------------------------------------------------- #
# 4. Identité : modifié après inventaire ⇒ conservé
# --------------------------------------------------------------------------- #


def test_fichier_modifie_depuis_inventaire_est_conserve(tmp_path: Path) -> None:
    dossier = tmp_path / "staging" / "envoi-4"
    dossier.mkdir(parents=True)
    fiche = dossier / "fiche.pdf"
    fiche.write_bytes(b"%PDF-1.4 version inventoriee")
    entree = _entree(fiche, empreinte=_sha(b"%PDF-1.4 version inventoriee"))

    with _magasin() as (client, magasin):
        magasin.put_object(
            Bucket="seamtech-documents",
            Key="objets/fiche.pdf",
            Body=b"%PDF-1.4 version inventoriee",
        )
        # Le fichier est remplacé APRÈS l'inventaire : ses octets n'ont jamais
        # été envoyés, même si le nom et la clé sont les mêmes.
        fiche.write_bytes(b"%PDF-1.4 version modifiee")
        bilan = purger_copie_prouvee(
            racine=dossier, entrees=[entree], sous_staging=True, verifier_objet=_verificateur(client)
        )

    assert bilan.purge is False
    assert fiche.exists()
    assert bilan.modifies == (str(fiche),)
    assert "modifié depuis l'inventaire" in " ".join(motif for _, motif in bilan.conserves)


def test_fichier_change_pendant_la_purge_est_conserve(tmp_path: Path) -> None:
    dossier = tmp_path / "staging" / "envoi-5"
    dossier.mkdir(parents=True)
    fiche = dossier / "fiche.pdf"
    fiche.write_bytes(b"%PDF-1.4 version inventoriee")

    class _Resultat:
        integrite_prouvee = True
        methode = "relecture_sha256"

    def verificateur_qui_mute(_cle: str, chemin: Path) -> Any:
        # Concurrence réelle : un tiers écrit entre la vérification distante
        # et la suppression. Le dernier contrôle doit l'attraper.
        chemin.write_bytes(b"%PDF-1.4 ecrit par un tiers")
        return _Resultat()

    bilan = purger_copie_prouvee(
        racine=dossier,
        entrees=[_entree(fiche, empreinte=_sha(b"%PDF-1.4 version inventoriee"))],
        sous_staging=True,
        verifier_objet=verificateur_qui_mute,
    )

    assert bilan.purge is False
    assert fiche.exists()
    assert bilan.modifies == (str(fiche),)


# --------------------------------------------------------------------------- #
# 5. Qualité de la preuve distante : relecture, pas déclaration
# --------------------------------------------------------------------------- #


def test_objet_distant_corrompu_interdit_la_suppression(tmp_path: Path) -> None:
    dossier = tmp_path / "staging" / "envoi-6"
    dossier.mkdir(parents=True)
    fiche = dossier / "fiche.pdf"
    fiche.write_bytes(b"%PDF-1.4 contenu integral")

    with _magasin() as (client, magasin):
        magasin.put_object(
            Bucket="seamtech-documents", Key="objets/fiche.pdf", Body=fiche.read_bytes()
        )
        # Le stockage objet n'est pas une sauvegarde : l'objet peut être altéré.
        magasin.corrompre("objets/fiche.pdf", octets=b"TRONQUE")
        bilan = purger_copie_prouvee(
            racine=dossier, entrees=[_entree(fiche)], sous_staging=True, verifier_objet=_verificateur(client)
        )

    assert bilan.purge is False
    assert fiche.exists(), "la copie locale est la SEULE copie intègre : elle reste"
    assert bilan.objets_illisibles == (str(fiche),)


def test_preuve_declarative_n_autorise_pas_la_suppression(tmp_path: Path) -> None:
    """``metadata_seule`` est une déclaration que NOUS avons fournie : non prouvé."""
    dossier = tmp_path / "staging" / "envoi-7"
    dossier.mkdir(parents=True)
    fiche = dossier / "fiche.pdf"
    fiche.write_bytes(b"%PDF-1.4 contenu integral")

    with _magasin() as (client, magasin):
        magasin.put_object(
            Bucket="seamtech-documents",
            Key="objets/fiche.pdf",
            Body=fiche.read_bytes(),
            Metadata={"seamtech-sha256": _sha(fiche.read_bytes())},
        )
        bilan = purger_copie_prouvee(
            racine=dossier,
            entrees=[_entree(fiche)],
            sous_staging=True,
            verifier_objet=_verificateur(client, relire=False),
        )

    assert bilan.purge is False
    assert fiche.exists()


def test_objet_absent_interdit_la_suppression(tmp_path: Path) -> None:
    dossier = tmp_path / "staging" / "envoi-8"
    dossier.mkdir(parents=True)
    fiche = dossier / "fiche.pdf"
    fiche.write_bytes(b"%PDF-1.4 jamais envoye")

    with _magasin() as (client, _):
        bilan = purger_copie_prouvee(
            racine=dossier, entrees=[_entree(fiche)], sous_staging=True, verifier_objet=_verificateur(client)
        )

    assert bilan.purge is False
    assert fiche.exists()


# --------------------------------------------------------------------------- #
# 6. Le bilan est lisible par l'exploitant
# --------------------------------------------------------------------------- #


def test_bilan_dit_ce_qui_est_supprime_et_pourquoi_le_reste_reste(tmp_path: Path) -> None:
    dossier = tmp_path / "staging" / "envoi-9"
    dossier.mkdir(parents=True)
    fiche = dossier / "fiche.pdf"
    fiche.write_bytes(b"%PDF-1.4 fiche")
    inconnu = dossier / "inconnu.txt"
    inconnu.write_text("sans preuve", encoding="utf-8")

    with _magasin() as (client, magasin):
        magasin.put_object(Bucket="seamtech-documents", Key="objets/fiche.pdf", Body=fiche.read_bytes())
        bilan = purger_copie_prouvee(
            racine=dossier, entrees=[_entree(fiche)], sous_staging=True, verifier_objet=_verificateur(client)
        )
        rapport = bilan.to_dict()

    assert rapport["purge"] is True
    assert rapport["nb_supprimes"] == 1
    assert rapport["nb_conserves"] == 1
    assert rapport["octets_liberes"] == len(b"%PDF-1.4 fiche")
    assert any("aucune preuve d'envoi" in motif for motif in rapport["conserves"])
    assert rapport["dossier_supprime"] is False


def test_bilan_refuse_par_defaut() -> None:
    bilan = BilanPurge(False, "rien à examiner")
    rapport = bilan.to_dict()
    assert rapport["purge"] is False
    assert rapport["nb_supprimes"] == 0
    assert rapport["conserves"] == []

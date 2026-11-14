"""Sauvegarde hors-git du corpus — preuves, dont la restauration avec empreintes.

Lot 21, priorité 0. Deux niveaux :

* des épreuves hors réseau, sur un client factice, qui fixent le comportement
  (intégrité, refus d'un nom distant, injection de clé) ;
* une épreuve marquée ``s3rver`` qui rejoue le trajet complet contre
  l'émulateur HTTP de test — pas MinIO ni R2, donc une qualification de
  mécanisme, pas du fournisseur réel.

Le lot exige une **restauration éprouvée**, pas une exécution sans erreur :
chaque épreuve de restauration vérifie l'empreinte du fichier rendu.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from seamtech_search import sauvegarde_corpus as module

# ---------------------------------------------------------------------------
# Client factice
# ---------------------------------------------------------------------------


class ClientFactice:
    """Stockage objet en mémoire, suffisant pour éprouver le mécanisme.

    ``prefixe`` reproduit le piège réel de ``S3StorageClient`` : l'envoi
    applique le préfixe et renvoie la clé réelle, alors que le téléchargement
    n'en applique aucun. Un code qui enregistre la clé DEMANDÉE au lieu de la
    clé RÉELLE ne restaure plus rien dès que le préfixe est configuré.
    """

    def __init__(self, *, prefixe: str = "", panne_liste: bool = False) -> None:
        self.prefixe = prefixe
        self.objets: dict[str, bytes] = {}
        self.panne_liste = panne_liste

    def _cle(self, cle: str) -> str:
        if not self.prefixe:
            return cle
        return f"{self.prefixe}/{cle.lstrip('/')}"

    def upload_bytes(self, data: bytes, remote_key: str, content_type: str | None = None, **_):
        cle = self._cle(remote_key)
        self.objets[cle] = data
        return cle

    def download_file(self, remote_key: str, destination_path: Path, **_):
        if remote_key not in self.objets:
            raise KeyError(remote_key)
        destination_path = Path(destination_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_bytes(self.objets[remote_key])
        return destination_path

    def list_keys(self, prefix: str) -> list[str]:
        if self.panne_liste:
            raise RuntimeError("inventaire indisponible")
        return sorted(cle for cle in self.objets if cle.startswith(self._cle(prefix)))


def _corpus(dossier: Path) -> Path:
    """Un corpus jetable : jamais le vrai, jamais le dépôt."""
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "accumulateur.json").write_text('{"fiches": []}', encoding="utf-8")
    (dossier / "decisions.json").write_text('{"decisions": []}', encoding="utf-8")
    return dossier


def _manifeste(client: ClientFactice, horodatage: str) -> dict:
    return json.loads(client.objets[client._cle(f"corpus/{horodatage}/manifest.json")])


# ---------------------------------------------------------------------------
# Sauvegarde
# ---------------------------------------------------------------------------


def test_sauvegarde_sans_client_ne_fait_rien_et_le_dit(tmp_path: Path):
    dossier = _corpus(tmp_path / "corpus")
    manifeste = module.sauvegarder(None, dossier=dossier)
    assert manifeste["hors_site"] is False
    assert manifeste["nb_fichiers"] == 2
    assert "cle_manifeste" not in manifeste
    # Rien n'est écrit à côté : une copie locale n'est pas une sauvegarde.
    assert sorted(p.name for p in dossier.iterdir()) == ["accumulateur.json", "decisions.json"]


def test_sauvegarde_verse_les_empreintes_et_le_manifeste(tmp_path: Path):
    dossier = _corpus(tmp_path / "corpus")
    client = ClientFactice()
    instant = datetime(2026, 11, 12, 3, 4, 5, tzinfo=timezone.utc)
    manifeste = module.sauvegarder(client, dossier=dossier, maintenant=instant)

    assert manifeste["horodatage"] == "20261112T030405Z"
    assert manifeste["hors_site"] is True
    noms = {e["nom"] for e in manifeste["fichiers"]}
    assert noms == {"accumulateur.json", "decisions.json"}
    for entree in manifeste["fichiers"]:
        attendu = hashlib.sha256((dossier / entree["nom"]).read_bytes()).hexdigest()
        assert entree["sha256"] == attendu
        assert client.objets[entree["cle"]] == (dossier / entree["nom"]).read_bytes()
    assert _manifeste(client, "20261112T030405Z")["horodatage"] == "20261112T030405Z"


def test_sauvegarde_sans_fichier_echoue_franchement(tmp_path: Path):
    with pytest.raises(module.SauvegardeCorpusError, match="Aucun fichier"):
        module.sauvegarder(ClientFactice(), dossier=tmp_path / "vide")


def test_client_sans_upload_est_refuse(tmp_path: Path):
    class SansEnvoi(ClientFactice):
        upload_bytes = None
        upload_file = None

    with pytest.raises(module.SauvegardeCorpusError, match="inutilisable"):
        module.sauvegarder(SansEnvoi(), dossier=_corpus(tmp_path / "corpus"))


# ---------------------------------------------------------------------------
# Restauration éprouvée
# ---------------------------------------------------------------------------


def test_aller_retour_restaure_a_l_identique(tmp_path: Path):
    source = _corpus(tmp_path / "source")
    client = ClientFactice()
    module.sauvegarder(client, dossier=source, maintenant=datetime(2026, 11, 12, tzinfo=timezone.utc))

    destination = tmp_path / "recu"
    rapport = module.restaurer(client, destination)

    assert rapport["integrite"] == "verifiee"
    assert rapport["nb_fichiers"] == 2
    for nom in ("accumulateur.json", "decisions.json"):
        assert (destination / nom).read_bytes() == (source / nom).read_bytes()


def test_restauration_detecte_un_objet_corrumpu(tmp_path: Path):
    """Le cœur du lot : une empreinte divergente fait échouer la restauration."""
    source = _corpus(tmp_path / "source")
    client = ClientFactice()
    module.sauvegarder(client, dossier=source, maintenant=datetime(2026, 11, 12, tzinfo=timezone.utc))

    cle = next(cle for cle in client.objets if cle.endswith("accumulateur.json"))
    client.objets[cle] = b'{"fiches": "TRONQUE"'

    with pytest.raises(module.SauvegardeCorpusError, match="INTÉGRITÉ ROMPUE"):
        module.restaurer(client, tmp_path / "recu")


def test_restauration_avec_prefixe_applicatif(tmp_path: Path):
    """La clé enregistrée doit être celle RENVOYÉE par l'envoi, pas celle demandée."""
    source = _corpus(tmp_path / "source")
    client = ClientFactice(prefixe="seamtech-docs")
    module.sauvegarder(client, dossier=source, maintenant=datetime(2026, 11, 12, tzinfo=timezone.utc))

    assert all(cle.startswith("seamtech-docs/corpus/") for cle in client.objets)
    rapport = module.restaurer(client, tmp_path / "recu")
    assert (tmp_path / "recu" / "accumulateur.json").read_bytes() == (source / "accumulateur.json").read_bytes()
    assert rapport["integrite"] == "verifiee"


def test_manifeste_avec_nom_de_fichier_hostile_est_refuse(tmp_path: Path):
    """Un manifeste est une donnée distante : il ne commande pas une écriture."""
    source = _corpus(tmp_path / "source")
    client = ClientFactice()
    module.sauvegarder(client, dossier=source, maintenant=datetime(2026, 11, 12, tzinfo=timezone.utc))

    horodatage = module.dernier_instant(client)
    assert horodatage
    cle_manifeste = client._cle(f"corpus/{horodatage}/manifest.json")
    client.objets[cle_manifeste] = json.dumps(
        {
            "version": module.VERSION_MANIFESTE,
            "horodatage": horodatage,
            "fichiers": [{"nom": "../../hors_corpus", "cle": cle_manifeste, "sha256": "0" * 64}],
        }
    ).encode("utf-8")

    with pytest.raises(module.SauvegardeCorpusError, match="Nom de fichier refusé"):
        module.restaurer(client, tmp_path / "recu")
    assert not (tmp_path.parent / "hors_corpus").exists()


def test_horodatage_injecte_est_refuse(tmp_path: Path):
    client = ClientFactice()
    with pytest.raises(module.SauvegardeCorpusError, match="Horodatage refusé"):
        module.restaurer(client, tmp_path / "recu", horodatage="../../nimportequoi")


def test_restauration_sans_sauvegarde_est_dite(tmp_path: Path):
    with pytest.raises(module.SauvegardeCorpusError, match="Aucune sauvegarde"):
        module.restaurer(ClientFactice(), tmp_path / "recu")


def test_restauration_sans_client_est_impossible(tmp_path: Path):
    with pytest.raises(module.SauvegardeCorpusError, match="Aucun stockage distant"):
        module.restaurer(None, tmp_path / "recu")


def test_liste_en_panne_ne_devient_pas_une_sauvegarde(tmp_path: Path):
    client = ClientFactice(panne_liste=True)
    with pytest.raises(module.SauvegardeCorpusError, match="Inventaire"):
        module.dernier_instant(client)


# ---------------------------------------------------------------------------
# Supervision
# ---------------------------------------------------------------------------


def test_verifier_sans_stockage_est_non_prouve():
    etat = module.verifier(None)
    assert etat["protege"] is False
    assert "distant" in etat["motif"]


def test_verifier_frais_est_prouve(tmp_path: Path):
    source = _corpus(tmp_path / "source")
    client = ClientFactice()
    maintenant = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    module.sauvegarder(client, dossier=source, maintenant=maintenant)
    etat = module.verifier(client, maintenant=maintenant + timedelta(hours=1))
    assert etat["protege"] is True
    assert etat["dernier"] == "20261112T120000Z"


def test_verifier_perime_est_non_prouve(tmp_path: Path):
    source = _corpus(tmp_path / "source")
    client = ClientFactice()
    maintenant = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    module.sauvegarder(client, dossier=source, maintenant=maintenant)
    etat = module.verifier(client, maintenant=maintenant + timedelta(hours=30))
    assert etat["protege"] is False
    assert "vieille" in etat["motif"]


def test_verifier_sans_sauvegarde_est_non_prouve():
    assert module.verifier(ClientFactice())["protege"] is False


# ---------------------------------------------------------------------------
# Trajet complet contre l'émulateur HTTP de test
# ---------------------------------------------------------------------------


@pytest.mark.s3rver
@pytest.mark.s3
def test_trajet_complet_sur_emulateur_isole(tmp_path: Path):
    """Envoi réel puis restauration réelle avec contrôle d'empreinte.

    Qualifie le MÉCANISME (un client S3 compatible), pas le fournisseur de
    production : s3rver n'est ni MinIO ni R2.
    """
    from seamtech_search.storage import S3StorageClient

    endpoint = os.environ.get("SEAMTECH_TEST_S3RVER_URL")
    if not endpoint:
        pytest.skip("émulateur réseau isolé absent")
    url = urlsplit(endpoint)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        pytest.fail("l'émulateur doit être isolé sur loopback")

    bucket = "seamtech-corpus-" + uuid.uuid4().hex
    client = S3StorageClient(
        endpoint_url=endpoint,
        bucket_name=bucket,
        access_key_id="S3RVER",
        secret_access_key="S3RVER",
        force_path_style=True,
    )
    brut = client._get_client()
    brut.create_bucket(Bucket=bucket)

    source = _corpus(tmp_path / "source")
    try:
        manifeste = module.sauvegarder(client, dossier=source)
        assert manifeste["hors_site"] is True
        assert module.verifier(client)["protege"] is True

        destination = tmp_path / "recu"
        rapport = module.restaurer(client, destination)
        assert rapport["integrite"] == "verifiee"
        for nom in ("accumulateur.json", "decisions.json"):
            assert (destination / nom).read_bytes() == (source / nom).read_bytes()

        # La corruption est vue côté restauration, pas seulement annoncée.
        cle = next(e["cle"] for e in manifeste["fichiers"] if e["nom"] == "accumulateur.json")
        brut.put_object(Bucket=bucket, Key=cle, Body=b'{"fiches": "TRONQUE"}')
        with pytest.raises(module.SauvegardeCorpusError, match="INTÉGRITÉ ROMPUE"):
            module.restaurer(client, tmp_path / "recu2", horodatage=manifeste["horodatage"])
    finally:
        for cle in list(client.list_keys("corpus/")):
            brut.delete_object(Bucket=bucket, Key=cle)
        brut.delete_bucket(Bucket=bucket)


# ---------------------------------------------------------------------------
# Ligne de commande et dossier du corpus
# ---------------------------------------------------------------------------


def _cli():
    import importlib.util

    chemin = Path(__file__).resolve().parent.parent / "scripts" / "sauvegarder_corpus.py"
    spec = importlib.util.spec_from_file_location("cli_sauvegarde_corpus", chemin)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


def test_dossier_corpus_suit_la_variable_d_environnement(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(module.VARIABLE_DOSSIER_CORPUS, str(tmp_path))
    assert module.dossier_corpus() == tmp_path
    monkeypatch.delenv(module.VARIABLE_DOSSIER_CORPUS)
    assert module.dossier_corpus().name == "etape0"


def test_cli_sans_stockage_distant_echoue_a_un(tmp_path: Path, monkeypatch):
    """Le CLI ne maquille jamais une copie locale en sauvegarde : code 1."""
    cli = _cli()
    monkeypatch.setattr(cli, "_client", lambda: None)
    monkeypatch.setattr(module, "dossier_corpus", lambda: _corpus(tmp_path / "corpus"))
    assert cli.main(["sauvegarder"]) == 1


def test_cli_verifier_sans_stockage_echoue_a_un(monkeypatch):
    cli = _cli()
    monkeypatch.setattr(cli, "_client", lambda: None)
    assert cli.main(["verifier"]) == 1

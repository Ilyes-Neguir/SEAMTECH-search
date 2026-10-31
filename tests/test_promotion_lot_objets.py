"""Promotion S3 : échantillons synthétiques et fournisseur simulé."""
import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest

from seamtech_search.fiches import televersement_lot as t


class StockageSimule:
    bucket_name = 'test-bucket'

    def __init__(self):
        self.objets = {}
        self.envois = 0
        self.corrompre = False

    def is_configured(self):
        return True

    def upload_file(self, local, remote_key, *, avoid_overwrite=True):
        self.envois += 1
        cle = remote_key + f'-{self.envois}'
        self.objets[cle] = b'erreur' if self.corrompre else local.read_bytes()
        return cle

    def verifier_integrite(self, cle, local, *, relire):
        assert relire is True
        return SimpleNamespace(integrite_prouvee=self.objets[cle] == local.read_bytes())


def session(tmp_path):
    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024, min_free_bytes=0)
    fichiers = {'annee/a.pdf': b'PDF synthetique', 'annee/b.txt': b'annexe'}
    token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=len(fichiers),
                                                taille_totale_octets=sum(map(len, fichiers.values())))['token']
    t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                           flux=[BytesIO(x) for x in fichiers.values()])
    t.terminer_session_televersement_lot(config, token)
    return config, token, fichiers


def test_envoi_verifie_tous_fichiers_rejeu_sans_doublon(tmp_path):
    config, token, fichiers = session(tmp_path)
    client = StockageSimule()
    resultat = t.promouvoir_session_televersement_objets(config, token, client)
    assert resultat['nb_verifies'] == 2
    dossier, manifeste = t._charger_manifeste(config, token)
    for nom, octets in fichiers.items():
        donnee = resultat['objets_promus'][str((dossier / 'fichiers' / nom).resolve())]
        assert donnee['object_key'] == manifeste['fichiers'][nom]['object_key']
        assert donnee['object_bucket'] == client.bucket_name
        assert donnee['sha256'] == hashlib.sha256(octets).hexdigest()
    for nom, octets in fichiers.items():
        entree = manifeste['fichiers'][nom]
        assert client.objets[entree['object_key']] == octets
        assert entree['object_bucket'] == client.bucket_name
        assert entree['empreinte_sha256'] == hashlib.sha256(octets).hexdigest()
        assert (dossier / 'fichiers' / nom).read_bytes() == octets
        assert nom not in entree['object_key'] and token not in entree['object_key']
    t.promouvoir_session_televersement_objets(config, token, client)
    assert client.envois == 2


def test_corruption_distante_ne_valide_pas_et_ne_supprime_pas_source(tmp_path):
    config, token, fichiers = session(tmp_path)
    client = StockageSimule()
    client.corrompre = True
    with pytest.raises(t.TeleversementLotErreur, match='intégrité'):
        t.promouvoir_session_televersement_objets(config, token, client)
    dossier, manifeste = t._charger_manifeste(config, token)
    assert all('object_key' not in v for v in manifeste['fichiers'].values())
    assert all((dossier / 'fichiers' / p).read_bytes() == b for p, b in fichiers.items())


def test_reprise_apres_echec_second_fichier(tmp_path):
    config, token, fichiers = session(tmp_path)
    class ClientPanne(StockageSimule):
        echouer = True
        def upload_file(self, local, remote_key, *, avoid_overwrite=True):
            if self.envois == 1 and self.echouer:
                self.echouer = False
                raise OSError('panne simulée')
            return super().upload_file(local, remote_key, avoid_overwrite=avoid_overwrite)
    client = ClientPanne()
    with pytest.raises(t.TeleversementLotErreur):
        t.promouvoir_session_televersement_objets(config, token, client)
    _, manifeste = t._charger_manifeste(config, token)
    assert sum('object_key' in item for item in manifeste['fichiers'].values()) == 1
    resultat = t.promouvoir_session_televersement_objets(config, token, client)
    assert resultat['nb_verifies'] == len(fichiers)
    assert client.envois == 2


def test_mutation_locale_pendant_envoi_interdit_validation(tmp_path):
    config, token, _ = session(tmp_path)
    class ClientMutation(StockageSimule):
        def upload_file(self, local, remote_key, *, avoid_overwrite=True):
            cle = super().upload_file(local, remote_key, avoid_overwrite=avoid_overwrite)
            local.write_bytes(b'modification')
            return cle
    client = ClientMutation()
    with pytest.raises(t.TeleversementLotErreur, match='intégrité'):
        t.promouvoir_session_televersement_objets(config, token, client)
    _, manifeste = t._charger_manifeste(config, token)
    assert all('object_key' not in item for item in manifeste['fichiers'].values())


def test_promotion_lot_regroupe_les_points_de_reprise(tmp_path, monkeypatch):
    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024, min_free_bytes=0)
    fichiers = {f'dossier/{i:03d}.txt': bytes([i % 256]) for i in range(110)}
    token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=len(fichiers),
                                              taille_totale_octets=len(fichiers))['token']
    t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=list(fichiers),
                                          flux=[BytesIO(x) for x in fichiers.values()])
    t.terminer_session_televersement_lot(config, token)
    appels = []
    original = t._sauver_manifeste
    def compter(*args):
        appels.append(1)
        return original(*args)
    monkeypatch.setattr(t, '_sauver_manifeste', compter)
    assert t.promouvoir_session_televersement_objets(config, token, StockageSimule())['nb_verifies'] == 110
    assert len(appels) <= 3


def test_promotion_grand_lot_evite_reecriture_integrale_par_bloc(tmp_path, monkeypatch):
    config = SimpleNamespace(database_path=tmp_path / 'data' / 'search.db', max_file_size_bytes=1024, min_free_bytes=0)
    n = 1000
    noms = [f'annee/{i:04d}.txt' for i in range(n)]
    token = t.creer_session_televersement_lot(config, nom_racine='TEST', nombre_fichiers=n,
                                              taille_totale_octets=n)['token']
    for start in range(0, n, 200):
        tranche = noms[start:start + 200]
        t.recevoir_fichiers_televersement_lot(config, token, chemins_relatives=tranche,
                                              flux=[BytesIO(b'x') for _ in tranche])
    t.terminer_session_televersement_lot(config, token)
    appels = []
    original = t._sauver_manifeste
    def compter(*args):
        appels.append(1)
        return original(*args)
    monkeypatch.setattr(t, '_sauver_manifeste', compter)
    assert t.promouvoir_session_televersement_objets(config, token, StockageSimule())['nb_verifies'] == n
    assert len(appels) <= 2


def test_reprise_journal_si_sauvegarde_manifeste_echoue(tmp_path, monkeypatch):
    config, token, fichiers = session(tmp_path)
    client = StockageSimule()
    original = t._sauver_manifeste
    def panne(*args):
        raise OSError('panne disque simulée')
    monkeypatch.setattr(t, '_sauver_manifeste', panne)
    with pytest.raises(OSError):
        t.promouvoir_session_televersement_objets(config, token, client)
    dossier, manifeste = t._charger_manifeste(config, token)
    assert all('object_key' not in v for v in manifeste['fichiers'].values())
    journal = dossier / 'promotion-objets.jsonl'
    assert journal.is_file()
    with journal.open('ab') as flux:
        flux.write(b'{"ligne_interrompue":')
    monkeypatch.setattr(t, '_sauver_manifeste', original)
    assert t.promouvoir_session_televersement_objets(config, token, client)['nb_verifies'] == len(fichiers)
    assert client.envois == len(fichiers)
    assert not journal.exists()
    _, manifeste = t._charger_manifeste(config, token)
    assert all(item.get('object_key') in client.objets for item in manifeste['fichiers'].values())


def test_reprise_refuse_journal_altere(tmp_path):
    config, token, _ = session(tmp_path)
    dossier, _ = t._charger_manifeste(config, token)
    (dossier / 'promotion-objets.jsonl').write_text('{"incoherent": true}\n')
    client = StockageSimule()
    with pytest.raises(t.TeleversementLotErreur, match='Journal'):
        t.promouvoir_session_televersement_objets(config, token, client)
    assert client.envois == 0


def test_reprise_journal_ne_publie_pas_copie_distante_devenue_invalide(tmp_path, monkeypatch):
    config, token, _ = session(tmp_path)
    client = StockageSimule()
    original = t._sauver_manifeste
    def panne(*args):
        raise OSError('panne disque simulée')
    monkeypatch.setattr(t, '_sauver_manifeste', panne)
    with pytest.raises(OSError):
        t.promouvoir_session_televersement_objets(config, token, client)
    monkeypatch.setattr(t, '_sauver_manifeste', original)
    client.objets[next(iter(client.objets))] = b'altere'
    with pytest.raises(t.TeleversementLotErreur, match='intégrité'):
        t.promouvoir_session_televersement_objets(config, token, client)
    dossier, manifeste = t._charger_manifeste(config, token)
    assert all('object_key' not in item for item in manifeste['fichiers'].values())
    assert (dossier / 'promotion-objets.jsonl').exists()


def test_promotion_interruptible_reste_reprenable(tmp_path):
    config, token, fichiers = session(tmp_path)
    client = StockageSimule()
    def continuer():
        return client.envois < 1
    with pytest.raises(t.TeleversementLotErreur, match='interrompue'):
        t.promouvoir_session_televersement_objets(config, token, client, doit_continuer=continuer)
    assert client.envois == 1
    assert t.promouvoir_session_televersement_objets(config, token, client)['nb_verifies'] == len(fichiers)
    assert client.envois == 2


def test_echec_promotion_expurge_trace_et_conserve_sources(tmp_path):
    import traceback

    config, token, fichiers = session(tmp_path)

    class ClientPanne(StockageSimule):
        def upload_file(self, *_args, **_kwargs):
            raise OSError("secret-et-chemin-synthetiques")

    with pytest.raises(t.TeleversementLotErreur) as erreur:
        t.promouvoir_session_televersement_objets(config, token, ClientPanne())
    trace = "".join(traceback.format_exception(erreur.value))
    assert "secret-et-chemin-synthetiques" not in trace
    dossier, manifeste = t._charger_manifeste(config, token)
    assert all("object_key" not in v for v in manifeste["fichiers"].values())
    assert all((dossier / "fichiers" / nom).read_bytes() == contenu for nom, contenu in fichiers.items())

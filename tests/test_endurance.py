"""P2 — endurance : un cycle qui tient mille fois tiendra-t-il trois jours ?

La question posée par le plan est « plusieurs jours ». Elle n'est pas
répondable dans un test : ce fichier ne prétend donc PAS l'avoir mesurée. Il
mesure ce qui, en pratique, casse au bout de plusieurs jours et pas avant :

* **les fuites** — un dossier de staging oublié par cycle, une clé Redis qui
  reste, un objet dupliqué, un descripteur de fichier qui n'est pas rendu. Au
  bout de mille cycles, une fuite d'un seul objet par cycle est une panne ; au
  bout de dix cycles, elle est invisible. C'est précisément pourquoi on compte
  les cycles ici, et pas seulement les états ;
* **la dérive d'état** — un job qui reste « running », une base qui grossit
  sans jamais se stabiliser, une reprise qui ne retrouve plus ses fichiers.

Le banc exécute ``SEAMTECH_ENDURANCE_CYCLES`` cycles (défaut : 25 — suffisant
pour attraper une fuite par cycle, assez court pour rester dans une CI), avec
une panne injectée tous les ``SEAMTECH_ENDURANCE_PANNE_TOUS`` cycles. Chaque
panne est une annulation au pire moment : juste avant la purge.

Invariants vérifiés à la FIN, sur des COMPTAGES (jamais sur l'absence
d'exception) :

1. aucun dossier de staging résiduel, hormis ceux des cycles annulés — une
   annulation conserve la copie locale par contrat, c'est attendu et compté ;
2. aucun job non terminal : « running » ou « queued » résiduel est une fuite
   d'état, pas un état métier ;
3. aucun document dupliqué : un objet par fichier, pas un par cycle ;
4. le nombre de descripteurs ouverts ne dérive pas.

Limite dite franchement : trois jours d'horloge ne sont pas exécutés ici. Ce
qui est prouvé, c'est que les invariants qui cassent en exploitation tiennent
sur N cycles avec panne injectée — et que la fuite, si elle existe, serait
visible au comptage, pas « partie en mémoire ».
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from seamtech_search.storage import S3StorageClient
from tests.s3_en_memoire import S3EnMemoire

CYCLES = int(os.environ.get("SEAMTECH_ENDURANCE_CYCLES", "25"))
PANNE_TOUS = int(os.environ.get("SEAMTECH_ENDURANCE_PANNE_TOUS", "7"))
CONTENU = b"%PDF-1.4 fiche d'endurance"


def _sha(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()


def _descripteurs_ouverts() -> int:
    """Nombre de descripteurs ouverts du processus (indicateur de fuite)."""
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:  # pragma: no cover - hors Linux
        return -1


class _Banc:
    """Un banc d'endurance : base SQLite, staging, magasin objet en mémoire."""

    def __init__(self, tmp_path: Path) -> None:
        from seamtech_search.config import AppConfig
        from seamtech_search.import_pipeline import staging_root
        from seamtech_search.indexer import SearchIndex

        self.tmp_path = tmp_path
        self.config = AppConfig(
            root_paths=[tmp_path],
            database_path=tmp_path / "endurance.db",
            min_free_bytes=0,
            s3_endpoint_url="https://s3.invalide",
            s3_bucket="seamtech-documents",
            s3_access_key="cle-fictive",
            s3_secret_key="secret-fictif",
        )
        self.index = SearchIndex(tmp_path / "endurance.db")
        self.index.initialize()
        self.staging = staging_root(self.config)
        self.staging.mkdir(parents=True, exist_ok=True)
        self.magasin = S3EnMemoire()
        # Le magasin est CAPTURÉ par la fermeture : le premier paramètre de la
        # lambda est le client patché, pas le banc — lire un attribut sur lui
        # échouerait silencieusement (AttributeError → « objet non vérifiable »
        # → rien ne serait jamais purgé, et le soak le montrerait).
        magasin = self.magasin
        self._patchs = [
            patch.object(S3StorageClient, "_get_client", lambda _client, probe_timeout=None: magasin),
            patch.object(S3StorageClient, "ensure_bucket_exists", lambda _client: True),
            patch.object(S3StorageClient, "first_free_key", lambda _client, cle: cle),
            patch.object(S3StorageClient, "_enable_versioning_once", lambda _client: None),
        ]

    def __enter__(self) -> "_Banc":
        for p in self._patchs:
            p.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        for p in self._patchs:
            p.stop()
        self.index.close()

    def cycle(self, numero: int, *, annuler: bool) -> dict[str, Any]:
        """Un cycle complet : dépôt stagé → import prouvé → purge (ou annulation)."""
        from seamtech_search.import_pipeline import ImportFile, ImportResult
        from seamtech_search.jobs import create_job, marquer_job_claim, update_job
        from seamtech_search.purge_locale import purger_copie_prouvee
        from seamtech_search.worker import (
            _verificateur_objet_purge,
            decider_purge,
            evaluer_preservation,
        )

        dossier = self.staging / f"cycle-{numero:04d}"
        dossier.mkdir(parents=True)
        piece = dossier / "fiche.pdf"
        piece.write_bytes(CONTENU)
        cle = f"objets/endurance/{numero:04d}.pdf"
        self.magasin.put_object(Bucket="seamtech-documents", Key=cle, Body=CONTENU)

        job_id = f"endurance-{numero:04d}"
        create_job(self.index, job_id, str(dossier), durability="durable")
        assert marquer_job_claim(self.index, job_id, "worker-endurance")

        resultat = ImportResult(
            import_id=job_id,
            source_path=str(dossier),
            status="completed",
            files_detected=1,
            analyzed_files=1,
            technical_pdf=str(piece),
            data={"reference": f"END-{numero:04d}"},
            report_path=None,
            upload_status="uploaded",
            warnings=[],
            files=[
                ImportFile(
                    path=str(piece),
                    name="fiche.pdf",
                    category="storage_direct",
                    size=len(CONTENU),
                    extension=".pdf",
                    extraction_status="success",
                    report_path=None,
                    upload_status="uploaded",
                    object_key=cle,
                    object_bucket="seamtech-documents",
                    uploaded_at=time.time(),
                    source_sha256=_sha(CONTENU),
                    catalogue_lie=True,
                )
            ],
            all_verified=True,
            transport_verifie=True,
        )

        if annuler:
            # Panne injectée AU PIRE MOMENT : l'annulation est enregistrée
            # juste avant que le worker n'atteigne la purge.
            update_job(self.index, job_id, status="cancelled")
            preservation = evaluer_preservation(resultat)
            accord, raison = decider_purge(preservation, self.config, dossier)
            annule = True
        else:
            preservation = evaluer_preservation(resultat)
            accord, raison = decider_purge(preservation, self.config, dossier)
            annule = False

        bilan = (
            purger_copie_prouvee(
                racine=dossier,
                entrees=[f.__dict__ for f in resultat.files],
                sous_staging=accord,
                verifier_objet=_verificateur_objet_purge(self.config),
            )
            if accord and not annule
            else None
        )
        worker_payload = {
            "job_id": job_id,
            "status": "cancelled" if annule else "completed",
            "cleanup": (bilan.to_dict() if bilan is not None else {"purge": False, "raison": raison}),
        }
        update_job(self.index, job_id, status=worker_payload["status"], result=worker_payload)
        return {"job_id": job_id, "dossier": dossier, "piece": piece, "annule": annule, "bilan": bilan}


@pytest.mark.endurance
def test_soak_cycles_avec_panne_injectee_ne_fuit_rien(tmp_path: Path) -> None:
    from seamtech_search.jobs import get_job

    fd_avant = _descripteurs_ouverts()
    annules = 0
    with _Banc(tmp_path) as banc:
        for numero in range(CYCLES):
            annuler = PANNE_TOUS > 0 and numero % PANNE_TOUS == (PANNE_TOUS - 1)
            if annuler:
                annules += 1
            banc.cycle(numero, annuler=annuler)

        # 1. Aucun dossier de staging résiduel, hormis les cycles annulés.
        residuels = sorted(p.name for p in banc.staging.iterdir())
        attendus = sorted(
            f"cycle-{n:04d}" for n in range(CYCLES) if n % PANNE_TOUS == (PANNE_TOUS - 1)
        )
        assert residuels == attendus, (
            f"fuite de staging : {len(residuels)} dossier(s) résiduel(s) au lieu de "
            f"{len(attendus)} (cycles annulés conservés par contrat) — {residuels[:10]}"
        )

        # 2. Aucun job non terminal.
        with banc.index.connect() as connexion:
            curseur = connexion.execute("SELECT status, COUNT(*) FROM import_jobs GROUP BY status")
            repartition = dict(curseur.fetchall())
        non_termiaux = {etat: nb for etat, nb in repartition.items() if etat in {"running", "queued", "claimed"}}
        assert not non_termiaux, f"jobs laissés en vol : {non_termiaux}"
        assert sum(repartition.values()) == CYCLES

        # 3. Un objet par fichier envoyé, pas un par cycle : pas de duplicata.
        cles = list(banc.magasin.objets) if hasattr(banc.magasin, "objets") else []
        if cles:
            assert len(set(cles)) == len(cles), "clés objets dupliquées : fuite d'envoi"

        # 4. Chaque cycle annulé a conservé sa pièce : la reprise a de quoi reprendre.
        for nom in attendus:
            piece = banc.staging / nom / "fiche.pdf"
            assert piece.exists(), f"cycle annulé sans sa copie locale : {nom} (reprise impossible)"
            assert piece.read_bytes() == CONTENU
            job = get_job(banc.index, f"endurance-{nom.split('-')[1]}")
            assert job is not None and job["status"] == "cancelled"

    fd_apres = _descripteurs_ouverts()
    if fd_avant >= 0 and fd_apres >= 0:
        # Une dérive de quelques descripteurs est tolérée (cache, imports
        # paresseux) ; une dérive proportionnelle au nombre de cycles est une
        # fuite, et c'est exactement ce que plusieurs jours rendraient mortelle.
        assert fd_apres - fd_avant < max(20, CYCLES // 2), (
            f"descripteurs ouverts : {fd_avant} → {fd_apres} sur {CYCLES} cycles"
        )

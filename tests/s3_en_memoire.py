"""Magasin d'objets S3 en mémoire, fidèle là où le code l'interroge réellement.

Pourquoi un magasin plutôt qu'un ``MagicMock`` : depuis que la vérification
après envoi relit les OCTETS et recalcule l'empreinte, un double qui se contente
de répondre « oui, l'objet existe » ne teste plus le comportement réel. Ici les
octets sont conservés, donc :

* un envoi tronqué ou altéré est **détecté** par la vérification ;
* une métadonnée fournie par l'application reste intacte même si le corps a été
  corrompu — ce qui permet de prouver que la métadonnée seule ne prouve rien.

Ce module n'ouvre aucun socket (RG14) : tout est en mémoire.
"""

from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any


class CorpsEnMemoire(BytesIO):
    """Corps d'objet minimal : ``read`` (utilisé par la vérification) et
    ``close``. Les mocks boto3 renvoient un objet de ce genre."""

    def read(self, taille: int = -1) -> bytes:  # noqa: D102
        return super().read(taille)

    def iter_chunks(self, chunk_size: int = 1024):
        while bloc := self.read(chunk_size):
            yield bloc


class S3EnMemoire:
    """S3-compatible en mémoire pour ``upload_file``/``head_object``/``get_object``.

    ``ETag`` : MD5 pour un objet « normal », valeur multipart (avec ``-N``) pour
    un objet déposé en plusieurs parties — c'est exactement le piège que la
    vérification doit ignorer.
    """

    def __init__(self) -> None:
        self.objets: dict[str, dict[str, Any]] = {}
        self.envois: list[dict[str, Any]] = []

    # --- écriture ---------------------------------------------------------
    def upload_file(
        self,
        Filename: str,  # noqa: N803 - signature boto3
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        ExtraArgs: dict[str, Any] | None = None,  # noqa: N803
    ) -> None:
        with open(Filename, "rb") as flux:
            donnees = flux.read()
        self._ecrire(Key, donnees, ExtraArgs or {})
        self.envois.append({"key": Key, "octets": len(donnees), "extra": ExtraArgs or {}})

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        # Écriture conditionnelle du service (If-None-Match: *) : le double doit
        # refuser une clé occupée EXACTEMENT comme S3 le fait (412), sinon la
        # restauration croirait créer sans écraser alors qu'elle écrase.
        if kwargs.get("IfNoneMatch") == "*" and kwargs.get("Key") in self.objets:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": "At least one of the pre-conditions you specified did not hold"}},
                "PutObject",
            )
        corps = kwargs.get("Body") or b""
        # boto3 accepte des octets, un flux ouvert ou un itérable : le double
        # doit en faire autant (une restauration envoie un flux de fichier).
        donnees = corps.read() if hasattr(corps, "read") else bytes(corps)
        self._ecrire(kwargs["Key"], donnees, kwargs)
        self.envois.append({"key": kwargs["Key"], "octets": len(donnees), "extra": kwargs})
        return {"ETag": self.objets[kwargs["Key"]]["entete"]["ETag"]}

    def _ecrire(self, cle: str, donnees: bytes, options: dict[str, Any]) -> None:
        metadonnees = dict(options.get("Metadata") or {})
        etag = hashlib.md5(donnees).hexdigest()  # noqa: S324 - ETag S3, pas de la sécurité
        entete: dict[str, Any] = {
            "ContentLength": len(donnees),
            "Metadata": metadonnees,
            "ETag": f'"{etag}"',
            "ContentType": options.get("ContentType"),
        }
        self.objets[cle] = {"donnees": donnees, "entete": entete}

    def head_object(self, Bucket: str, Key: str, ChecksumMode: str | None = None) -> dict[str, Any]:  # noqa: N803
        from botocore.exceptions import ClientError

        if Key not in self.objets:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return dict(self.objets[Key]["entete"])

    def get_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:  # noqa: N803
        from botocore.exceptions import ClientError

        if Key not in self.objets:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")
        donnees = self.objets[Key]["donnees"]
        entete = dict(self.objets[Key]["entete"])
        plage = kwargs.get("Range")
        if plage:
            import re

            match = re.fullmatch(r"bytes=(\d+)-(\d+)", plage)
            if match is None or int(match.group(1)) >= len(donnees):
                raise ClientError({"Error": {"Code": "InvalidRange", "Message": "Invalid Range"}}, "GetObject")
            debut, fin = int(match.group(1)), min(int(match.group(2)), len(donnees) - 1)
            entete["ContentRange"] = f"bytes {debut}-{fin}/{len(donnees)}"
            donnees = donnees[debut:fin + 1]
            entete["ContentLength"] = len(donnees)
        return {"Body": CorpsEnMemoire(donnees), **entete}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:  # noqa: N803
        """Téléchargement vers un fichier local (chemin « cache froid » de l'API)."""
        from botocore.exceptions import ClientError

        if Key not in self.objets:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "Not Found"}}, "GetObject")
        destination = Path(Filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objets[Key]["donnees"])

    def delete_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        self.objets.pop(Key, None)
        return {}

    def get_paginator(self, nom: str):  # noqa: ANN201 - double de test
        magasin = self

        class _Paginateur:
            def paginate(self, Bucket: str, Prefix: str = "", **_: Any):  # noqa: N803, ANN201
                cles = [{"Key": cle} for cle in sorted(magasin.objets) if cle.startswith(Prefix)]
                yield {"Contents": cles}

        assert nom == "list_objects_v2", nom
        return _Paginateur()

    # --- helpers de test --------------------------------------------------
    def corrompre(self, cle: str, *, tronquer_a: int | None = None, octets: bytes | None = None) -> None:
        """Altère le corps stocké SANS toucher aux métadonnées.

        C'est précisément le scénario qu'une comparaison de métadonnées ne peut
        pas voir : l'en-tête déclare la bonne empreinte, le corps ne correspond
        plus.
        """
        entree = self.objets[cle]
        if octets is not None:
            entree["donnees"] = octets
        elif tronquer_a is not None:
            entree["donnees"] = entree["donnees"][:tronquer_a]
        entree["entete"]["ContentLength"] = len(entree["donnees"])

    def ajouter_checksum_serveur(self, cle: str, *, type_checksum: str = "FULL_OBJECT") -> None:
        """Simule le checksum calculé par le fournisseur sur l'objet stocké."""
        donnees = self.objets[cle]["donnees"]
        self.objets[cle]["entete"]["ChecksumSHA256"] = base64.b64encode(
            hashlib.sha256(donnees).digest()
        ).decode("ascii")
        self.objets[cle]["entete"]["ChecksumType"] = type_checksum

    def journal(self) -> str:
        return json.dumps(self.envois, ensure_ascii=False)

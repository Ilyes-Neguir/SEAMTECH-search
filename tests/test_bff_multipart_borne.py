"""Les proxys multipart du BFF ne doivent jamais tamponner un envoi entier.

`req.formData()` matérialise le corps complet avant même que le backend
puisse appliquer ses bornes : un compte autorisé pouvait donc exercer une
pression mémoire disproportionnée sur le processus Next.js. Le contrat est
un relais en flux, compté et borné, avec le type multipart d'origine.
"""

from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
ROUTES_MULTIPART = (
    "frontend/app/api/imports/upload/route.ts",
    "frontend/app/api/imports/dossier/televersements/[jeton]/fichiers/route.ts",
    "frontend/app/api/gabarits/brouillon/from-pdf/route.ts",
)


def _lire(relatif: str) -> str:
    return (RACINE / relatif).read_text(encoding="utf-8")


def test_les_envois_multipart_sont_relayes_en_flux_borne() -> None:
    aide = _lire("frontend/lib/backend.ts")
    assert "export async function proxyMultipartBorne" in aide
    assert "req.body" in aide and "TransformStream" in aide
    assert 'duplex: "half"' in aide
    assert 'req.headers.get("content-length")' in aide

    for relatif in ROUTES_MULTIPART:
        source = _lire(relatif)
        assert "proxyMultipartBorne(" in source and "req" in source, relatif
        assert "req.formData()" not in source, f"{relatif} tamponne encore tout l'envoi"

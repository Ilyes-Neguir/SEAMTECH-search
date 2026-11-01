"""Les proxys multipart du BFF ne doivent jamais tamponner un envoi entier.

`req.formData()` matérialise le corps complet avant même que le backend
puisse appliquer ses bornes : un compte autorisé pouvait donc exercer une
pression mémoire disproportionnée sur le processus Next.js. Le contrat est
un relais en flux, compté et borné, avec le type multipart d'origine.

Le relais vit dans `frontend/lib/relais-multipart.ts` (`relayerMultipart`) :
une seule implémentation, suivie par les routes ci-dessous et par les tests
TypeScript `frontend/lib/relais-multipart.test.ts`.
"""

from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
ROUTES_MULTIPART = (
    "frontend/app/api/imports/upload/route.ts",
    "frontend/app/api/imports/dossier/televersements/[jeton]/fichiers/route.ts",
    "frontend/app/api/gabarits/brouillon/from-pdf/route.ts",
)
RELAIS = "frontend/lib/relais-multipart.ts"


def _lire(relatif: str) -> str:
    return (RACINE / relatif).read_text(encoding="utf-8")


def test_les_envois_multipart_sont_relayes_en_flux_borne() -> None:
    aide = _lire(RELAIS)
    assert "export async function relayerMultipart" in aide
    # Flux réel : le corps n'est jamais matérialisé, il est compté au fil de
    # l'eau et interrompu dès le franchissement de la borne.
    assert "req.body" in aide and "TransformStream" in aide
    assert 'duplex: "half"' in aide
    assert 'req.headers.get("content-length")' in aide
    # Bornes et gardes mesurables : refus annoncé, refus compté, inactivité,
    # concurrence par session.
    for garde in ("413", "ErreurTropGrand", "ErreurInactivite", "RELAIS_SIMULTANES_MAX"):
        assert garde in aide, f"garde absente du relais : {garde}"

    for relatif in ROUTES_MULTIPART:
        source = _lire(relatif)
        assert "relayerMultipart(" in source and "req" in source, relatif
        # Un commentaire peut citer `req.formData()` pour expliquer le contrat ;
        # ce qui est interdit, c'est de l'appeler.
        assert not re.search(r"(?:await\s+|=|\()\s*req\.formData\(", source), (
            f"{relatif} tamponne encore tout l'envoi"
        )


def test_une_seule_implementation_de_relais_multipart() -> None:
    """Un doublon de relais finirait par diverger : une seule source de vérité."""
    doublons = [
        str(chemin.relative_to(RACINE))
        for chemin in RACINE.glob("frontend/**/*.ts*")
        if "node_modules" not in chemin.parts
        and chemin.is_file()
        and "proxyMultipartBorne" in chemin.read_text(encoding="utf-8")
    ]
    assert doublons == [], f"ancien relais encore référencé : {doublons}"

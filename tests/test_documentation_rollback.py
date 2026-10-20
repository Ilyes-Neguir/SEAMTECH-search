"""Garde-fou de la consigne de rollback Redis (revue Lot 0a, blocage 2).

La consigne publiée dans ``docs/DEPLOYMENT.md`` disait de nettoyer les clés de
session avec un scan sur ``seamtech:*``. Ce préfixe couvre AUSSI la file
durable, les battements de cœur, les jobs et les compteurs de débit : suivre la
consigne détruisait du travail accepté ou récupérable. Ce test lit le document
(il n'ouvre aucune connexion, RG14) et refuse tout retour en arrière :

1. chaque ``redis-cli --scan --pattern '<x>'`` du document doit viser l'une des
   deux seules familles de clés du Lot 0a ;
2. les familles protégées doivent être NOMMÉES dans la section de rollback,
   pour qu'un opérateur sache ce qu'il ne doit pas toucher.
"""

from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "docs" / "DEPLOYMENT.md"

#: Les seules clés que le Lot 0a possède.
FAMILLES_SESSIONS = {"seamtech:session:*", "seamtech:user_sessions:*"}

#: Tout le reste doit survivre à un rollback.
FAMILLES_PROTEGEES = (
    "seamtech:queue:",
    "seamtech:processing:",
    "seamtech:retry:",
    "seamtech:deadletter:",
    "seamtech:claim:",
    "seamtech:cancel:",
    "seamtech:job:",
    "seamtech:heartbeat:",
    "seamtech:worker:",
    "seamtech:ratelimit:",
)


def _section_rollback() -> str:
    texte = DOC.read_text(encoding="utf-8")
    debut = texte.find("**Rolling back Lot 0a.**")
    assert debut != -1, "la section « Rolling back Lot 0a » doit exister"
    fin = texte.find("\n## ", debut + 10)
    return texte[debut : fin if fin != -1 else len(texte)]


def test_le_rollback_ne_supprime_que_les_cles_de_session() -> None:
    section = _section_rollback()
    motifs = set(re.findall(r"--pattern '([^']+)'", section))
    assert motifs == FAMILLES_SESSIONS, (
        "seuls les deux préfixes du Lot 0a peuvent être scannés/supprimés ; "
        f"trouvé : {sorted(motifs)}"
    )


def test_le_rollback_nomme_les_familles_protegees() -> None:
    section = _section_rollback()
    manquantes = [prefixe for prefixe in FAMILLES_PROTEGEES if prefixe not in section]
    assert manquantes == [], (
        "la consigne doit nommer les familles à NE PAS toucher (file durable, "
        f"jobs, workers, débit) ; absentes : {manquantes}"
    )
    assert "**not** part of Lot 0a" in section, (
        "le document doit dire explicitement que ces clés ne font pas partie du Lot 0a"
    )

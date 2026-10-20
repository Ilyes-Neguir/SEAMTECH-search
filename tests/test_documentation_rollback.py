"""Garde-fou de la consigne de rollback Redis (revue Lot 0a, blocages 2 et 3).

La consigne publiée dans ``docs/DEPLOYMENT.md`` disait de nettoyer les clés de
session avec un scan sur ``seamtech:*``. Ce préfixe couvre AUSSI la file
durable, les battements de cœur, les jobs et les compteurs de débit : suivre la
consigne détruisait du travail accepté ou récupérable. Puis elle a été corrigée
sur les deux seuls préfixes du Lot 0a, mais sans authentification — alors que
Compose démarre Redis avec ``--requirepass`` : la commande échoue en ``NOAUTH``.
Et la corriger avec ``-a "$REDIS_PASSWORD"`` aurait mis le secret dans la ligne
de commande, donc dans ``ps`` et l'historique du shell.

Ce module lit le document (aucune connexion, RG14) et refuse ces trois
régressions. Le test exécutable de la commande — sur un vrai Redis protégé par
mot de passe — vit dans ``tests/test_rollback_redis_auth.py`` (marqueur
``session_redis``, car il démarre son propre serveur).
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


def _bloc_bash(section: str) -> str:
    bloc = re.search(r"```bash\n(.*?)```", section, re.DOTALL)
    assert bloc is not None, "la consigne doit contenir un bloc ```bash exécutable"
    return bloc.group(1)


def test_le_rollback_ne_supprime_que_les_cles_de_session() -> None:
    section = _section_rollback()
    motifs = set(re.findall(r'"(seamtech:[^"]+)"', section))
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


def test_le_rollback_sauthentifie_sans_mot_de_passe_en_argument() -> None:
    """``REDISCLI_AUTH`` dans l'environnement, jamais ``-a`` sur la ligne.

    Compose démarre Redis avec ``--requirepass`` : sans authentification la
    consigne échoue (``NOAUTH``). Avec ``-a``, le secret devient visible dans
    ``ps`` et dans l'historique — exactement ce qu'il faut éviter.
    """
    section = _section_rollback()
    bloc = _bloc_bash(section)

    assert 'export REDISCLI_AUTH="$REDIS_PASSWORD"' in bloc, (
        "le bloc doit exporter REDISCLI_AUTH depuis l'environnement du conteneur "
        "(le mot de passe n'est alors jamais un argument)"
    )
    assert re.search(r"redis-cli\s+(-a|--pass)\b", bloc) is None, (
        "aucune invocation de redis-cli ne doit recevoir le mot de passe en argument"
    )
    assert "$REDIS_PASSWORD" in bloc, (
        "le bloc doit lire REDIS_PASSWORD fourni par Compose dans le conteneur"
    )
    # Le secret n'est jamais écrit en clair : uniquement une variable.
    assert not re.search(r"REDISCLI_AUTH=[\"']?[A-Za-z0-9+/=_-]{12,}", bloc), (
        "le bloc ne doit contenir aucun secret littéral"
    )
    assert "docker compose exec -T redis" in bloc, (
        "la commande doit s'exécuter DANS le conteneur redis, où REDIS_PASSWORD "
        "est exporté par Compose"
    )

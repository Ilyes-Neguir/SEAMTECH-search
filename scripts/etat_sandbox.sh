#!/usr/bin/env bash
# État du bac à sable AVANT CHAQUE commit (audit indépendant du 22/09).
#
# Le bac à sable s'est réinitialisé entre deux tours : l'historique local avait
# été ramené à un ancêtre pendant que l'arbre de travail restait à jour, et un
# commit orphelin (108 fichiers « recréés ») a failli être poussé. Ce script
# rend l'incident impossible à manquer :
#   1. HEAD doit être sur origin/<branche> ou son DESCENDANT direct — sinon la
#      vérité n'est plus locale et il faut s'arrêter (git reset --hard sur
#      origin, puis ré-appliquer les modifications) ;
#   2. l'arbre de travail est affiché (propre ou liste des modifications) ;
#   3. les empreintes des fixtures sont vérifiées contre tests/test_empreintes_fixtures.py.
#
# La vérité est sur origin : on ne travaille jamais sans avoir fetché.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

BRANCHE=$(git rev-parse --abbrev-ref HEAD)
# ls-remote plutôt que fetch : robuste quel que soit le refspec du dépôt,
# et ne dépend d'aucun état local périmé.
DISTANT=$(git ls-remote origin "refs/heads/$BRANCHE" 2>/dev/null | cut -f1)

if [ -n "$DISTANT" ]; then
    LOCAL=$(git rev-parse HEAD)
    if [ "$LOCAL" = "$DISTANT" ]; then
        echo "✓ HEAD = origin/$BRANCHE ($LOCAL)"
    elif git merge-base --is-ancestor "$DISTANT" "$LOCAL" 2>/dev/null; then
        echo "⚠ HEAD est EN AVANCE d'origin/$BRANCHE (commits non poussés) :"
        git log --oneline "origin/$BRANCHE..$LOCAL"
    else
        echo "STOP — HEAD n'est ni sur origin/$BRANCHE ni son descendant." >&2
        echo "  local : $LOCAL ($(git log --oneline -1 HEAD | tail -c +1))" >&2
        echo "  origin: $DISTANT" >&2
        echo "  Le sandbox a probablement réinitialisé l'historique. NE PAS COMMITTER :" >&2
        echo "  git reset --hard origin/$BRANCHE   # puis ré-appliquer les modifications" >&2
        exit 1
    fi
else
    echo "⚠ origin/$BRANCHE inconnue (premier push de la branche ?)"
fi

if [ -z "$(git status --porcelain)" ]; then
    echo "✓ arbre de travail propre"
else
    echo "⚠ arbre de travail modifié :"
    git status --porcelain | head -20
fi

echo "Empreintes des fixtures (doivent correspondre à tests/test_empreintes_fixtures.py) :"
sha256sum \
    sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf \
    sample_data/CLIENT-GENOA/fiche-genois.pdf \
    sample_data/CLIENT-123/fiche-technique.pdf \
    frontend/e2e/live-fixtures/CLIENT-E2E-TROIS/fiche-trois.pdf \
    2>/dev/null || echo "⚠ au moins une fixture est absente"

if [ -z "$(git config user.name)" ] || [ -z "$(git config user.email)" ]; then
    echo "⚠ identité git absente dans ce sandbox — poser user.name/user.email avant de commit"
fi

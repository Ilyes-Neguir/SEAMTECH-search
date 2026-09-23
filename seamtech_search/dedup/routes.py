"""Routes de détection de doublons — Lot L.1.

- ``GET  /fiches/doublons``       : groupes exacts + paires probables (lecture seule)
- ``POST /fiches/doublons/scan``  : relance un scan ; ``appliquer=false`` par DÉFAUT
- ``GET  /fiches/{code}/doublons``: liens d'une fiche (le bandeau de /validation)

Chemin retenu : ``/fiches/doublons`` plutôt que ``/recherche/doublons`` — un
doublon est une propriété des FICHES, pas de la recherche, et l'écran qui les
affiche (``/validation``) vit déjà sous la ressource ``fiches``. Aucun conflit
de routage : ``GET /fiches/{code}`` n'existe pas (seuls ``/fiches`` et les
sous-chemins ``/fiches/{code}/…`` sont déclarés).

Aucune de ces routes n'efface, ne fusionne ni ne change un statut. ``POST
…/scan`` écrit des LIGNES DE LIEN seulement si ``appliquer`` vaut explicitement
``true``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Header, HTTPException

from seamtech_search.dedup.detection import (
    SEUIL_PROBABLE_DEFAUT,
    _exiger_postgres,
    doublons_exacts,
    doublons_par_code,
    doublons_probables,
    enregistrer_liens,
    liens_depuis_exacts,
    liens_depuis_probables,
    liens_dune_fiche,
)


def enregistrer_routes_dedup(app: Any, index: Any, config: Any, verifier_auth: Any) -> None:  # noqa: ANN401
    def _id_fiche_du_code(cursor: Any, code: str) -> int:  # noqa: ANN401 - curseur psycopg2 réel
        cursor.execute("SELECT id_fiche FROM fiche WHERE code = %s", (code,))
        ligne = cursor.fetchone()
        if ligne is None:
            raise HTTPException(status_code=404, detail=f"Fiche « {code} » inconnue.")
        return int(ligne[0])

    @app.get("/fiches/doublons")
    def route_doublons(
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
        seuil: float = SEUIL_PROBABLE_DEFAUT,
    ) -> dict[str, Any]:
        """Groupes de doublons EXACTS (empreinte SHA-256) + paires PROBABLES.

        LECTURE SEULE : cette route ne crée jamais de lien. Pour en enregistrer,
        ``POST /fiches/doublons/scan`` avec ``appliquer=true``.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        groupes = doublons_exacts(index)
        probables = doublons_probables(index, seuil=seuil)
        return {
            "groupes_exacts": groupes,
            "nb_groupes_exacts": len(groupes),
            "probables": probables.en_dicts(),
            "nb_probables": len(probables),
            "trigrammes_disponibles": bool(probables.trigrammes_disponibles),
            "critere_probables": probables.critere(),
            "seuil": float(seuil),
            "rappel": (
                "PROPOSITIONS uniquement : rien n'est supprimé, rien n'est fusionné, "
                "aucun statut n'est modifié. La décision reste humaine."
            ),
        }

    @app.post("/fiches/doublons/scan")
    def route_scan(
        corps: dict[str, Any] | None = None,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Relance le scan. ``appliquer`` vaut ``false`` par défaut.

        Corps : ``{"seuil": 0.55, "appliquer": false}``. Sans ``appliquer=true``,
        la route ne fait que COMPTER ce qu'un scan écrit — c'est le dry-run, et
        c'est le défaut : écrire des liens ne doit jamais être un effet de bord
        d'un appel de consultation.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        corps = corps or {}
        seuil = float(corps.get("seuil", SEUIL_PROBABLE_DEFAUT))
        appliquer = bool(corps.get("appliquer", False))

        groupes = doublons_exacts(index)
        probables = doublons_probables(index, seuil=seuil)
        liens = liens_depuis_exacts(groupes) + liens_depuis_probables(probables)
        resume = enregistrer_liens(index, liens, dry_run=not appliquer)
        return {
            "seuil": seuil,
            "dry_run": not appliquer,
            "trigrammes_disponibles": bool(probables.trigrammes_disponibles),
            "critere_probables": probables.critere(),
            "groupes_exacts": len(groupes),
            "paires_probables": len(probables),
            **resume,
        }

    @app.get("/fiches/{code}/doublons")
    def route_doublons_dune_fiche(
        code: str,
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Liens de doublon d'une fiche — ce que le bandeau affiche.

        Rend ``{code, liens: [{type, score, code_autre, statut_autre, …}], nb}``.
        Pas de bouton « fusionner » : l'appelant reçoit une liste, jamais une
        action.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        with index.connect() as connexion:
            with connexion.cursor() as cursor:
                id_fiche = _id_fiche_du_code(cursor, code)
        liens = liens_dune_fiche(index, id_fiche)
        return {"code": code, "id_fiche": id_fiche, "liens": liens, "nb": len(liens)}

    @app.post("/validation/doublons")
    def route_doublons_par_codes(
        corps: dict[str, Any],
        token: Annotated[str | None, Header(alias="X-SEAMTECH-TOKEN")] = None,
    ) -> dict[str, Any]:
        """Liens pour une LISTE de codes — une seule requête pour l'écran /validation.

        Corps : ``{"codes": ["0701-GV-001", …]}``.
        """
        verifier_auth(config, token)
        _exiger_postgres(index)
        codes = [str(c) for c in (corps.get("codes") or [])]
        if not codes:
            return {"par_code": {}}
        return {"par_code": doublons_par_code(index, codes)}

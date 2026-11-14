#!/usr/bin/env python3
"""A.3 — Ce que les instruments 1.1 → 1.5 apportent, compté et non estimé.

Pourquoi ce script existe : le mandat demande un tableau avant/après PAR
FAMILLE D'ANOMALIE, et « le vrai comptage, pas une estimation ». Une
estimation dirait « les contrôles attrapent des erreurs ». Un comptage dit
combien, lesquelles, et sur combien de fiches.

**Méthode.** Les mêmes PDF sont déposés deux fois, dans deux bases neuves :

  - **AVANT** : ``evaluer_anomalies`` est appelée SANS les instruments 1.2
    (référentiel bateaux), 1.3 (références constatées) et 1.4 (gabarit).
    C'est exactement le comportement documenté de la fonction : « Sans lui,
    le contrôle ne s'applique pas — il ne devine pas ce qu'il n'a pas
    reçu. » On ne modifie donc pas le produit : on retire ce que les
    instruments lui fournissent.
  - **APRÈS** : le pipeline complet, instruments actifs.

La différence entre les deux colonnes EST l'apport des instruments. Elle est
mesurée sur les fiches réelles, pas sur un jeu monté.

**Ce que ce script ne fait pas.** Il n'écrit rien dans le dépôt, ne modifie
aucun seuil et ne touche pas aux fiches. Il mesure et il imprime.

.. code-block:: shell

    python scripts/mesurer_apport_instruments.py --corpus /chemin/vers/corpus

Codes de retour : 0 = mesure produite, 2 = corpus introuvable ou erreur.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Familles d'anomalies suivies. Les codes viennent des lots 1.3, 1.4 et 1.5 ;
#: « aucun » n'est pas une famille mais le constat qu'une fiche est passée sans
#: rien lever, et il compte : c'est le dénominateur silencieux.
FAMILLES = (
    "bateau_hors_referentiel",   # Lot 1.2
    "reference_discordante",     # Lot 1.3 — NON bloquante
    "champ_obligatoire_manquant",  # Lot 1.4 — BLOQUANTE
    "champ_manquant",            # Lot 1.5
    "cote_hors_plage",           # Lot 1.5
    "cotes_incoherentes",        # Lot 1.5
    "surface_incoherente",       # Lot 1.5
    "client_invraisemblable",    # Lot 1.5
)


def _nouvelle_base(nom: str, url_admin: str) -> str:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = psycopg2.connect(url_admin)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nom)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nom)))
    admin.close()
    from urllib.parse import urlsplit

    parties = urlsplit(url_admin)
    return f"{parties.scheme}://{parties.netloc}/{nom}?{parties.query}"


def _compter_anomalies(url: str) -> dict[str, int]:
    """Anomalies par code, lues sur les fiches réellement écrites."""
    import psycopg2

    with psycopg2.connect(url) as cx:
        with cx.cursor() as cur:
            cur.execute("SELECT code, count(*) FROM fiche_anomalie GROUP BY code")
            par_code = dict(cur.fetchall())
            cur.execute("SELECT count(DISTINCT id_fiche) FROM fiche")
            nb_fiches = int(cur.fetchone()[0])
            cur.execute("SELECT count(DISTINCT id_fiche) FROM fiche_anomalie")
            fiches_avec_anomalie = int(cur.fetchone()[0])
    resultat = {famille: int(par_code.get(famille, 0)) for famille in FAMILLES}
    resultat["_fiches"] = nb_fiches
    resultat["_fiches_avec_anomalie"] = fiches_avec_anomalie
    resultat["_fiches_sans_anomalie"] = nb_fiches - fiches_avec_anomalie
    resultat["_total_anomalies"] = int(sum(par_code.values()))
    # codes inattendus : on les montre plutôt que de les perdre
    for code, n in par_code.items():
        if code not in FAMILLES:
            resultat[f"_autre:{code}"] = int(n)
    return resultat


def _deposer_corpus(url: str, corpus: Path, *, instruments: bool) -> tuple[int, int]:
    """Dépose tout le corpus ; rend (réussis, échecs).

    ``instruments=False`` neutralise l'apport des lots 1.2, 1.3 et 1.4 en
    appelant ``evaluer_anomalies`` sans ce qu'ils fournissent.
    """
    from seamtech_search.fiches import anomalies as module_anomalies
    from seamtech_search.fiches.depot import deposer_dossier
    from seamtech_search.fiches.gabarits import initialiser_gabarits
    from seamtech_search.indexer import SearchIndex

    ix = SearchIndex(Path(f"/tmp/a3-{instruments}.db"), url)
    ix.initialize()
    ix.run_migrations()
    initialiser_gabarits(ix)

    originale = module_anomalies.evaluer_anomalies

    if not instruments:
        def evaluer_sans_instruments(fiche, **kwargs):  # noqa: ANN001, ANN202, ARG001
            # On écarte ce que les instruments fournissent : la fonction est
            # alors exactement dans son état d'avant eux.
            return originale(fiche)

        module_anomalies.evaluer_anomalies = evaluer_sans_instruments
        # les appelants internes doivent voir la version neutralisée
        import seamtech_search.fiches.extraction as extraction

        extraction.evaluer_anomalies = evaluer_sans_instruments

    reussis = echecs = 0
    try:
        for dossier in sorted(p for p in corpus.rglob("*") if p.is_dir() and any(x.is_file() for x in p.iterdir())):
            try:
                res = deposer_dossier(ix, dossier)
            except Exception:
                echecs += 1
                continue
            if res.get("statut") not in {"traite", "deja_traite"}:
                echecs += 1
            else:
                reussis += 1
    finally:
        module_anomalies.evaluer_anomalies = originale
        if not instruments:
            import seamtech_search.fiches.extraction as extraction

            extraction.evaluer_anomalies = originale
        ix.close()
    return reussis, echecs


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--corpus", required=True, help="Dossier racine du corpus réel")
    parseur.add_argument("--url-admin", default=None, help="URL PostgreSQL d'administration")
    parseur.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parseur.parse_args()

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        print(f"Corpus introuvable : {corpus}", file=sys.stderr)
        return 2

    if args.url_admin is None:
        # Aucun chemin de cluster en dur : il vient de l'environnement, ou
        # d'un emplacement standard s'il n'est pas précisé. Le garde-fou
        # « aucun chemin de production codé en dur » l'exige, et il a raison :
        # un chemin écrit ici casserait sur tout autre poste.
        dossier = os.environ.get("SEAMTECH_PGDATA") or os.path.join(
            tempfile.gettempdir(), "seamtech-pgdata"
        )
        try:
            import pgserver

            url_admin = pgserver.get_server(Path(dossier)).get_uri()
        except Exception:
            print(
                "URL d'administration requise (--url-admin, ou SEAMTECH_PGDATA "
                "pour un cluster local) : pgserver indisponible.",
                file=sys.stderr,
            )
            return 2
    else:
        url_admin = args.url_admin

    url_avant = _nouvelle_base("a3_avant", url_admin)
    url_apres = _nouvelle_base("a3_apres", url_admin)

    print("Dépôt SANS les instruments 1.2 / 1.3 / 1.4 …")
    r_avant, e_avant = _deposer_corpus(url_avant, corpus, instruments=False)
    print(f"  {r_avant} déposé(s), {e_avant} échec(s)")

    print("Dépôt AVEC les instruments 1.1 → 1.5 …")
    r_apres, e_apres = _deposer_corpus(url_apres, corpus, instruments=True)
    print(f"  {r_apres} déposé(s), {e_apres} échec(s)")

    avant = _compter_anomalies(url_avant)
    apres = _compter_anomalies(url_apres)

    if args.json:
        print(json.dumps({"avant": avant, "apres": apres}, ensure_ascii=False, indent=2))
        return 0

    print()
    print("=" * 74)
    print("A.3 — apport des instruments, par famille d'anomalie")
    print("=" * 74)
    print(f"{'famille':<30} {'AVANT':>8} {'APRÈS':>8} {'écart':>8}")
    print("-" * 74)
    for famille in FAMILLES:
        a, b = avant[famille], apres[famille]
        ecart = b - a
        signe = f"{ecart:+d}" if ecart else "="
        print(f"{famille:<30} {a:>8} {b:>8} {signe:>8}")
    print("-" * 74)
    for cle, libelle in (
        ("_total_anomalies", "TOTAL anomalies"),
        ("_fiches", "fiches déposées"),
        ("_fiches_avec_anomalie", "fiches avec ≥1 anomalie"),
        ("_fiches_sans_anomalie", "fiches sans anomalie"),
    ):
        print(f"{libelle:<30} {avant[cle]:>8} {apres[cle]:>8} {apres[cle] - avant[cle]:>+8}")
    autres = [k for k in set(avant) | set(apres) if k.startswith("_autre:")]
    if autres:
        print("-" * 74)
        for k in sorted(autres):
            print(f"{k[1:]:<30} {avant.get(k, 0):>8} {apres.get(k, 0):>8}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

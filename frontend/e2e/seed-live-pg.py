# Seed d'une base PostgreSQL jetable pour l'e2e « validation » en conditions
# réelles (métier PostgreSQL, §17.1). Appelé par playwright.config.ts quand
# SEAMTECH_E2E_DATABASE_URL est défini (URL d'administration sur le serveur
# PostgreSQL de test). La base est créée une fois par RUN puis réutilisée par
# les rechargements de config (playwright réimporte ce module par processus) ;
# une base déjà consommée par un run précédent est recréée : l'e2e reste
# répétable sans nettoyage manuel.
#
# Toutes les décisions (réutilisation, création, raison) sont journalisées sur
# stderr — visible dans la sortie playwright — jamais d'échec silencieux.
#
# Sortie (stdout) : {"url": "postgresql://...", "nom": "e2e_xxxx"}

import fcntl
import json
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

REPO = Path(__file__).resolve().parents[2]
FICHIER_RUN = Path(__file__).resolve().parent / ".live-pg-run.json"
VERROU = Path(__file__).resolve().parent / ".live-pg-seed.lock"
NOMBRE_FICHES_ATTENDU = 3


@contextmanager
def verrou_exclusif():
    """Sérialise les chargements concurrents de la config playwright :
    sans ce verrou, deux processus peuvent se disputer la purge/création
    et le second détruirait la base du premier (observé en pratique)."""
    with VERROU.open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def base_vivante(url: str) -> bool:
    """La base répond-elle et porte-t-elle encore la table fiche ? Une base
    partiellement consommée (fiches validées au fil des tests) reste vivante :
    les chargements du MÊME run doivent la réutiliser tels quels."""
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM fiche")
            cur.fetchone()
        conn.close()
        return True
    except Exception as exc:
        print(f"Seed e2e : base {url} injoignable ({type(exc).__name__} : {exc}).", file=sys.stderr)
        return False


def main() -> None:
    admin_url = os.environ.get("SEAMTECH_E2E_DATABASE_URL")
    if not admin_url:
        print(json.dumps({"erreur": "SEAMTECH_E2E_DATABASE_URL absent"}))
        sys.exit(1)
    run_id = os.environ.get("SEAMTECH_E2E_RUN_ID", "")

    with verrou_exclusif():
        admin = psycopg2.connect(admin_url)
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

        # réutilisation au sein du MÊME run playwright : la base peut être
        # partiellement consommée par les tests déjà exécutés — on la garde.
        if run_id and FICHIER_RUN.exists():
            try:
                enregistre = json.loads(FICHIER_RUN.read_text(encoding="utf-8"))
                if enregistre.get("run_id") == run_id and base_vivante(enregistre["url"]):
                    print(
                        f"Seed e2e : run {run_id[:8]} — réutilisation de {enregistre['nom']}.",
                        file=sys.stderr,
                    )
                    print(json.dumps({"url": enregistre["url"], "nom": enregistre["nom"], "reutilisee": True}))
                    admin.close()
                    return
                print(
                    f"Seed e2e : fichier run d'un autre run ({enregistre.get('run_id', '?')[:8]} "
                    f"≠ {run_id[:8]}) — nouvelle base.",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(
                    f"Fichier run illisible ({type(exc).__name__} : {exc}) — nouvelle base.",
                    file=sys.stderr,
                )

        with admin.cursor() as cur:
            # bases e2e des exécutions précédentes : nettoyage best-effort
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname LIKE 'e2e\\_%' AND datname <> current_database()"
            )
            cur.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE 'e2e\\_%' "
                "AND datname <> current_database()"
            )
            anciennes = [r[0] for r in cur.fetchall()]
            for nom_ancien in anciennes:
                cur.execute(f'DROP DATABASE IF EXISTS "{nom_ancien}"')
            nom = f"e2e_{uuid.uuid4().hex[:8]}"
            cur.execute(f'CREATE DATABASE "{nom}"')
        admin.close()

        url = admin_url.rsplit("/", 1)[0] + f"/{nom}"

        # import tardif : ne compte que si la base existe (métier PostgreSQL)
        sys.path.insert(0, str(REPO))
        from seamtech_search.fiches import depot
        from seamtech_search.fiches.gabarits import initialiser_gabarits
        from seamtech_search.indexer import SearchIndex

        idx = SearchIndex(Path(f"/tmp/e2e-{nom}.db"), url)
        idx.initialize()
        idx.run_migrations()
        initialiser_gabarits(idx)
        for dossier in ("CLIENT-7792-SO", "CLIENT-GENOA", "CLIENT-E2E-TROIS"):
            chemin = REPO / "sample_data" / dossier
            if not chemin.exists():
                chemin = Path(__file__).resolve().parent / "live-fixtures" / dossier
            resultat = depot.deposer_dossier(idx, chemin)
            if resultat.get("statut") != "traite":
                print(
                    f"Dépôt e2e de {dossier} en échec ({resultat}) — conséquence : "
                    "l'e2e de validation manque une fiche.",
                    file=sys.stderr,
                )
                sys.exit(1)
        idx.close()

        FICHIER_RUN.write_text(json.dumps({"run_id": run_id, "url": url, "nom": nom}), encoding="utf-8")
        print(f"Seed e2e : base {nom} créée et semée (3 dossiers) pour le run {run_id[:8]}.", file=sys.stderr)
        print(json.dumps({"url": url, "nom": nom}))


if __name__ == "__main__":
    main()

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
# Lot L.1 — la fiche supplémentaire qui partage le PDF de 7792-SO. Sans elle,
# l'e2e du bandeau de doublon n'aurait rien à afficher (voir e2e/doublons.spec.ts).
CODE_DOUBLON = "7792-SO-BIS"


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


def _copier_lignes(cur, table: str, id_source: int, exclusions: tuple[str, ...], surcharges=None,
                   id_fiche: int | None = None) -> int:
    """Duplique les lignes d'une table (fiche ou table fille) en recopiant TOUTES
    les colonnes sauf les exclusions, lues dans le catalogue PostgreSQL.

    Recopier la liste des colonnes à la main ferait casser ce seed à chaque
    migration ajoutant une colonne ; le catalogue, lui, dit toujours la vérité.
    Les surcharges ajoutent des colonnes supplémentaires en tête de liste, dans
    l'ordre du dictionnaire — cibles, valeurs et paramètres sont donc construits
    dans le MÊME parcours (une inversion d'ordre écrivait le code dans la colonne
    statut, mesuré au premier essai : la fiche copiée s'appelait « a_valider »).
    """
    surcharges = surcharges or {}
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    colonnes = [r[0] for r in cur.fetchall()]
    if not colonnes:
        raise SystemExit(f"Seed e2e : table {table} introuvable — seed du doublon impossible.")

    cibles: list[str] = []
    valeurs: list[str] = []
    params: list[object] = []
    for cle, valeur in surcharges.items():
        cibles.append(f'"{cle}"')
        valeurs.append("%s")
        params.append(valeur)
    if id_fiche is not None:
        cibles.append('"id_fiche"')
        valeurs.append("%s")
        params.append(id_fiche)
    copiees = [c for c in colonnes if c not in exclusions and c not in surcharges]
    cibles += [f'"{c}"' for c in copiees]
    valeurs += [f'"{c}"' for c in copiees]
    params.append(id_source)

    cur.execute(
        f"INSERT INTO {table} ({', '.join(cibles)}) SELECT {', '.join(valeurs)} "
        f"FROM {table} WHERE id_fiche = %s RETURNING 1",
        tuple(params),
    )
    lignes = cur.fetchall()
    if table == "fiche":
        cur.execute("SELECT id_fiche FROM fiche WHERE code = %s", (surcharges.get("code"),))
        ligne = cur.fetchone()
        if ligne is None:
            raise SystemExit("Seed e2e : la fiche de doublon n'a pas été créée (copie sans effet).")
        return int(ligne[0])
    return len(lignes)


def semer_doublon(idx) -> None:
    """Ajoute une DEUXIÈME fiche partageant le PDF de 7792-SO, puis lance le scan.

    Pourquoi ce n'est pas fait par le pipeline de dépôt : le code de fiche est
    EXTRAIT du PDF (gabarit v2), donc deux fichiers identiques produisent le
    même code, et `fiche.code` est UNIQUE — le pipeline ne peut structurellement
    pas créer deux fiches pour un même PDF. Le cas réel que L.1 couvre est celui
    d'un même fichier rattaché à deux fiches, ce que l'on reproduit ici : la
    fiche bis est une COPIE de la 7792-SO (mêmes colonnes, mêmes champs
    extraits) plus une pièce jointe de MÊME empreinte SHA-256.

    C'est une donnée de TEST (base jetable e2e_*), jamais une donnée de
    production — et rien n'est fusionné : le scan est propositif, il ne crée que
    des liens (table fiche_lien).
    """
    from seamtech_search.dedup import doublons_exacts, enregistrer_liens

    with idx.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id_fiche FROM fiche WHERE code = %s", ("7792-SO",))
            ligne = cur.fetchone()
            if ligne is None:
                print(
                    "Seed e2e : fiche 7792-SO absente — le doublon exact ne peut pas être semé.",
                    file=sys.stderr,
                )
                sys.exit(1)
            id_source = int(ligne[0])
            id_doublon = _copier_lignes(cur, "fiche", id_source, exclusions=("id_fiche", "code", "statut"),
                                        surcharges={"code": CODE_DOUBLON, "statut": "a_valider"})
            # Les champs extraits : sans eux, /validation n'affiche rien pour la fiche.
            _copier_lignes(cur, "fiche_champ_extrait", id_source, exclusions=("id_champ", "id_fiche"),
                           id_fiche=id_doublon)
            # LE doublon : le MÊME fichier rattaché aux DEUX fiches (même chemin,
            # même empreinte SHA-256). Le dépôt de dossier n'écrit pas de pièce
            # jointe pour le PDF de la fiche (il n'enregistre que les fichiers
            # SECONDAIRES du dossier, role='piece_jointe' — mesuré : « pieces: 0 »
            # pour CLIENT-7792-SO) ; c'est donc le seed qui rattache explicitement
            # le PDF réel, exactement comme le fait le crawler pour un document
            # retrouvé dans deux dossiers.
            from seamtech_search.fiches.depot import empreinte_fichier

            pdf = REPO / "sample_data" / "CLIENT-7792-SO" / "fiche-7792-SO_ffab.pdf"
            sha256 = empreinte_fichier(pdf)
            taille = pdf.stat().st_size
            for id_cible in (id_source, id_doublon):
                cur.execute(
                    "INSERT INTO fiche_piece_jointe (id_fiche, chemin, role, empreinte_sha256, taille_octets) "
                    "VALUES (%s, %s, 'fiche', %s, %s) ON CONFLICT DO NOTHING",
                    (id_cible, str(pdf), sha256, taille),
                )
    # HORS du bloc de connexion : la transaction est validée, sinon le scan
    # (qui ouvre sa propre connexion) ne verrait pas les pièces insérées.
    resultat = enregistrer_liens(idx, doublons_exacts(idx))
    if resultat["liens_crees"] == 0 and resultat["liens_deja_presents"] == 0:
        print(
            "Seed e2e : AUCUN lien de doublon créé — le bandeau de /validation n'aurait rien à "
            "afficher (e2e/doublons.spec.ts échouerait).",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
            f"Seed e2e : fiche {CODE_DOUBLON} (id {id_doublon}) partage le PDF de 7792-SO — "
            f"scan propositif : {resultat['liens_crees']} lien(s) créé(s), "
            f"{resultat['liens_deja_presents']} déjà présent(s), "
            f"{resultat['fiches_concernees']} fiche(s) concernée(s).",
            file=sys.stderr,
        )


def semer_comptes(idx) -> None:
    """Crée les deux comptes nominatifs de l'e2e (Lot L.2).

    Un opérateur et un administrateur, avec des mots de passe connus du test :
    les specs peuvent alors prouver les règles réelles — un opérateur reçoit 403
    sur la gestion des comptes, un administrateur passe — sans jamais dépendre du
    compte de secours partagé.
    """
    from seamtech_search.comptes import creer_utilisateur, lister_utilisateurs

    operateur = os.environ.get("SEAMTECH_E2E_OPERATEUR_PASSWORD", "e2e-operateur-password")
    admin = os.environ.get("SEAMTECH_E2E_ADMIN_PASSWORD", "e2e-admin-password")
    existants = {compte["identifiant"] for compte in lister_utilisateurs(idx)}
    for identifiant, nom, role, mot_de_passe in (
        ("e2e-operateur", "Opérateur e2e", "operateur", operateur),
        ("e2e-admin", "Administrateur e2e", "administrateur", admin),
    ):
        if identifiant in existants:
            continue
        creer_utilisateur(idx, identifiant, nom, mot_de_passe, role)
        print(f"Seed e2e : compte nominatif « {identifiant} » ({role}) créé.", file=sys.stderr)


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
        semer_doublon(idx)
        semer_comptes(idx)
        idx.close()

        FICHIER_RUN.write_text(json.dumps({"run_id": run_id, "url": url, "nom": nom}), encoding="utf-8")
        print(f"Seed e2e : base {nom} créée et semée (3 dossiers) pour le run {run_id[:8]}.", file=sys.stderr)
        print(json.dumps({"url": url, "nom": nom}))


if __name__ == "__main__":
    main()

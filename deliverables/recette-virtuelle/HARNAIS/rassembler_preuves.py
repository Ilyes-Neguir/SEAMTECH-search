#!/usr/bin/env python3
"""Rassemble les preuves et journaux dans le livrable deliverables/recette-virtuelle/.

- PREUVES/ : fichiers de preuve machine (JSON/CSV du dépôt de recette) + captures.
- LOGS/ : journaux d'exécution TRONQUÉS quand ils sont volumineux (worker.log atteint
  des dizaines de Mo) ; chaque troncature est déclarée dans LOGS/LISEZMOI.md avec le
  nombre de lignes conservées.
Aucun secret n'est copié : les fichiers d'identifiants (rv/secrets, recette.env) sont
exclus par construction.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

RV = Path("/home/user/rv")
LIVRABLE = Path("/home/user/SEAMTECH-search/deliverables/recette-virtuelle")
PREUVES = LIVRABLE / "PREUVES"
LOGS = LIVRABLE / "LOGS"

FICHIERS_PREUVES = [
    "environnement.json",
    "config_resolue.json",
    "integrite_corpus.json",
    "integrite_corpus.csv",
    "provisionnement_stockage.json",
    "pannes_et_reprises.json",
    "recette_api.json",
    "e2e-rapport.json",
    "e2e-rapport_passe4.json",
    "e2e-rapport_passe5.json",
    "e2e-rapport_passe6.json",
    "e2e-rapport_passe8.json",
    "e2e-rapport_passe9.json",
    "essai_panne_stockage_import.json",
    "reparation_copies_locales.json",
    "verification_telechargements.json",
    "restauration_complete_independante.json",
    "import_drag_drop_complet.json",
    "changement_mot_de_passe_compte_neuf.json",
]

JOURNAUX = {
    "recette_verif.log": 0,        # 0 = intégral
    "api.log": 0,
    "api-e2e.log": 3000,          # ~50 Mo en clair : 3 000 dernières lignes
    "frontend.log": 0,
    "frontend-e2e.log": 0,
    "redis.log": 0,
    "s3.log": 0,
    "postgres.log": 0,
    "e2e_depot_passe4.log": 0,
    "e2e_depot_passe5.log": 0,
    "e2e_depot_passe6.log": 0,
    "e2e_depot_passe7.log": 0,
    "e2e_depot_passe8.log": 0,
    "e2e_depot_passe9.log": 0,
    "recette_verif_relance.log": 0,
    "pannes_final.log": 0,
    "worker.log": 4000,            # volumineux : 4 000 dernières lignes
    "build_redis.log": 200,
    "initdb.log": 200,
}

LECTURE = """# Journaux d'exécution de la recette

Ces journaux sont ceux produits par les processus réellement lancés sur la machine de
recette. Politique de troncature : les fichiers sont copiés **intégralement** sauf
`worker.log` (plusieurs dizaines de Mo) et les journaux de compilation, dont seules les
dernières lignes sont conservées — la liste exacte et le nombre de lignes figurent dans
`MANIFESTE_LOGS.json`.

Contenu :

| Fichier | Ce qu'il prouve |
| --- | --- |
| `recette_verif.log` | sortie du script de recette officiel du dépôt (19 contrôles, `controles_echoues\|aucun`) |
| `api.log`, `worker.log` | exécution de l'API et du worker de recette (migrations, dépôts, jobs) |
| `api-e2e.log`, `frontend-e2e.log`, `e2e_depot_passe4.log` … `e2e_depot_passe9.log` | passes de la suite e2e du dépôt (base PostgreSQL jetable, navigateur réel) |
| `pannes_final.log` | run définitif §8/§9 du 2026-10-12 (23/23 PASS) |
| `recette_verif_relance.log` | relance du script officiel : séquelles de l'anomalie A-07 (copies locales manquantes), avant réparation |
| `redis.log`, `s3.log`, `postgres.log`, `frontend.log` | démarrage et vie des services de la pile |
| `build_redis.log`, `initdb.log` | construction de l'environnement |

Aucun secret n'est présent dans ces journaux : les URL contenant des mots de passe sont
masquées à l'écriture (`***`) et les fichiers d'identifiants ne sont pas copiés.
"""

LECTURE_HARNAIS = """# Harnais de recette (scripts réellement exécutés)

Ces scripts sont ceux qui ont **produit** les fichiers de `PREUVES/` et `LOGS/`. Ils sont
versionnés avec le livrable pour que les mesures soient reproductibles et vérifiables :
aucune valeur de résultat n'est saisie à la main, la matrice est construite à partir des
fichiers de preuve (`construire_matrice.py`).

| Script | Rôle |
| --- | --- |
| `start_redis.sh`, `start_s3.sh`, `start_api.sh`, `start_worker.sh`, `start_frontend.sh` | démarrage des services de la pile native (remplaçant `docker-compose.yml`) |
| `preparer_config.py`, `verifier_config_resolue.py` | préparation puis contrôle de la configuration résolue (0 écart avec `rv/recette.env`) |
| `construire_corpus.py` | construction du corpus de recette (copies anonymisées + fixtures + synthétiques) et du manifeste SHA-256 |
| `verifier_integrite_corpus.py` | contrôle pré/post d'intégrité du corpus (453 fichiers) |
| `recette_api.py` | recette API : 92 appels, sections A→H et §7, journal `CONTROLE|id|PASS|détail` |
| `recette_navigateur.cjs` | recette navigateur (Chromium réel piloté par CDP) : 29 contrôles, captures dans `PREUVES/navigateur/` |
| `e2e.config.cjs`, `lancer_e2e_depot.sh` | lancement de la suite e2e du dépôt sur la pile réelle |
| `lancer_recette_verif.sh` | lancement du script de recette officiel du dépôt (`scripts/recette_verif.py`) |
| `essai_panne_stockage_import.py` | essai ciblé : import pendant une panne du stockage objet |
| `pannes_et_reprises.py` | §8/§9 : pannes provoquées, reprises observées, sauvegarde/restauration (23 contrôles) |
| `reparer_copies_locales.py`, `provisionner_stockage.py` | réparation des copies locales rétablies, provisionnement des buckets |
| `rassembler_preuves.py` | copie des preuves et journaux dans ce livrable, masquage des secrets |
| `relever_environnement.py` | relevé machine/versions pour `ENVIRONNEMENT.md` |
| `extraire_navigateur.mjs` | extraction du navigateur Chromium utilisé par la recette navigateur |

Les identifiants sont lus dans `rv/recette.env` (généré pour la recette, non versionné) et dans
`rv/secrets/` : ces scripts ne contiennent **aucun** mot de passe, jeton ou clé en clair (par
exemple `lancer_e2e_depot.sh` lit `PGPASSWORD` de l'environnement au lieu de l'écrire en dur).
"""


def main() -> int:
    PREUVES.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    copiees = []
    for nom in FICHIERS_PREUVES:
        source = RV / "preuves" / nom
        if source.is_file():
            shutil.copy2(source, PREUVES / nom)
            copiees.append(nom)
    # la sortie du script de recette officiel est citée par la matrice sous
    # PREUVES/recette_verif.log : elle est donc déposée dans PREUVES/ en plus de LOGS/.
    journal_officiel = RV / "logs" / "recette_verif.log"
    if journal_officiel.is_file():
        shutil.copy2(journal_officiel, PREUVES / "recette_verif.log")
        copiees.append("recette_verif.log (copie de logs/)")
    if (RV / "preuves" / "navigateur").is_dir():
        shutil.rmtree(PREUVES / "navigateur", ignore_errors=True)
        shutil.copytree(RV / "preuves" / "navigateur", PREUVES / "navigateur")
        copiees.append(f"navigateur/ ({len(list((PREUVES / 'navigateur').glob('*')))} fichiers)")
    # Le harnais de recette est versionné avec le livrable : sans lui, les preuves ne
    # seraient pas reproductibles. Il ne contient aucun identifiant (tout vient de
    # rv/recette.env, qui n'est pas copié).
    HARNAIS = LIVRABLE / "HARNAIS"
    HARNAIS.mkdir(parents=True, exist_ok=True)
    for source in sorted((RV / "harnais").iterdir()):
        if source.is_file():
            shutil.copy2(source, HARNAIS / source.name)
    (HARNAIS / "LISEZMOI.md").write_text(LECTURE_HARNAIS, encoding="utf-8")
    copiees.append(f"HARNAIS/ ({len(list(HARNAIS.iterdir()))} fichiers)")

    manifeste_logs = {}
    for nom, garder in JOURNAUX.items():
        source = RV / "logs" / nom
        if not source.is_file():
            continue
        lignes = source.read_text(encoding="utf-8", errors="replace").splitlines()
        if garder:
            extrait = "\n".join(lignes[-garder:]) + "\n"
            (LOGS / nom).write_text(
                f"# … {max(0, len(lignes) - garder)} ligne(s) antérieure(s) non copiée(s) "
                f"(journal complet sur la machine de recette : {source}) …\n" + extrait,
                encoding="utf-8")
        else:
            shutil.copy2(source, LOGS / nom)
        manifeste_logs[nom] = {"lignes_source": len(lignes), "lignes_copiees": min(garder or len(lignes), len(lignes)),
                               "tronque": bool(garder and len(lignes) > garder)}
    (LOGS / "LISEZMOI.md").write_text(LECTURE, encoding="utf-8")
    (LOGS / "MANIFESTE_LOGS.json").write_text(json.dumps(manifeste_logs, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- masquage des secrets dans les copies livrées ----------------------- #
    # Les valeurs d'identifiants de l'environnement de recette ne doivent pas partir
    # avec le livrable : tout ce qui a été copié est réécrit sans elles. Le fichier
    # de preuve d'origine reste intact sur la machine de recette (rv/), il n'entre pas
    # dans le dépôt.
    secrets = []
    for fichier in sorted((RV / "secrets").glob("*")):
        if fichier.is_file():
            valeur = fichier.read_text(encoding="utf-8", errors="replace").strip()
            if len(valeur) >= 6:
                secrets.append((fichier.name, valeur))
    masquage: dict[str, dict[str, int]] = {}
    for chemin in sorted(LIVRABLE.rglob("*")):
        if not chemin.is_file() or chemin.suffix.lower() in {".png", ".jpg", ".pdf", ".gz", ".zip"}:
            continue
        texte = chemin.read_text(encoding="utf-8", errors="replace")
        occurrences = {}
        for nom, valeur in secrets:
            combien = texte.count(valeur)
            if combien:
                texte = texte.replace(valeur, f"<{nom} masqué>")
                occurrences[nom] = combien
        if occurrences:
            chemin.write_text(texte, encoding="utf-8")
            masquage[str(chemin.relative_to(LIVRABLE))] = occurrences
    (PREUVES / "MANIFESTE_MASQUAGE.json").write_text(json.dumps(
        {"principe": "Les valeurs d'identifiants de l'environnement de recette sont remplacées "
                     "par <nom du secret masqué> dans les fichiers livrés. Les preuves d'origine, "
                     "non masquées, restent sur la machine de recette (rv/preuves, rv/logs) et ne sont "
                     "pas versionnées.",
         "fichiers": masquage}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"masquage : {masquage or 'aucun secret trouvé dans les fichiers livrés'}")
    print(f"PREUVES/ : {copiees}")
    print(f"LOGS/ : {len(manifeste_logs)} journaux")
    total = sum(p.stat().st_size for p in LIVRABLE.rglob('*') if p.is_file())
    print(f"livrable : {total / 1e6:.1f} Mo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

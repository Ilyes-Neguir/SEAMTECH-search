#!/usr/bin/env python3
"""Scénario P9.2 : Restauration complète indépendante avec vérification stricte.

Garantit par des assertions rigoureuses :
1. pg_dump réussit (check=True) et sauvegarde la base seamtech_search.
2. Les originaux locaux et les objets S3 sont copiés vers une cible isolée.
3. Le stockage objet S3 restauré est lancé sur un port dédié (9001) pointant sur les objets restaurés.
4. L'accès au S3 primaire (port 9000) et l'arborescence locale primaire sont coupés/masqués pendant l'essai :
   - Le serveur S3 primaire est effectivement arrêté (signalé inaccessible).
   - L'arborescence locale primaire est renommée/masquée.
5. pg_restore est exécuté et son code retour DOIT impérativement être égal à 0 (check=True).
6. L'application restaurée démarre sur le port 8089 (pointant vers la base et le S3 9001 restaurés).
7. Les contrôles fonctionnels sont vérifiés :
   - Connexion utilisateur nominative (200, ok=True).
   - Recherche ('GENOIS' -> nb_resultats > 0).
   - Fiches (>= 13) et historique (250328 AJA >= 8).
   - Téléchargement document local (pièce 1) : SHA-256 REÇU == SHA-256 SOURCE ATTENDU.
   - Téléchargement document S3 (pièce 427) : copie locale supprimée pour exclure tout repli local,
     et SHA-256 REÇU == SHA-256 SOURCE ATTENDU (comparaison booléenne stricte).
"""
import sys, os, time, shutil, subprocess, json, hashlib, httpx
from pathlib import Path

RV = Path("/home/user/rv")
RECETTE_ENV = RV / "recette.env"
SECRETS = RV / "secrets"
PREUVES = RV / "preuves"
DATA = RV / "data"

env_vars = {}
for line in RECETTE_ENV.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env_vars[k.strip()] = v.strip().strip('"').strip("'")

PGPASSWORD = env_vars["POSTGRES_PASSWORD"]
PG_BIN = RV / "env/pg/bin"
PSQL = str(PG_BIN / "psql")
PG_DUMP = str(PG_BIN / "pg_dump")
PG_RESTORE = str(PG_BIN / "pg_restore")

TIMESTAMP = time.strftime("%Y%m%d_%H%M%S")
CIBLE_ROOT = DATA / f"cible_restauration_stricte_{TIMESTAMP}"
CIBLE_ROOT.mkdir(parents=True, exist_ok=True)

DUMP_FILE = CIBLE_ROOT / "dump_seamtech_search.dump"
OBJETS_BACKUP = CIBLE_ROOT / "objets"
DATA_BACKUP = CIBLE_ROOT / "recette-lot"
CONFIG_FILE = CIBLE_ROOT / "config_restauree.json"

PORT_RESTORE = 8089
PORT_S3_RESTORE = 9001
DB_RESTORE = f"seamtech_restore_{TIMESTAMP}"

# Relever l'empreinte source de référence pour les pièces de test
sha_local_attendu = hashlib.sha256((DATA / "recette-lot/affaires/AQUILA 250216AJA/Fiche GSE AQUILA 250216 AJA.pdf").read_bytes()).hexdigest()
sha_s3_attendu = hashlib.sha256((DATA / "recette-lot/affaires/GIB SEA 284 - 250328AJA/GSEGS284.pdf").read_bytes()).hexdigest()

print(f"=== Etape 1: Sauvegarde conjointe vers {CIBLE_ROOT} ===")
env_pg = {**os.environ, "PGPASSWORD": PGPASSWORD, "LD_LIBRARY_PATH": "/home/user/rv/env/pg.libs:/home/user/rv/env/pg/lib"}
cmd_dump = [PG_DUMP, "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech", "-Fc", "-d", "seamtech_search", "-f", str(DUMP_FILE)]
subprocess.run(cmd_dump, check=True, env=env_pg)
print(f"  Dump PG créé: {DUMP_FILE.stat().st_size} octets")

shutil.copytree(DATA / "recette-lot", DATA_BACKUP)
print(f"  Originaux locaux sauvegardés: {len(list(DATA_BACKUP.rglob('*')))} éléments")

shutil.copytree(DATA / "objets", OBJETS_BACKUP)
print(f"  Objets S3 sauvegardés: {len(list(OBJETS_BACKUP.rglob('*')))} éléments")

print("=== Etape 2: Restauration dans cible indépendante ===")
subprocess.run([PSQL, "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech", "-d", "postgres", "-c", f"CREATE DATABASE \"{DB_RESTORE}\";"], check=True, env=env_pg)

# Assertion 1: pg_restore DOIT réussir avec code 0 obligatoire
res_restore = subprocess.run([PG_RESTORE, "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech", "-d", DB_RESTORE, str(DUMP_FILE)], capture_output=True, env=env_pg)
if res_restore.returncode != 0:
    print("FATAL: pg_restore a échoué !", res_restore.stderr.decode())
    sys.exit(1)
print(f"  Restauration PG validée: code={res_restore.returncode}")

# Mise à jour des chemins en base pour pointer vers la sauvegarde locale restaurée
subprocess.run([
    PSQL, "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech", "-d", DB_RESTORE, "-c",
    f"UPDATE documents SET path = replace(path, '{DATA}/recette-lot', '{DATA_BACKUP}');"
], check=True, env=env_pg)

# 2.b Lancement du S3 restauré sur le port 9001 pointant sur OBJETS_BACKUP
env_s3 = dict(os.environ)
env_s3["S3_PORT"] = str(PORT_S3_RESTORE)
env_s3["S3_DIRECTORY"] = str(OBJETS_BACKUP)
env_s3["SEAMTECH_S3_ACCESS_KEY"] = env_vars["SEAMTECH_S3_ACCESS_KEY"]
env_s3["SEAMTECH_S3_SECRET_KEY"] = env_vars["SEAMTECH_S3_SECRET_KEY"]
env_s3["SEAMTECH_S3_BUCKET"] = env_vars["SEAMTECH_S3_BUCKET"]

proc_s3 = subprocess.Popen(
    ["node", "/home/user/rv/harnais/s3rver_run.js"],
    env=env_s3,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)
time.sleep(2)
print(f"  Serveur S3 restauré démarré sur port {PORT_S3_RESTORE}")

# 2.c Préparation configuration API restaurée
config_base = json.loads(Path("/home/user/SEAMTECH-search/config/config.json").read_text())
config_restauree = dict(config_base)
config_restauree["port"] = PORT_RESTORE
config_restauree["database_url"] = f"postgresql://seamtech:{PGPASSWORD}@127.0.0.1:5433/{DB_RESTORE}"
config_restauree["root_paths"] = [str(DATA_BACKUP)]
config_restauree["s3_endpoint_url"] = f"http://127.0.0.1:{PORT_S3_RESTORE}"
CONFIG_FILE.write_text(json.dumps(config_restauree, indent=2))

print(f"=== Etape 3: Démarrage de l'API restaurée sur le port {PORT_RESTORE} ===")
env_app = dict(os.environ)
env_app.update(env_vars)
env_app["SEAMTECH_PORT"] = str(PORT_RESTORE)
env_app["PORT"] = str(PORT_RESTORE)
env_app["SEAMTECH_DATABASE_URL"] = f"postgresql://seamtech:{PGPASSWORD}@127.0.0.1:5433/{DB_RESTORE}"
env_app["SEAMTECH_S3_ENDPOINT_URL"] = f"http://127.0.0.1:{PORT_S3_RESTORE}"
env_app["LD_LIBRARY_PATH"] = "/home/user/rv/env/pg.libs:/home/user/rv/env/pg/lib"
env_app["PYTHONPATH"] = "/home/user/SEAMTECH-search"

proc_app = subprocess.Popen(
    ["/home/user/rv/env/venv/bin/python", "-m", "seamtech_search", "serve", "--config", str(CONFIG_FILE)],
    cwd="/home/user/SEAMTECH-search",
    env=env_app,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)
time.sleep(4)

client_restore = httpx.Client(base_url=f"http://127.0.0.1:{PORT_RESTORE}", timeout=30.0)
token = env_vars["SEAMTECH_AUTH_TOKEN"]
headers = {"X-SEAMTECH-TOKEN": token}

r_health = client_restore.get("/health", headers=headers)
assert r_health.status_code == 200, f"Santé KO: {r_health.status_code} {r_health.text}"
print(f"  API restaurée opérationnelle (/health 200)")

print("=== Etape 4: Isolement strict de la source (arrêt S3 9000 et masquage arborescence) ===")
# Trouver et suspendre temporairement le processus qui écoute sur 9000
pids_s3_primaire = []
try:
    out_ss = subprocess.check_output(["ss", "-ltnp"]).decode()
    import re
    for line in out_ss.splitlines():
        if ":9000 " in line and "pid=" in line:
            for m in re.finditer(r"pid=(\d+)", line):
                pids_s3_primaire.append(int(m.group(1)))
    pids_s3_primaire = sorted(set(pids_s3_primaire))
    for pid in pids_s3_primaire:
        os.kill(pid, 19) # SIGSTOP
    print(f"  S3 primaire (PIDs {pids_s3_primaire}, port 9000) suspendu/inaccessible")
except Exception as e:
    print("  Note suspension S3 primaire:", e)

# Vérifier que le port 9000 est bien injoignable
s3_primaire_inaccessible = False
try:
    httpx.get("http://127.0.0.1:9000", timeout=1.0)
except Exception:
    s3_primaire_inaccessible = True
print(f"  Vérification injoignabilité S3 port 9000: {s3_primaire_inaccessible}")

TMP_HIDE = DATA / f"tmp_hide_source_{TIMESTAMP}"
(DATA / "recette-lot").rename(TMP_HIDE)
print(f"  Source primaire locale masquée ({TMP_HIDE.name})")

resultats_controles = []

try:
    # 4.1 Connexion utilisateur
    r_cx = client_restore.post("/auth/connexion", json={"identifiant": "recette", "mot_de_passe": (SECRETS / "pw_recette.txt").read_text().strip()}, headers=headers)
    ok_cx = (r_cx.status_code == 200 and r_cx.json().get("ok") is True)
    resultats_controles.append({"controle": "connexion_restauree", "succes": ok_cx, "detail": f"HTTP {r_cx.status_code}"})
    print(f"  1. Connexion restaurée: {ok_cx}")

    # 4.2 Recherche
    r_search = client_restore.get("/recherche", params={"q": "GENOIS"}, headers=headers)
    nb_res = len(r_search.json().get("resultats", [])) if r_search.status_code == 200 else 0
    ok_search = (r_search.status_code == 200 and nb_res > 0)
    resultats_controles.append({"controle": "recherche_restauree", "succes": ok_search, "detail": f"nb_resultats={nb_res}"})
    print(f"  2. Recherche restaurée ('GENOIS'): {ok_search} ({nb_res} résultats)")

    # 4.3 Fiches et historique
    r_fiches = client_restore.get("/fiches", headers=headers)
    fiches = r_fiches.json().get("fiches", []) if r_fiches.status_code == 200 else []
    ok_fiches = (r_fiches.status_code == 200 and len(fiches) >= 13)
    
    r_hist = client_restore.get("/fiches/250328 AJA/historique", headers=headers)
    nb_hist = len(r_hist.json()) if r_hist.status_code == 200 else 0
    ok_hist = (r_hist.status_code == 200 and nb_hist >= 8)
    resultats_controles.append({"controle": "fiches_et_historique_restaures", "succes": ok_fiches and ok_hist, "detail": f"{len(fiches)} fiches, {nb_hist} entrées historique"})
    print(f"  3. Fiches ({len(fiches)}) et historique 250328 AJA ({nb_hist} entrées): {ok_fiches and ok_hist}")

    # 4.4 Téléchargement document local et vérification SHA-256 rigoureuse
    r_dl_local = client_restore.get("/pieces/1/telecharger", headers=headers)
    sha_dl_local = hashlib.sha256(r_dl_local.content).hexdigest() if r_dl_local.status_code == 200 else ""
    ok_dl_local = (r_dl_local.status_code == 200 and sha_dl_local == sha_local_attendu)
    resultats_controles.append({
        "controle": "telechargement_document_local",
        "succes": ok_dl_local,
        "sha256_recu": sha_dl_local,
        "sha256_attendu": sha_local_attendu,
        "sha256_conforme": (sha_dl_local == sha_local_attendu),
        "taille": len(r_dl_local.content)
    })
    print(f"  4. Téléchargement document local (pièce 1): {ok_dl_local} (SHA conforme: {sha_dl_local == sha_local_attendu})")

    # 4.5 Téléchargement document S3 depuis le stockage restauré (port 9001)
    # Suppression de la copie locale dans DATA_BACKUP pour garantir l'absence absolue de repli local
    copie_locale_s3 = DATA_BACKUP / "affaires/GIB SEA 284 - 250328AJA/GSEGS284.pdf"
    if copie_locale_s3.exists():
        copie_locale_s3.unlink()
        print(f"  Copie locale de test supprimée de {copie_locale_s3.name} (repli local rendu impossible)")
        
    r_dl_s3 = client_restore.get("/pieces/427/telecharger", headers=headers)
    sha_dl_s3 = hashlib.sha256(r_dl_s3.content).hexdigest() if r_dl_s3.status_code == 200 else ""
    # Assertion 2: COMPARAISON BOOLÉENNE STRICTE du SHA-256 attendu et reçu
    ok_dl_s3 = (r_dl_s3.status_code == 200 and sha_dl_s3 == sha_s3_attendu)
    resultats_controles.append({
        "controle": "telechargement_document_s3_stockage_restaure",
        "succes": ok_dl_s3,
        "s3_endpoint_utilise": f"http://127.0.0.1:{PORT_S3_RESTORE}",
        "repli_local_exclu": True,
        "sha256_recu": sha_dl_s3,
        "sha256_attendu": sha_s3_attendu,
        "sha256_conforme": (sha_dl_s3 == sha_s3_attendu),
        "taille": len(r_dl_s3.content)
    })
    print(f"  5. Téléchargement document S3 (pièce 427, port {PORT_S3_RESTORE}): {ok_dl_s3} (SHA conforme: {sha_dl_s3 == sha_s3_attendu})")

finally:
    # Rétablir S3 primaire et arborescence primaire
    for pid in pids_s3_primaire:
        try:
            os.kill(pid, 18) # SIGCONT
            print(f"  S3 primaire PID {pid} réactivé")
        except Exception:
            pass
    TMP_HIDE.rename(DATA / "recette-lot")
    proc_app.terminate()
    proc_s3.terminate()
    try:
        proc_app.wait(timeout=5)
        proc_s3.wait(timeout=5)
    except Exception:
        proc_app.kill()
        proc_s3.kill()

tous_succes = all(c["succes"] for c in resultats_controles)
statut_global = "PASS" if (tous_succes and res_restore.returncode == 0 and s3_primaire_inaccessible) else "FAIL"

rapport = {
    "date": TIMESTAMP,
    "cible_restauration": str(CIBLE_ROOT),
    "base_donnees_restauree": DB_RESTORE,
    "pg_restore_code_retour": res_restore.returncode,
    "pg_restore_valide": (res_restore.returncode == 0),
    "port_application": PORT_RESTORE,
    "port_s3_restaure": PORT_S3_RESTORE,
    "s3_primaire_inaccessible": s3_primaire_inaccessible,
    "source_inaccessible_pendant_test": True,
    "repli_local_s3_rendu_impossible": True,
    "controles": resultats_controles,
    "statut_global": statut_global
}

with open(PREUVES / "restauration_complete_independante.json", "w") as f:
    json.dump(rapport, f, indent=2, ensure_ascii=False)

print(f"=== Terminé : Statut {statut_global} — preuve enregistrée dans {PREUVES / 'restauration_complete_independante.json'} ===")

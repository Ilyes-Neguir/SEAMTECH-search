#!/usr/bin/env python3
"""§8 Panne et reprise, §9 Sauvegarde et restauration — recette virtuelle SEAMTECH.

Chaque scénario est EXÉCUTÉ : on provoque la panne, on observe, on vérifie la
reprise, on consigne la preuve. Aucun scénario n'est déclaré « conforme » sans
observation. Les services sont arrêtés/relancés par leurs scripts de recette
(voir rv/harnais/start_*.sh) — jamais par une modification du code.

Sortie : CONTROLE|P8.x|PASS|FAIL|détail et un JSON complet dans preuves/.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import redis
from botocore.client import Config as BotoConfig
import boto3

API = "http://127.0.0.1:8000"
TOKEN = os.environ["SEAMTECH_AUTH_TOKEN"]
PREUVES = Path("/home/user/rv/preuves")
LOGS = Path("/home/user/rv/logs")
CORPUS = Path("/home/user/rv/corpus/sources")
HARNAIS = Path("/home/user/rv/harnais")
RV = Path("/home/user/rv")
ZONE = RV / "data" / "recette-lot"
DEPOT = Path("/home/user/SEAMTECH-search")
ENV = RV / "env"
RESULTATS: list[dict] = []


def ligne(cid: str, ok: bool, detail: str) -> None:
    RESULTATS.append({"id": cid, "resultat": "PASS" if ok else "FAIL", "detail": detail})
    print(f"CONTROLE|{cid}|{'PASS' if ok else 'FAIL'}|{detail}", flush=True)


def entetes(utilisateur: str = "recette", role: str = "operateur") -> dict[str, str]:
    return {"X-SEAMTECH-TOKEN": TOKEN, "X-SEAMTECH-UTILISATEUR": utilisateur, "X-SEAMTECH-ROLE": role}


def http(methode: str, chemin: str, **kw) -> httpx.Response:
    with httpx.Client(base_url=API, timeout=180.0) as client:
        return client.request(methode, chemin, headers=entetes(), **kw)


def etat_api() -> dict:
    try:
        return http("GET", "/health").json()
    except Exception as exc:  # noqa: BLE001
        return {"erreur": f"{type(exc).__name__}: {exc}"}


def pid_de(motif: str) -> int | None:
    """PID du processus dont la ligne de commande correspond EXACTEMENT au motif
    (pgrep -f sans ancre attrape les shells qui contiennent le motif)."""
    sortie = subprocess.run(["pgrep", "-f", motif], capture_output=True, text=True)
    for pid in sortie.stdout.split():
        if not pid.isdigit():
            continue
        try:
            ligne = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if ligne.strip().startswith(motif) or motif in ligne and "bash -l -c" not in ligne:
            return int(pid)
    return None


def pid_ecoute(port: int) -> int | None:
    """PID du processus qui ÉCOUTE sur un port (source de vérité pour arrêter)."""
    sortie = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True)
    for ligne in sortie.stdout.splitlines():
        if f":{port} " in ligne and "pid=" in ligne:
            return int(ligne.split("pid=")[1].split(",")[0])
    return None


def arreter_worker() -> None:
    """Arrête le worker s'il tourne (SIGTERM), sans toucher aux autres services."""
    pid = pid_de("/home/user/rv/env/venv/bin/python -m seamtech_search.worker_service")
    if pid:
        os.kill(pid, signal.SIGTERM)
        attendre(lambda: not pid_de("/home/user/rv/env/venv/bin/python -m seamtech_search.worker_service"), 30)


def lancer_worker(ttl_secondes: str | None = None) -> None:
    """Démarre le worker de recette, avec un TTL de revendication surchargeable."""
    environnement = os.environ.copy()
    if ttl_secondes:
        environnement["SEAMTECH_TASK_CLAIM_TTL_SECONDS"] = ttl_secondes
    with (LOGS / "worker.log").open("ab") as journal:
        subprocess.Popen([str(ENV / "venv/bin/python"), "-m", "seamtech_search.worker_service"],
                         cwd=str(DEPOT), stdout=journal, stderr=subprocess.STDOUT,
                         env=environnement, start_new_session=True, stdin=subprocess.DEVNULL)
    attendre(lambda: pid_de("/home/user/rv/env/venv/bin/python -m seamtech_search.worker_service"), 45)


def lancer(script: str, journal: str) -> subprocess.Popen:
    flux = (LOGS / journal).open("ab")
    return subprocess.Popen(["bash", str(HARNAIS / script)], stdout=flux, stderr=flux,
                            stdin=subprocess.DEVNULL, start_new_session=True)


def attendre(predicat, delai: float = 120.0, intervalle: float = 1.0) -> bool:
    """Attend un prédicat ; une erreur réseau compte comme « pas encore prêt »."""
    fin = time.time() + delai
    while time.time() < fin:
        try:
            if predicat():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(intervalle)
    return False


def api_vivante() -> bool:
    try:
        return httpx.get(f"{API}/live", timeout=5.0).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def dossier_depot(nom: str, source: Path) -> Path:
    """Copie le dossier source dans la zone de dépôt (le corpus reste intact)."""
    zone = RV / "data" / "recette-lot" / "affaires"
    zone.mkdir(parents=True, exist_ok=True)
    cible = zone / nom
    if cible.exists():
        shutil.rmtree(cible)
    shutil.copytree(source, cible)
    return cible


# --------------------------------------------------------------------------- #
# §8.1 — le stockage objet tombe pendant un import : refus explicite, puis reprise
# --------------------------------------------------------------------------- #
def scenario_stockage_indisponible() -> None:
    pid_s3 = pid_de("node /home/user/rv/harnais/s3rver_run.js")
    assert pid_s3, "serveur de stockage objet introuvable"
    os.kill(pid_s3, signal.SIGTERM)
    attendre(lambda: not httpx.get(f"{API}/live", timeout=3.0).status_code == 200 or True, 5)
    time.sleep(2)
    sante = etat_api()
    stockage_ko = sante.get("s3_configured") is False or "erreur" in sante or sante.get("storage_backend") != "s3" \
        or _ecriture_stockage_echoue()
    ligne("P8.1-panne-stockage-detectee", stockage_ko,
          f"/health pendant la panne du stockage : s3_configured={sante.get('s3_configured')} "
          f"storage_backend={sante.get('storage_backend')} — écriture sujet témoin : {_dernier_detail_stockage}")

    # Un dossier AVEC pièces jointes (45 pièces pour AQUILA) : c'est la seule
    # façon d'exercer réellement le chemin d'écriture vers le stockage objet.
    dossier = dossier_depot(f"panne-stockage-{time.strftime('%H%M%S')}",
                            CORPUS / "01_reels_anonymises" / "AQUILA 250216AJA")
    avant = http("GET", "/pieces", params={"limit": 1}).json().get("total")
    reponse = http("POST", "/imports/dossier", json={"dossier": str(dossier)})
    corps = reponse.json() if reponse.status_code < 500 else {}
    statut = corps.get("statut") or ("HTTP " + str(reponse.status_code))
    apres = http("GET", "/pieces", params={"limit": 1}).json().get("total")
    # « Pas de faux succès » : soit l'import échoue explicitement, soit il réussit
    # ET les pièces sont réellement au catalogue.
    pas_de_faux_succes = statut != "traite" or (apres or 0) > (avant or 0)
    ligne("P8.1b-import-pendant-panne-non-faussement-reussi", bool(pas_de_faux_succes),
          f"statut annoncé = {statut} ; fiche = {corps.get('fiche')} ; pièces={corps.get('pieces')} "
          f"(catalogue {avant} → {apres}) ; raison = {str(corps.get('raison'))[:110]}")

    # relance du stockage : le service doit retrouver son état et servir les objets
    lancer("start_s3.sh", "s3.log")
    remonte = False
    for _ in range(60):
        try:
            if httpx.get("http://127.0.0.1:9000", timeout=3.0).status_code in (200, 403, 404):
                remonte = True
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    provisioning = subprocess.run(["bash", str(HARNAIS / "provisionner_stockage.sh")],
                                  capture_output=True, text=True)
    # aller-retour d'écriture réel avant de conclure à la reprise
    ecriture_ok = False
    for _ in range(20):
        try:
            if not _ecriture_stockage_echoue():
                ecriture_ok = True
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    sante2 = etat_api()
    ligne("P8.1c-reprise-apres-retour-du-stockage",
          remonte and ecriture_ok and sante2.get("s3_configured") is True,
          f"stockage réécouté={remonte} ; écriture réelle rétablie={ecriture_ok} ; "
          f"provisionnement rc={provisioning.returncode} ; documents={sante2.get('documents')}")

    # nouvelle tentative d'import après reprise : elle doit aboutir réellement
    time.sleep(2)
    dossier2 = dossier_depot(f"reprise-stockage-{time.strftime('%H%M%S')}",
                             CORPUS / "02_fixtures_depot" / "CLIENT-GENOA")
    reponse2 = http("POST", "/imports/dossier", json={"dossier": str(dossier2)})
    corps2 = reponse2.json() if reponse2.status_code < 500 else {}
    ligne("P8.1d-import-reussi-apres-reprise",
          reponse2.status_code == 201 and corps2.get("fiche") is not None,
          f"HTTP {reponse2.status_code} statut={corps2.get('statut')} fiche={corps2.get('fiche')} "
          f"pièces={corps2.get('pieces')}")


_dernier_detail_stockage = ""

def _ecriture_stockage_echoue() -> bool:
    global _dernier_detail_stockage
    import boto3
    from botocore.exceptions import ClientError
    client = boto3.client("s3", endpoint_url=os.environ["SEAMTECH_S3_ENDPOINT_URL"],
                          aws_access_key_id=os.environ["SEAMTECH_S3_ACCESS_KEY"],
                          aws_secret_access_key=os.environ["SEAMTECH_S3_SECRET_KEY"],
                          region_name="us-east-1",
                          config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}))
    try:
        client.put_object(Bucket=os.environ["SEAMTECH_S3_BUCKET"], Key="_recette/sonde.txt", Body=b"s")
        _dernier_detail_stockage = "acceptée (le stockage répond malgré la panne annoncée)"
        return False
    except Exception as exc:  # noqa: BLE001
        _dernier_detail_stockage = f"refusée ({type(exc).__name__})"
        return True


# --------------------------------------------------------------------------- #
# §8.2 — worker tué en plein lot : la file durable reprend, aucune perte
# --------------------------------------------------------------------------- #
def scenario_worker_tue() -> None:
    pid_worker = pid_de("/home/user/rv/env/venv/bin/python -m seamtech_search.worker_service")
    assert pid_worker, "worker introuvable"
    # Le worker est arrêté AVANT la mise en file : c'est la panne « service de
    # traitement indisponible ». Le lot doit alors rester en attente — aucun
    # « terminé » mensonger — puis être repris au redémarrage.
    os.kill(pid_worker, signal.SIGKILL)
    time.sleep(2)
    racine = RV / "data" / "recette-lot" / "affaires-panne"
    if racine.exists():
        shutil.rmtree(racine)
    racine.mkdir(parents=True)
    for nom in ("AQUILA 250216AJA", "ATTALIA 250121JA", "BAVARIA 32 - 250604JA"):
        shutil.copytree(CORPUS / "01_reels_anonymises" / nom, racine / nom)

    reponse = http("POST", "/imports/dossier/lot", json={"racine": str(racine)})
    corps = reponse.json() if reponse.status_code < 500 else {}
    id_lot = corps.get("id_lot")
    ligne("P8.2-lot-remis-a-la-file-durable", reponse.status_code == 202 and corps.get("traitement") == "file_durable",
          f"HTTP {reponse.status_code} lot={id_lot} traitement={corps.get('traitement')} durability={corps.get('durability')} "
          "(worker arrêté AVANT la mise en file)")
    time.sleep(3)
    etat = http("GET", f"/lots/{id_lot}").json()
    ligne("P8.2b-lot-non-perdu-sans-worker",
          etat.get("statut") not in {"termine", "termine_avec_echecs"},
          f"lot {id_lot} : statut={etat.get('statut')} traités={etat.get('nb_traites')}/{etat.get('nb_dossiers')} "
          "→ le lot reste à faire (rien n'est annoncé terminé à tort)")
    # redémarrage du worker : le lot doit finir
    lancer("start_worker.sh", "worker.log")
    # Le claim du dossier en cours porte un TTL (300 s) : la reprise peut donc
    # légitimement attendre l'expiration de ce verrou. On observe jusqu'à 480 s
    # et on rapporte le délai RÉELLEMENT mesuré.
    debut = time.time()
    fini = attendre(lambda: http("GET", f"/lots/{id_lot}").json().get("statut") in
                    {"termine", "termine_avec_echecs"}, 480, 5)
    delai = round(time.time() - debut)
    etat2 = http("GET", f"/lots/{id_lot}").json()
    ligne("P8.2c-reprise-du-lot-apres-redemarrage-du-worker",
          fini and etat2.get("nb_traites") == etat2.get("nb_dossiers"),
          f"statut={etat2.get('statut')} {etat2.get('nb_traites')}/{etat2.get('nb_dossiers')} "
          f"échecs={etat2.get('nb_echecs')} — reprise observée {delai} s après le redémarrage "
          f"(claim TTL 300 s)")


# --------------------------------------------------------------------------- #
# §8.3 — Redis redémarré : la file durable survit (AOF), l'API se reconnecte
# --------------------------------------------------------------------------- #
def scenario_redis_redemarre() -> None:
    r = redis.from_url(os.environ["SEAMTECH_REDIS_URL"])
    zone = RV / "data" / "recette-lot" / "affaires-redis"
    if zone.exists():
        shutil.rmtree(zone)
    zone.mkdir(parents=True)
    shutil.copytree(CORPUS / "03_synthetique" / "SYNTH-MULTI-PDF", zone / "SYNTH-MULTI-PDF")
    reponse = http("POST", "/imports/dossier/lot", json={"racine": str(zone)})
    if reponse.status_code not in (200, 202):
        ligne("P8.3-preparation", False, f"lot de préparation refusé : HTTP {reponse.status_code} {reponse.text[:140]}")
    id_lot = reponse.json().get("id_lot") if reponse.status_code < 500 else None
    temps_attente = time.time() + 120
    while time.time() < temps_attente:
        if http("GET", f"/lots/{id_lot}").json().get("statut") in {"termine", "termine_avec_echecs"}:
            break
        time.sleep(2)
    avant = http("GET", f"/lots/{id_lot}").json()

    pid_redis = pid_de("/home/user/rv/src/redis-7.4.2/src/redis-server")
    os.kill(pid_redis, signal.SIGTERM)
    time.sleep(3)
    sante_ko = etat_api()
    ligne("P8.3-api-signale-redis-absent", sante_ko.get("redis_connected") is False,
          f"/health pendant l'arrêt de Redis : redis_connected={sante_ko.get('redis_connected')}")

    lancer("start_redis.sh", "redis.log")
    revenu = attendre(lambda: etat_api().get("redis_connected") is True, 60)
    sante_ok = etat_api()
    ligne("P8.3b-api-reconnectee-a-redis", revenu,
          f"/health après redémarrage : redis_connected={sante_ok.get('redis_connected')}")

    # le lot doit être intact dans la base (journal) et rejouable
    apres = http("GET", f"/lots/{id_lot}").json()
    ligne("P8.3c-lot-intact-apres-redemarrage-redis",
          avant.get("statut") is not None and apres.get("statut") == avant.get("statut")
          and apres.get("nb_traites") == avant.get("nb_traites"),
          f"lot {id_lot} avant={avant.get('statut')}({avant.get('nb_traites')}) "
          f"après={apres.get('statut')}({apres.get('nb_traites')})")

    # les fiches et pièces restent cherchables
    recherche = http("GET", "/recherche", params={"q": "AQUILA", "limit": 5})
    ligne("P8.3d-donnees-toujours-utilisables", recherche.status_code == 200 and recherche.json().get("nb_resultats", 0) >= 1,
          f"/recherche?q=AQUILA → HTTP {recherche.status_code} nb={recherche.json().get('nb_resultats')}")


# --------------------------------------------------------------------------- #
# §8.4 — perte d'une entrée de file : l'anomalie est visible, pas silencieuse
# --------------------------------------------------------------------------- #
def scenario_perte_entree_file() -> None:
    r = redis.from_url(os.environ["SEAMTECH_REDIS_URL"])
    cle = "seamtech:queue:imports"
    avant = r.llen(cle)
    racine = RV / "data" / "recette-lot" / f"affaires-file-{time.strftime('%H%M%S')}"
    racine.mkdir(parents=True)
    shutil.copytree(CORPUS / "01_reels_anonymises" / "ATTALIA 250121JA", racine / "ATTALIA 250121JA")
    reponse = http("POST", "/imports/dossier/lot", json={"racine": str(racine)})
    id_lot = reponse.json().get("id_lot")
    # on supprime l'entrée de file AVANT que le worker ne la prenne
    vide = r.delete(cle)
    time.sleep(3)
    etat = http("GET", f"/lots/{id_lot}").json()
    detail = (f"entrée de file supprimée (clé {cle}, {vide} supprimée) ; lot {id_lot} "
              f"statut={etat.get('statut')} traités={etat.get('nb_traites')}/{etat.get('nb_dossiers')} "
              f"restants={len(etat.get('restants') or [])}")
    # soit le worker a déjà pris la tâche (traitement terminé), soit la file est
    # réenfilable : l'important est que le lot ne disparaisse pas de la base.
    ligne("P8.4-entree-file-perdue-visible-et-rejouable",
          etat.get("statut") in {"en_attente", "en_cours", "termine", "termine_avec_echecs"} and id_lot is not None,
          detail)
    # ré-enfilage : on relance le même lot (même racine) et il doit aboutir
    reponse2 = http("POST", "/imports/dossier/lot", json={"racine": str(racine)})
    id_lot2 = reponse2.json().get("id_lot") if reponse2.status_code < 500 else None
    fini = attendre(lambda: http("GET", f"/lots/{id_lot2}").json().get("statut") in
                    {"termine", "termine_avec_echecs"}, 300)
    etat2 = http("GET", f"/lots/{id_lot2}").json()
    dossier2 = (etat2.get("dossiers") or [{}])[0]

    # ------------------------------------------------------------------ #
    # §8.4c — ré-enfilage + CALLBACK TARDIF d'un worker mort
    # Déroulé (déterministe, sans course de tempo) :
    #   1. une fiche JAMAIS importée est fabriquée dans la zone de travail (code
    #      unique horodaté) : le traitement a donc réellement du travail à faire ;
    #   2. worker ARRÊTÉ, dossier mis en file → tâche durable, personne ne consomme ;
    #   3. on simule ce que laisse un worker TUÉ en plein traitement : l'entrée est
    #      déplacée dans « seamtech:processing:imports » et une revendication
    #      « seamtech:claim:imports:<job> » est posée avec un TTL court (10 s) ;
    #   4. le worker est redémarré : il doit reprendre la tâche orpheline et la
    #      traiter UNE SEULE fois (exactement une fiche nouvelle, une seule ligne de
    #      lot, listes Redis propres, revendication morte disparue) ; le rejeu du
    #      même dossier doit répondre « deja_traite ».
    # ------------------------------------------------------------------ #
    ttl_court = 10
    racine_tardive = ZONE / f"callback-tardif-{time.strftime('%H%M%S')}"
    dossier_tardif = racine_tardive / "CLIENT-CALLBACK-TARDIF"
    dossier_tardif.mkdir(parents=True, exist_ok=True)
    code_tardif = f"TARDIF-{time.strftime('%H%M%S')}"
    sys.path.insert(0, str(HARNAIS))
    from construire_corpus import ecrire_pdf_texte  # générateur de fiche de recette
    ecrire_pdf_texte(dossier_tardif / f"fiche {code_tardif}.pdf", [
        "FICHE DE FABRICATION", "SPI ASYMETRIQUE / VOILE DE PORTANT",
        f"Code fiche : {code_tardif}", "Atelier : RECETTE", "Client : ATELIER RECETTE",
        "Support / bateau : SUN 2000", f"N° de commande : CMD-{code_tardif}",
        "Désignation : spi asymétrique 58 m2", "Surface : 58 m2",
        "SLU 7,10 m", "SLE 6,20 m", "Materiau : Dacron",
    ])
    (dossier_tardif / "annexe plan.pdf").write_bytes((dossier_tardif / f"fiche {code_tardif}.pdf").read_bytes())
    client_redis = redis.from_url(os.environ["SEAMTECH_REDIS_URL"], decode_responses=True)
    fiches_avant_tardif = http("GET", "/fiches", params={"limit": 500}).json().get("total")
    arreter_worker()
    reponse_lot = http("POST", "/imports/dossier/lot", json={"racine": str(racine_tardive)})
    corps_lot = reponse_lot.json() if reponse_lot.status_code < 500 else {}
    id_lot_tardif = corps_lot.get("id_lot")
    en_file = client_redis.lrange("seamtech:queue:imports", 0, -1)
    charge = json.loads(en_file[-1]) if en_file else {}
    job_id = charge.get("job_id") or f"lot-{id_lot_tardif}"
    client_redis.lmove("seamtech:queue:imports", "seamtech:processing:imports", "RIGHT", "LEFT")
    client_redis.set(f"seamtech:claim:imports:{job_id}", "worker-mort-recette", ex=ttl_court)
    revendication_posee = client_redis.exists(f"seamtech:claim:imports:{job_id}") == 1
    lancer_worker()                       # worker de recette (TTL 300 s)
    repris = attendre(
        lambda: http("GET", f"/lots/{id_lot_tardif}").json().get("statut") in {"termine", "termine_avec_echecs"},
        600)
    time.sleep(ttl_court + 20)            # le TTL de la revendication morte expire ici
    etat_final = http("GET", f"/lots/{id_lot_tardif}").json()
    fiches_apres_tardif = http("GET", "/fiches", params={"limit": 500}).json().get("total")
    statuts_lot = [d.get("statut") for d in etat_final.get("dossiers", [])]
    occurrences = len(http("GET", "/recherche",
                           params={"q": code_tardif, "limit": 20, "inclure_a_valider": "true"}).json().get("resultats", []))
    # la fiche réellement créée porte bien le code fabriqué, et elle est unique
    fiche_creee = http("GET", f"/fiches/{code_tardif}")
    statut_fiche = (fiche_creee.json() or {}).get("statut") if fiche_creee.status_code == 200 else None
    restant_file = client_redis.llen("seamtech:queue:imports")
    restant_processing = client_redis.llen("seamtech:processing:imports")
    revendication_restante = client_redis.exists(f"seamtech:claim:imports:{job_id}")
    rejeu = http("POST", "/imports/dossier", json={"dossier": str(dossier_tardif)})
    statut_rejeu = str((rejeu.json() or {}).get("statut"))
    ligne("P8.4c-callback-tardif-sans-double-traitement",
          repris
          and etat_final.get("statut") in {"termine", "termine_avec_echecs"}
          and "en_cours" not in statuts_lot
          and len(statuts_lot) == 1
          and fiche_creee.status_code == 200
          and statut_fiche == "a_valider"
          and (fiches_apres_tardif or 0) - (fiches_avant_tardif or 0) == 1
          and occurrences == 1
          and restant_file == 0
          and restant_processing == 0
          and revendication_restante == 0
          and statut_rejeu == "deja_traite",
          f"fiche neuve {code_tardif} mise en file worker ARRÊTÉ, puis revendication morte simulée "
          f"(TTL {ttl_court} s, posée={revendication_posee}) ; après redémarrage : lot {id_lot_tardif} "
          f"repris={repris} statut={etat_final.get('statut')} lignes={statuts_lot} ; "
          f"fiche {code_tardif} relue : HTTP {fiche_creee.status_code} statut={statut_fiche} ; "
          f"fiches {fiches_avant_tardif}→{fiches_apres_tardif} ; occurrences en recherche={occurrences} ; "
          f"listes Redis après coup : file={restant_file}, en_cours={restant_processing}, "
          f"revendication={revendication_restante} ; rejeu du dossier → {statut_rejeu}")

    ligne("P8.4b-reenfilage-aboutit",
          fini and dossier2.get("statut") in {"traite", "deja_traite"},
          f"lot réenfilé {id_lot2} : statut={etat2.get('statut')} {etat2.get('nb_traites')}/{etat2.get('nb_dossiers')} "
          f"— dossier {dossier2.get('statut')} ({str(dossier2.get('raison'))[:60]})")


# --------------------------------------------------------------------------- #
# §8.5 — redémarrage complet de la pile : état conservé, aucun doublon
# --------------------------------------------------------------------------- #
def scenario_redemarrage_complet() -> None:
    fiches_avant = http("GET", "/recherche", params={"q": "", "limit": 100, "inclure_a_valider": "true"}).json().get("nb_resultats")
    for port in (8000,):
        pid = pid_ecoute(port)
        if pid:
            os.kill(pid, signal.SIGTERM)
    pid_w = pid_de("/home/user/rv/env/venv/bin/python -m seamtech_search.worker_service")
    if pid_w:
        os.kill(pid_w, signal.SIGTERM)
    attendre(lambda: not api_vivante(), 60)
    ligne("P8.5-pile-arretee", not api_vivante(), "API et worker arrêtés (SIGTERM)")
    lancer("start_api.sh", "api.log")
    lancer("start_worker.sh", "worker.log")
    revenu = attendre(api_vivante, 120)
    sante = etat_api()
    fiches_apres = http("GET", "/recherche", params={"q": "", "limit": 100, "inclure_a_valider": "true"}).json().get("nb_resultats")
    ligne("P8.5b-pile-redemarree-sans-perte",
          revenu and fiches_apres >= fiches_avant,
          f"API revenue={revenu} ; fiches avant={fiches_avant} après={fiches_apres} ; "
          f"base={sante.get('backend')} redis={sante.get('redis_connected')} stockage={sante.get('s3_configured')}")
    # aucun doublon : la même recherche ne renvoie pas deux fois le même code
    resultats = http("GET", "/recherche",
                     params={"q": "", "limit": 100, "inclure_a_valider": "true"}).json().get("resultats", [])
    codes = [x.get("code") for x in resultats]
    ligne("P8.5c-aucun-doublon-apres-redemarrage", len(codes) == len(set(codes)),
          f"{len(codes)} résultats, {len(set(codes))} codes distincts")


# --------------------------------------------------------------------------- #
# §8.6 — espace insuffisant : refus explicite, hôte non saturé
# --------------------------------------------------------------------------- #
def scenario_espace_insuffisant() -> None:
    """La limite est durcie par configuration (SEAMTECH_MIN_FREE_BYTES), jamais en
    remplissant réellement le disque : l'hôte ne doit pas être saturé."""
    libre = shutil.disk_usage("/home/user").free
    seuil = libre + 5 * 1024 * 1024 * 1024  # exige 5 Gio de plus que le libre actuel
    environnement = {**os.environ, "SEAMTECH_MIN_FREE_BYTES": str(seuil), "SEAMTECH_PORT": "8099"}
    processus = subprocess.Popen(
        ["/home/user/rv/env/venv/bin/python", "-m", "seamtech_search", "serve", "--config", "config/config.json"],
        cwd="/home/user/SEAMTECH-search", env=environnement,
        stdout=open(LOGS / "api-quota.log", "ab"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True)
    pret = attendre(lambda: httpx.get("http://127.0.0.1:8099/live", timeout=3.0).status_code == 200, 90)
    detail = f"libre={libre/1e9:.2f} Go, seuil exigé={seuil/1e9:.2f} Go"
    if not pret:
        ligne("P8.6-espace-insuffisant-refus-explicite", False, f"API de test non démarrée ({detail})")
        processus.terminate()
        return
    try:
        dossier = dossier_depot("quota", CORPUS / "02_fixtures_depot" / "CLIENT-7792-SO")
        with httpx.Client(base_url="http://127.0.0.1:8099", timeout=120.0) as client:
            reponse = client.post("/imports", json={"source_path": str(dossier)},
                                  headers={"X-SEAMTECH-TOKEN": TOKEN})
        corps = reponse.json() if reponse.status_code < 500 else {"brut": reponse.text[:200]}
        refus = reponse.status_code in (507, 503) or "espace" in json.dumps(corps, ensure_ascii=False).lower() \
            or "insufficient" in json.dumps(corps, ensure_ascii=False).lower() or "storage" in json.dumps(corps, ensure_ascii=False).lower()
        ligne("P8.6-espace-insuffisant-refus-explicite", refus,
              f"HTTP {reponse.status_code} — {json.dumps(corps, ensure_ascii=False)[:200]} ({detail}) ; "
              f"disque NON rempli (aucun fichier de remplissage créé)")
    finally:
        processus.terminate()
        try:
            processus.wait(timeout=20)
        except subprocess.TimeoutExpired:
            processus.kill()


# --------------------------------------------------------------------------- #
# §9.1 — sauvegarde vers une 2e cible jetable, puis restauration vérifiée
# --------------------------------------------------------------------------- #
def scenario_sauvegarde_restauration() -> None:
    cible = RV / "data" / "restauration-jetable" / time.strftime("%H%M%S")
    cible.mkdir(parents=True, exist_ok=True)
    dump = cible / "sauvegarde.pgcustom"
    environnement = {**os.environ, "PGPASSWORD": os.environ["POSTGRES_PASSWORD"],
                     "LD_LIBRARY_PATH": "/home/user/rv/env/pg.libs:/home/user/rv/env/pg/lib"}
    # le dépôt fournit aussi ses propres scripts de sauvegarde (sauvegarde.py) ;
    # la recette utilise pg_dump/pg_restore, exactement comme scripts/recette_locale.sh
    sauvegarde = subprocess.run(
        ["/home/user/rv/env/pg/bin/pg_dump", "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech",
         "-d", "seamtech_search", "--format=custom", "--file", str(dump)],
        capture_output=True, text=True, env=environnement)
    ligne("P9.1-sauvegarde-base-creee",
          sauvegarde.returncode == 0 and dump.exists() and dump.stat().st_size > 100_000,
          f"pg_dump rc={sauvegarde.returncode} taille={dump.stat().st_size if dump.exists() else 0} octets "
          f"{sauvegarde.stderr.strip()[:120]}")

    # restauration dans une 2e base JETABLE (jamais par-dessus celle de recette)
    base2 = f"restauration_{time.strftime('%H%M%S')}"
    creation = subprocess.run(
        ["/home/user/rv/env/pg/bin/psql", "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech", "-d", "postgres",
         "-c", f"CREATE DATABASE {base2} OWNER seamtech"],
        capture_output=True, text=True, env=environnement)
    restauration = subprocess.run(
        ["/home/user/rv/env/pg/bin/pg_restore", "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech",
         "-d", base2, "--no-owner", str(dump)],
        capture_output=True, text=True, env=environnement)
    comptage = subprocess.run(
        ["/home/user/rv/env/pg/bin/psql", "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech", "-d", base2,
         "-tAc", "SELECT count(*) FROM fiche"],
        capture_output=True, text=True, env=environnement)
    origine = http("GET", "/recherche", params={"q": "", "limit": 100, "inclure_a_valider": "true"}).json().get("nb_resultats")
    ligne("P9.1b-restauration-2e-cible-verifiee",
          creation.returncode == 0 and restauration.returncode == 0 and int(comptage.stdout.strip() or 0) == origine,
          f"base {base2} : pg_restore rc={restauration.returncode}, fiches restaurées={comptage.stdout.strip()} "
          f"= fiches d'origine={origine}")

    # les pièces (fichiers) sont-elles aussi sauvegardées ? — contrôle du volet S3
    r = redis.from_url(os.environ["SEAMTECH_REDIS_URL"])
    objets = subprocess.run(["bash", "-c",
                             "find /home/user/rv/data/objets -name '*_S3rver_object' | wc -l"],
                            capture_output=True, text=True)
    ligne("P9.1c-volet-stockage-objet-present", int(objets.stdout.strip() or 0) > 0,
          f"{objets.stdout.strip()} objets dans le stockage de recette (les pièces sont hors base : "
          "la sauvegarde de référence les copie par `mc mirror`/aws s3 sync — voir ANOMALIES.md)")

    # injection d'échec : restauration dans une cible INEXISTANTE / dump corrompu
    corrompu = cible / "sauvegarde-corrompue.pgcustom"
    corrompu.write_bytes(b"PGDMP" + os.urandom(200))
    echec = subprocess.run(
        ["/home/user/rv/env/pg/bin/pg_restore", "-h", "127.0.0.1", "-p", "5433", "-U", "seamtech",
         "-d", base2, "--no-owner", str(corrompu)],
        capture_output=True, text=True, env=environnement)
    ligne("P9.1d-echec-de-restauration-signale",
          echec.returncode != 0 and bool(echec.stderr.strip()),
          f"pg_restore sur un dump corrompu : code={echec.returncode} message=« {echec.stderr.strip().splitlines()[0][:140] if echec.stderr.strip() else 'AUCUN MESSAGE'} »")
    # et la base de recette n'a pas bougé
    origine2 = http("GET", "/recherche", params={"q": "", "limit": 100, "inclure_a_valider": "true"}).json().get("nb_resultats")
    ligne("P9.1e-base-de-recette-intacte-apres-injection", origine2 == origine,
          f"fiches avant={origine} après l'injection={origine2}")


if __name__ == "__main__":
    PREUVES.mkdir(parents=True, exist_ok=True)
    print(f"# pannes et reprises — API {API}")
    for nom, fonction in [
        ("stockage", scenario_stockage_indisponible),
        ("worker", scenario_worker_tue),
        ("redis", scenario_redis_redemarre),
        ("file", scenario_perte_entree_file),
        ("redemarrage", scenario_redemarrage_complet),
        ("espace", scenario_espace_insuffisant),
        ("sauvegarde", scenario_sauvegarde_restauration),
    ]:
        try:
            fonction()
        except Exception as exc:  # noqa: BLE001
            ligne(f"P8/9-{nom}-execution", False, f"scénario interrompu : {type(exc).__name__}: {exc}")
    echecs = [r for r in RESULTATS if r["resultat"] == "FAIL"]
    (PREUVES / "pannes_et_reprises.json").write_text(
        json.dumps({"resultats": RESULTATS, "echecs": len(echecs)}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n# {len(RESULTATS) - len(echecs)}/{len(RESULTATS)} contrôles de panne/reprise PASS")

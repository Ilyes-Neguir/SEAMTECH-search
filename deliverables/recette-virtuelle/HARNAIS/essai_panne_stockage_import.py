#!/usr/bin/env python3
"""Essai ciblé A-04 : que fait un import QUAND le stockage objet est indisponible ?

Protocole :
  1. relever l'état AVANT : nombre d'objets écrits dans le stockage, compteurs /health ;
  2. arrêter le serveur de stockage (SIGTERM) et vérifier que l'écriture échoue vraiment ;
  3. déposer un dossier JAMAIS importé (copie fraîche → nouvelle clé d'idempotence) ;
  4. relever la réponse de l'API, l'état APRÈS côté base et côté stockage ;
  5. redémarrer le stockage et voir si les objets manquants apparaissent après coup
     (écriture différée) ou s'ils manquent définitivement ;
  6. rouvrir un document déposé pendant la panne et vérifier qu'il est réellement servi.

Sortie : CONTROLE|… + preuves/essai_panne_stockage_import.json
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

API = "http://127.0.0.1:8000"
TOKEN = os.environ["SEAMTECH_AUTH_TOKEN"]
CORPUS = Path("/home/user/rv/corpus/sources")
ZONE = Path("/home/user/rv/data/recette-lot")
OBJETS = Path(os.environ.get("S3_DIRECTORY", "/home/user/rv/data/objets"))
HARNAIS = Path("/home/user/rv/harnais")
PREUVES = Path("/home/user/rv/preuves")
CLIENT = httpx.Client(base_url=API, timeout=300.0, headers={"X-SEAMTECH-TOKEN": TOKEN})

RESULTATS: list[dict] = []


def ligne(identifiant: str, ok: bool, detail: str = "") -> None:
    RESULTATS.append({"id": identifiant, "resultat": "PASS" if ok else "FAIL", "detail": detail})
    print(f"CONTROLE|{identifiant}|{'PASS' if ok else 'FAIL'}|{detail}", flush=True)


def objets_stockage() -> int:
    if not OBJETS.exists():
        return 0
    return sum(1 for p in OBJETS.rglob("*") if p.is_file())


def sante() -> dict:
    try:
        return CLIENT.get("/health").json()
    except Exception as exc:  # noqa: BLE001
        return {"erreur": f"{type(exc).__name__}: {exc}"}


def total_pieces() -> int:
    return CLIENT.get("/pieces", params={"limit": 1}).json().get("total", -1)


def ecriture_stockage() -> tuple[bool, str]:
    import boto3
    from botocore.config import Config as BotoConfig
    client = boto3.client("s3", endpoint_url=os.environ["SEAMTECH_S3_ENDPOINT_URL"],
                          aws_access_key_id=os.environ["SEAMTECH_S3_ACCESS_KEY"],
                          aws_secret_access_key=os.environ["SEAMTECH_S3_SECRET_KEY"],
                          region_name="us-east-1",
                          config=BotoConfig(signature_version="s3v4",
                                            s3={"addressing_style": "path"}, connect_timeout=3, read_timeout=5))
    try:
        client.put_object(Bucket=os.environ["SEAMTECH_S3_BUCKET"], Key="_recette/sonde.txt", Body=b"s")
        return False, "acceptée"
    except Exception as exc:  # noqa: BLE001
        return True, f"refusée ({type(exc).__name__})"


def pid_port(port: int) -> int | None:
    sortie = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    for ligne_texte in sortie.splitlines():
        if f":{port}" in ligne_texte and "pid=" in ligne_texte:
            return int(ligne_texte.split("pid=")[1].split(",")[0])
    return None


def main() -> int:
    rapport: dict[str, object] = {}
    objets_avant = objets_stockage()
    sante_avant = sante()
    pieces_avant = total_pieces()
    rapport["avant"] = {"objets_stockage": objets_avant, "pieces_catalogue": pieces_avant,
                        "documents_health": sante_avant.get("documents")}
    print(f"# avant : {objets_avant} objets dans le stockage, {pieces_avant} pièces, "
          f"documents={sante_avant.get('documents')}")

    pid = pid_port(9000)
    ligne("A04.0-stockage-identifie", pid is not None, f"serveur de stockage pid={pid} (port 9000)")
    if pid is None:
        return 1
    os.kill(pid, signal.SIGTERM)
    panne = False
    for _ in range(30):
        panne, detail = ecriture_stockage()
        if panne:
            break
        time.sleep(1)
    ligne("A04.1-panne-effective", panne, f"écriture vers le stockage : {detail}")

    # Dossier jamais importé : copie fraîche (nouveau chemin → nouvelle clé d'idempotence)
    source = CORPUS / "01_reels_anonymises" / "BAVARIA 34 - 250323JA"
    cible = ZONE / "affaires" / f"panne-stockage-a04-{time.strftime('%H%M%S')}" / source.name
    cible.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, cible)
    rapport["dossier_importe"] = str(cible)

    reponse = CLIENT.post("/imports/dossier", json={"dossier": str(cible)})
    corps = reponse.json() if reponse.status_code < 500 else {"texte": reponse.text[:300]}
    rapport["reponse_import"] = {"http": reponse.status_code, **{k: corps.get(k) for k in
                                 ("statut", "fiche", "pieces", "raison", "id_lot")}}
    print(f"# réponse : HTTP {reponse.status_code} {json.dumps(rapport['reponse_import'], ensure_ascii=False)[:220]}")

    time.sleep(3)
    objets_pendant = objets_stockage()
    pieces_apres = total_pieces()
    sante_apres = sante()
    rapport["pendant"] = {"objets_stockage": objets_pendant, "pieces_catalogue": pieces_apres,
                          "documents_health": sante_apres.get("documents")}
    nouveau = corps.get("statut") == "traite"
    ligne("A04.2-import-annonce-traite-pendant-la-panne", nouveau,
          f"statut annoncé={corps.get('statut')} fiche={corps.get('fiche')} pièces={corps.get('pieces')} "
          f"→ catalogue {pieces_avant}→{pieces_apres}, documents /health={sante_apres.get('documents')}")
    ligne("A04.3-aucune-ecriture-dans-le-stockage-pendant-la-panne", objets_pendant == objets_avant,
          f"objets dans le stockage : {objets_avant} avant → {objets_pendant} pendant "
          "(le serveur est arrêté : toute écriture devrait échouer)")

    # Le document est-il servi malgré tout (copie locale) ?
    pieces = CLIENT.get("/pieces", params={"limit": 5, "tri": "recent"}).json().get("pieces") or []
    servis = []
    for piece in pieces[:5]:
        identifiant = piece.get("id") or piece.get("piece_id")
        rep = CLIENT.get(f"/pieces/{identifiant}/telecharger")
        servis.append((identifiant, rep.status_code, len(rep.content)))
    rapport["pieces_servies_pendant_la_panne"] = servis
    ligne("A04.4-documents-servis-malgre-la-panne", any(s[1] == 200 and s[2] > 1000 for s in servis),
          f"téléchargements par id pendant la panne : {servis}")

    # Reprise du stockage
    subprocess.Popen(["bash", str(HARNAIS / "start_s3.sh")],
                     stdout=open("/home/user/rv/logs/s3.log", "a"), stderr=subprocess.STDOUT,
                     start_new_session=True, stdin=subprocess.DEVNULL)
    remonte = False
    for _ in range(60):
        ok, _ = ecriture_stockage()
        if not ok:
            remonte = True
            break
        time.sleep(1)
    time.sleep(10)
    objets_apres = objets_stockage()
    rapport["apres"] = {"objets_stockage": objets_apres, "pieces_catalogue": total_pieces(),
                        "documents_health": sante().get("documents")}
    ligne("A04.5-stockage-remonte", remonte, f"écriture de nouveau acceptée ; objets={objets_apres} "
          f"(avant la panne : {objets_avant}, pendant : {objets_pendant})")
    ligne("A04.6-objets-manquants-rattrapes-apres-la-panne", objets_apres > objets_pendant,
          f"objets rattrapés après reprise : {objets_apres - objets_pendant} "
          f"(aucun rattrapage = les documents déposés pendant la panne n'ont pas d'objet dans le stockage)")

    PREUVES.mkdir(parents=True, exist_ok=True)
    (PREUVES / "essai_panne_stockage_import.json").write_text(
        json.dumps({"rapport": rapport, "resultats": RESULTATS}, indent=2, ensure_ascii=False), encoding="utf-8")
    echecs = [r for r in RESULTATS if r["resultat"] == "FAIL"]
    print(f"\n# essai A-04 : {len(RESULTATS) - len(echecs)}/{len(RESULTATS)} contrôles PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

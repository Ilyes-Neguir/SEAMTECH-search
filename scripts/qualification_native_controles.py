#!/usr/bin/env python3
"""Contrôles d'un essai d'installation NATIVE (pas Docker Compose).

Exécuté par scripts/qualification_native.sh après démarrage des processus
PostgreSQL, Redis, MinIO, API, worker et frontend. Sortie :
  CONTROLE|<id>|PASS|détail
  CONTROLE|<id>|FAIL|détail
Code 0 si aucun FAIL.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = os.environ.get("SEAMTECH_API_URL", "http://127.0.0.1:8000").rstrip("/")
FRONT = os.environ.get("SEAMTECH_FRONT_URL", "http://127.0.0.1:3000").rstrip("/")
JETON = os.environ["SEAMTECH_AUTH_TOKEN"]
IDENTIFIANT = os.environ.get("SEAMTECH_NATIF_IDENTIFIANT", "natif-essai")
MOT_DE_PASSE = os.environ["SEAMTECH_NATIF_MOT_DE_PASSE"]
RACINE_IMPORT = Path(os.environ["SEAMTECH_NATIF_IMPORT"])
DSN = os.environ["SEAMTECH_DATABASE_URL"]
DSN_RESTORE = os.environ["SEAMTECH_RESTORE_DATABASE_URL"]
DOSSIER_SAUVEGARDE = Path(os.environ.get("SEAMTECH_NATIF_BACKUP", "data/backups-natif"))
FICHIERS_RESTORE = Path(os.environ.get("SEAMTECH_NATIF_RESTORE_FILES", "/tmp/seamtech-natif-restore-files"))
TIMEOUT_LOT = int(os.environ.get("SEAMTECH_NATIF_TIMEOUT_LOT", "240"))

NB_FAIL = 0


def _ligne(identifiant: str, ok: bool, detail: str) -> None:
    global NB_FAIL
    statut = "PASS" if ok else "FAIL"
    if not ok:
        NB_FAIL += 1
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=native/{identifiant}::{detail[:1500]}")
    print(f"CONTROLE|{identifiant}|{statut}|{detail}", flush=True)


def _appel(
    methode: str,
    chemin: str,
    corps: dict[str, Any] | None = None,
    entetes: dict[str, str] | None = None,
    brut: bool = False,
    base: str | None = None,
) -> tuple[int, Any, bytes]:
    url = (base or API) + chemin
    data = None if corps is None else json.dumps(corps).encode("utf-8")
    headers = {"X-SEAMTECH-TOKEN": JETON, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if entetes:
        headers.update(entetes)
    req = urllib.request.Request(url, data=data, headers=headers, method=methode)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            code = resp.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        code = exc.code
    if brut:
        return code, None, raw
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except json.JSONDecodeError:
        parsed = raw.decode("utf-8", errors="replace")[:300]
    return code, parsed, raw


def sante() -> None:
    code, corps, _ = _appel("GET", "/live")
    _ligne("api-live", code == 200, f"GET /live HTTP {code}")
    code, corps, _ = _appel("GET", "/health")
    ok = code == 200 and isinstance(corps, dict)
    _ligne("api-health", ok, f"GET /health HTTP {code} {str(corps)[:180]}")
    try:
        req = urllib.request.Request(FRONT + "/login", method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            front_code = resp.getcode()
            page = resp.read()[:200]
    except Exception as exc:  # noqa: BLE001
        front_code, page = 0, str(exc)
    _ligne("frontend-login", front_code == 200, f"GET {FRONT}/login HTTP {front_code} {page[:80]!r}")


def connexion() -> None:
    code, corps, _ = _appel(
        "POST",
        "/auth/connexion",
        {"identifiant": IDENTIFIANT, "mot_de_passe": MOT_DE_PASSE},
    )
    ok = code == 200 and isinstance(corps, dict) and bool(corps.get("id_session"))
    _ligne("connexion-nominative", ok, f"POST /auth/connexion HTTP {code} {str(corps)[:180]}")


def _preparer_affaire_ocr() -> Path:
    dest = RACINE_IMPORT / "AFFAIRE-OCR-NATIF"
    dest.mkdir(parents=True, exist_ok=True)
    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ocr"
    for nom in ("ocr_propre.pdf", "ocr_degrade.pdf"):
        src = fixtures / nom
        if src.is_file():
            shutil.copyfile(src, dest / nom)
    return dest


def import_et_worker() -> int | None:
    affaire = _preparer_affaire_ocr()
    sample = Path(__file__).resolve().parent.parent / "sample_data"
    if sample.is_dir():
        for enfant in sample.iterdir():
            if enfant.is_dir() and enfant.name.startswith("CLIENT-"):
                cible = RACINE_IMPORT / enfant.name
                if not cible.exists():
                    shutil.copytree(enfant, cible, dirs_exist_ok=True)
                break
    code, corps, _ = _appel(
        "POST",
        "/imports/dossier/lot",
        {"racine": str(RACINE_IMPORT), "notes": "essai natif"},
        entetes={"X-SEAMTECH-BACKGROUND": "true"},
    )
    id_lot = corps.get("id_lot") if isinstance(corps, dict) else None
    durability = corps.get("durability") if isinstance(corps, dict) else None
    ok = code == 202 and isinstance(id_lot, int)
    _ligne(
        "import-lot",
        ok,
        f"HTTP {code} id_lot={id_lot} durability={durability} affaire={affaire.name}",
    )
    if not ok:
        return None
    debut = time.time()
    etat: dict[str, Any] = {}
    while time.time() - debut < TIMEOUT_LOT:
        c2, corps2, _ = _appel("GET", f"/lots/{id_lot}")
        if c2 == 200 and isinstance(corps2, dict):
            etat = corps2
            if corps2.get("statut") in {"termine", "interrompu"}:
                break
        time.sleep(3)
    statut = etat.get("statut")
    nb_traites = int(etat.get("nb_traites") or 0)
    nb_echecs = int(etat.get("nb_echecs") or 0)
    ok_lot = statut == "termine" and nb_echecs == 0 and nb_traites > 0
    _ligne(
        "worker-lot",
        ok_lot,
        f"statut={statut} traites={nb_traites} echecs={nb_echecs} {str(etat)[:200]}",
    )
    return int(id_lot) if ok_lot else None


def recherche_et_ocr() -> str | None:
    path_ouvert: str | None = None
    for requete, etiquette in (("SEAMTECH", "ocr-fra"), ("GENOA", "ocr-eng"), ("7792", "fonds-sample")):
        code, corps, _ = _appel("GET", "/search?q=" + urllib.parse.quote(requete))
        hits = []
        if code == 200 and isinstance(corps, dict):
            hits = corps.get("results") or corps.get("resultats") or []
        ok = code == 200 and bool(hits)
        _ligne(f"recherche-{etiquette}", ok, f"q={requete!r} HTTP {code} n={len(hits)}")
        if ok and path_ouvert is None:
            premier = hits[0]
            path_ouvert = premier.get("path") or premier.get("chemin")
            ident = premier.get("id")
            if not path_ouvert and ident is not None:
                try:
                    import psycopg2

                    with psycopg2.connect(DSN) as cnx:
                        with cnx.cursor() as cur:
                            cur.execute("SELECT path FROM documents WHERE id = %s", (int(ident),))
                            ligne = cur.fetchone()
                            if ligne:
                                path_ouvert = ligne[0]
                except Exception as exc:  # noqa: BLE001
                    _ligne("recherche-path", False, f"path introuvable ({exc})")
    code, corps, _ = _appel("GET", "/recherche?q=SEAMTECH")
    hits = corps.get("resultats") or corps.get("results") or [] if isinstance(corps, dict) else []
    _ligne("recherche-metier", code == 200, f"GET /recherche HTTP {code} n={len(hits) if isinstance(hits, list) else '?'}")
    return path_ouvert


def telechargement(path: str | None) -> None:
    if not path:
        _ligne("telechargement", False, "aucun path à ouvrir")
        return
    code, _, raw = _appel("POST", "/open?path=" + urllib.request.quote(path), brut=True)
    _ligne("telechargement", code == 200 and bool(raw), f"POST /open HTTP {code} octets={len(raw)}")


def sauvegarde_restauration() -> None:
    import subprocess

    DOSSIER_SAUVEGARDE.mkdir(parents=True, exist_ok=True)
    FICHIERS_RESTORE.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "seamtech_search.sauvegarde",
        "sauver",
        "--base-url",
        DSN,
        "--archive",
        str(RACINE_IMPORT),
        "--dossier",
        str(DOSSIER_SAUVEGARDE),
        "--avec-references",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    ok = proc.returncode in {0, 2} and proc.stdout.strip()
    _ligne(
        "sauvegarde",
        ok,
        f"rc={proc.returncode} stdout={proc.stdout.strip()[:240]} err={proc.stderr[-200:]}",
    )
    if not ok:
        return
    try:
        meta = json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        _ligne("sauvegarde-json", False, proc.stdout[:200])
        return
    manifeste = Path(meta["manifeste"])
    dump = manifeste.with_name(manifeste.name.replace(".manifest.json", ""))
    if not dump.is_file():
        # le manifeste pointe vers le nom du dump
        candidats = list(DOSSIER_SAUVEGARDE.glob("*.dump")) + list(DOSSIER_SAUVEGARDE.glob("*.sql"))
        dump = candidats[0] if candidats else dump
    sha_dump = hashlib.sha256(dump.read_bytes()).hexdigest() if dump.is_file() else ""
    _ligne("sauvegarde-dump", bool(sha_dump), f"dump={dump.name} sha256={sha_dump[:16]}")

    masque = RACINE_IMPORT.parent / "sources-masquees-natif"
    masque.mkdir(parents=True, exist_ok=True)
    deplaces: list[tuple[Path, Path]] = []
    if RACINE_IMPORT.exists():
        cible = masque / RACINE_IMPORT.name
        shutil.move(str(RACINE_IMPORT), str(cible))
        deplaces.append((cible, RACINE_IMPORT))
    try:
        cmd_r = [
            sys.executable,
            "-m",
            "seamtech_search.sauvegarde",
            "restaurer",
            "--manifeste",
            str(manifeste),
            "--base-cible",
            DSN_RESTORE,
            "--fichiers-cible",
            str(FICHIERS_RESTORE),
            "--restaurer-objets",
        ]
        proc_r = subprocess.run(cmd_r, capture_output=True, text=True, timeout=180, check=False)
        _ligne(
            "restauration-conjointe",
            proc_r.returncode == 0,
            f"rc={proc_r.returncode} {proc_r.stdout.strip()[:200]} {proc_r.stderr[-180:]}",
        )
        if dump.is_file():
            sha2 = hashlib.sha256(dump.read_bytes()).hexdigest()
            _ligne("restauration-sha-dump", sha2 == sha_dump, f"{sha_dump[:12]} vs {sha2[:12]}")
    finally:
        for src, dst in deplaces:
            if src.exists() and not dst.exists():
                shutil.move(str(src), str(dst))


def main() -> int:
    sante()
    connexion()
    import_et_worker()
    path = recherche_et_ocr()
    telechargement(path)
    sauvegarde_restauration()
    return 1 if NB_FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Contrôles d'un essai d'installation NATIVE (pas Docker Compose).

Exécuté par scripts/qualification_native.sh après démarrage des processus
PostgreSQL, Redis, MinIO, API, worker et frontend. Sortie :
  CONTROLE|<id>|PASS|détail
  CONTROLE|<id>|FAIL|détail
Code 0 si aucun FAIL.

RG14_EXCEPTION : client HTTP d'ESSAI natif (outillage, jamais importé par le
service). Cible LOOPBACK uniquement (127.0.0.1 API/frontend). urllib sert à
prouver le parcours réel sur la machine vierge, pas une dépendance runtime.
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
RACINE_OCR = Path(os.environ.get("SEAMTECH_NATIF_OCR", str(RACINE_IMPORT.parent / "ocr")))
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


def _preparer_sources() -> None:
    sample = Path(__file__).resolve().parent.parent / "sample_data" / "CLIENT-7792-SO"
    RACINE_IMPORT.mkdir(parents=True, exist_ok=True)
    if sample.is_dir():
        cible = RACINE_IMPORT / "CLIENT-7792-SO"
        if not cible.exists():
            shutil.copytree(sample, cible)
    RACINE_OCR.mkdir(parents=True, exist_ok=True)
    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ocr"
    for nom in ("ocr_propre.pdf", "ocr_degrade.pdf"):
        src = fixtures / nom
        if src.is_file():
            shutil.copyfile(src, RACINE_OCR / nom)


def _attendre_lot(id_lot: int) -> dict[str, Any]:
    debut = time.time()
    etat: dict[str, Any] = {}
    while time.time() - debut < TIMEOUT_LOT:
        c2, corps2, _ = _appel("GET", f"/lots/{id_lot}")
        if c2 == 200 and isinstance(corps2, dict):
            etat = corps2
            if corps2.get("statut") in {"termine", "interrompu"}:
                break
        time.sleep(3)
    return etat


def _attendre_job(job_id: str) -> dict[str, Any]:
    debut = time.time()
    etat: dict[str, Any] = {}
    while time.time() - debut < TIMEOUT_LOT:
        c2, corps2, _ = _appel("GET", f"/imports/{job_id}")
        if c2 == 200 and isinstance(corps2, dict):
            etat = corps2
            statut = str(corps2.get("status") or "").lower()
            if statut in {"completed", "failed", "cancelled", "error", "success"}:
                break
        time.sleep(3)
    return etat


def import_et_worker() -> int | None:
    _preparer_sources()
    code, corps, _ = _appel(
        "POST",
        "/imports/dossier/lot",
        {"racine": str(RACINE_IMPORT), "notes": "essai natif 7792-SO"},
        entetes={"X-SEAMTECH-BACKGROUND": "true"},
    )
    id_lot = corps.get("id_lot") if isinstance(corps, dict) else None
    durability = corps.get("durability") if isinstance(corps, dict) else None
    ok = code == 202 and isinstance(id_lot, int)
    _ligne("import-lot", ok, f"HTTP {code} id_lot={id_lot} durability={durability}")
    if not ok:
        return None
    etat = _attendre_lot(int(id_lot))
    statut = etat.get("statut")
    nb_traites = int(etat.get("nb_traites") or 0)
    nb_echecs = int(etat.get("nb_echecs") or 0)
    ok_lot = statut == "termine" and nb_echecs == 0 and nb_traites > 0
    _ligne("worker-lot", ok_lot, f"statut={statut} traites={nb_traites} echecs={nb_echecs} {str(etat)[:240]}")

    from seamtech_search.extractors import extract_file

    propre = RACINE_OCR / "ocr_propre.pdf"
    if propre.is_file():
        extra = extract_file(propre, max_chars=20_000, enable_ocr=True, tesseract_command="tesseract")
        texte = (extra.text or "").upper()
        _ligne(
            "ocr-tesseract-hote",
            extra.status == "extracted" and "SEAMTECH" in texte,
            f"status={extra.status} detail={str(extra.detail)[:120]} texte={texte[:120]!r}",
        )
        try:
            from seamtech_search.config import AppConfig
            from seamtech_search.crawler import crawl
            from seamtech_search.indexer import SearchIndex

            cfg = AppConfig.load()
            idx = SearchIndex(Path(cfg.database_path), DSN)
            n = idx.upsert_documents(list(crawl(cfg)))
            idx.close()
            _ligne("ocr-index-crawl", n >= 1, f"upsert_documents={n} (crawl racines, tesseract hôte)")
        except Exception as exc:  # noqa: BLE001
            _ligne("ocr-index-crawl", False, f"{type(exc).__name__}: {exc}"[:240])
    else:
        _ligne("ocr-tesseract-hote", False, f"fixture absente {propre}")
    return int(id_lot) if ok_lot else None


def recherche_et_ocr() -> str | None:
    path_ouvert: str | None = None
    code, corps, _ = _appel("GET", "/recherche?q=" + urllib.parse.quote("7792"))
    hits: list[Any] = []
    if code == 200 and isinstance(corps, dict):
        hits = corps.get("resultats") or corps.get("results") or []
    _ligne("recherche-metier-7792", code == 200 and bool(hits), f"GET /recherche q=7792 HTTP {code} n={len(hits)} {str(corps)[:180]}")

    for requete, etiquette in (("SEAMTECH", "ocr-fra"), ("Sailonet", "fonds-sample")):
        c2, corps2, _ = _appel("GET", "/search?q=" + urllib.parse.quote(requete))
        hits2: list[Any] = []
        if c2 == 200 and isinstance(corps2, dict):
            hits2 = corps2.get("results") or corps2.get("resultats") or []
        ok = c2 == 200 and bool(hits2)
        _ligne(f"recherche-{etiquette}", ok, f"GET /search q={requete!r} HTTP {c2} n={len(hits2)}")
        if ok and path_ouvert is None:
            premier = hits2[0]
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
    return path_ouvert


def telechargement(path: str | None) -> None:
    if not path:
        try:
            import psycopg2

            with psycopg2.connect(DSN) as cnx:
                with cnx.cursor() as cur:
                    cur.execute("SELECT path FROM documents WHERE path IS NOT NULL LIMIT 1")
                    ligne = cur.fetchone()
                    if ligne:
                        path = ligne[0]
        except Exception as exc:  # noqa: BLE001
            _ligne("telechargement", False, f"aucun path ({exc})")
            return
    if not path:
        _ligne("telechargement", False, "aucun path à ouvrir")
        return
    code, _, raw = _appel("POST", "/open?path=" + urllib.parse.quote(str(path)), brut=True)
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
        import traceback

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
        err = (proc_r.stderr or "")[-500:].replace("\n", " | ")
        out = (proc_r.stdout or "").strip()[:300]
        _ligne(
            "restauration-conjointe",
            proc_r.returncode == 0,
            f"rc={proc_r.returncode} out={out} err={err}",
        )
        if dump.is_file():
            sha2 = hashlib.sha256(dump.read_bytes()).hexdigest()
            _ligne("restauration-sha-dump", sha2 == sha_dump, f"{sha_dump[:12]} vs {sha2[:12]}")
    except Exception as exc:  # noqa: BLE001
        _ligne("restauration-conjointe", False, f"{type(exc).__name__}: {exc} {traceback.format_exc()[-400:]}")
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

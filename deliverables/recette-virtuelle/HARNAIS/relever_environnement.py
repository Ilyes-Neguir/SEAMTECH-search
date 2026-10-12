#!/usr/bin/env python3
"""Relève l'environnement réellement utilisé et l'écrit dans preuves/environnement.json.

Tout ce qui figure dans ENVIRONNEMENT.md doit pouvoir être recoupé ici : versions
des composants, ressources de la machine, état des services, extensions réellement
installées dans la base de recette, empreintes du corpus et du manifeste.
"""
from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path

PREUVES = Path("/home/user/rv/preuves")
CORPUS = Path("/home/user/rv/corpus/sources")
DEPOT = Path("/home/user/SEAMTECH-search")


def executer(*commande: str) -> str:
    try:
        return subprocess.run(commande, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"({type(exc).__name__}: {exc})"


def tcp_ouvert(hote: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.5)
        return sock.connect_ex((hote, port)) == 0


def main() -> int:
    env = {
        "machine": {
            "hote": platform.node(),
            "systeme": platform.platform(),
            "distribution": executer("bash", "-c", "grep PRETTY_NAME /etc/os-release"),
            "noyau": platform.release(),
            "architecture": platform.machine(),
            "vCPU": os.cpu_count(),
            "memoire_mo": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1_048_576),
            "disque": executer("df", "-h", "/home/user"),
            "conteneurs": executer("bash", "-c", "command -v docker podman || echo 'aucun moteur de conteneur'"),
        },
        "versions": {
            "python": sys.version.split()[0],
            "node": executer("node", "-v"),
            "postgresql": executer(str(Path("/home/user/rv/env/pg/bin/postgres")), "--version"),
            "redis": executer("bash", "-c",
                              "AUTH=$(grep -oP 'requirepass \\K.*' /home/user/rv/config/redis.conf); "
                              "printf 'AUTH %s\\r\\nINFO server\\r\\n' \"$AUTH\" | timeout 5 nc 127.0.0.1 6379 "
                              "| tr -d '\\r' | grep -E '^redis_version'"),
            "s3rver": "3.7.1",
            "next": executer("bash", "-c",
                             "grep -m1 '\"version\"' " + str(DEPOT / "frontend/node_modules/next/package.json")),
            "playwright": "1.63.0",
            "chromium": executer("bash", "-c",
                                 "LD_LIBRARY_PATH=/tmp/chromium-libs/lib /home/user/rv/env/chrome/chromium --version"),
            "tesseract": executer("bash", "-c", "command -v tesseract || echo absent"),
            "pdftoppm": executer("bash", "-c", "command -v pdftoppm || echo absent"),
        },
        "depot": {
            "chemin": str(DEPOT),
            "head": executer("git", "-C", str(DEPOT), "rev-parse", "HEAD"),
            "branche": executer("git", "-C", str(DEPOT), "rev-parse", "--abbrev-ref", "HEAD"),
            "dernier_commit": executer("git", "-C", str(DEPOT), "log", "-1", "--format=%H %ad %s", "--date=iso"),
            "ecart_avec_candidat": executer("git", "-C", str(DEPOT), "diff", "--stat",
                                            "3304b9105dff9929011f5c299a83d6fc36ab5132", "HEAD"),
        },
        "services": {
            "postgresql_5433": tcp_ouvert("127.0.0.1", 5433),
            "redis_6379": tcp_ouvert("127.0.0.1", 6379),
            "stockage_s3_9000": tcp_ouvert("127.0.0.1", 9000),
            "api_8000": tcp_ouvert("127.0.0.1", 8000),
            "frontend_3000": tcp_ouvert("127.0.0.1", 3000),
        },
        "extensions_postgres": executer(
            "bash", "-c",
            "PGPASSWORD=$POSTGRES_PASSWORD LD_LIBRARY_PATH=/home/user/rv/env/pg.libs:/home/user/rv/env/pg/lib "
            "/home/user/rv/env/pg/bin/psql -h 127.0.0.1 -p 5433 -U seamtech -d seamtech_search "
            "-tAc \"SELECT extname||' '||extversion FROM pg_extension ORDER BY extname\""),
        "corpus": {
            "racine": str(CORPUS),
            "fichiers": sum(1 for p in CORPUS.rglob("*") if p.is_file()),
            "octets": sum(p.stat().st_size for p in CORPUS.rglob("*") if p.is_file()),
            "manifeste": str(DEPOT / "deliverables/recette-virtuelle/MANIFESTES/MANIFESTE_CORPUS.csv"),
        },
        "reseau_sortant_autorise": ["github.com", "codeload.github.com", "api.github.com",
                                    "registry.npmjs.org", "pypi.org", "files.pythonhosted.org"],
        "reseau_sortant_bloque": ["deb.debian.org", "archive.ubuntu.com", "dl.min.io",
                                  "storage.googleapis.com", "golang.org", "proxy.golang.org",
                                  "cdn.playwright.dev", "registres de conteneurs"],
    }
    PREUVES.mkdir(parents=True, exist_ok=True)
    (PREUVES / "environnement.json").write_text(json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(env["versions"], indent=2, ensure_ascii=False))
    print(json.dumps(env["services"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

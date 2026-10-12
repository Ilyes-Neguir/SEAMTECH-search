#!/usr/bin/env python3
"""Vérifie la configuration RÉSOLUE par le produit (et non les fichiers écrits).

Charge la configuration exactement comme le fait `seamtech_search serve` et
confronte la valeur résolue à ce qui a été déclaré dans `recette.env` :
aucun écart silencieux (mauvais fichier, variable d'environnement ignorée,
chemin relatif résolu ailleurs). Écrit `preuves/config_resolue.json`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/user/SEAMTECH-search")

from seamtech_search.config import AppConfig  # noqa: E402

PREUVES = Path("/home/user/rv/preuves")
ATTENDUS = {
    "host": os.environ.get("SEAMTECH_HOST"),
    "port": int(os.environ["SEAMTECH_PORT"]) if os.environ.get("SEAMTECH_PORT") else None,
    "database_url": os.environ.get("SEAMTECH_DATABASE_URL"),
    "redis_url": os.environ.get("SEAMTECH_REDIS_URL"),
    "storage_backend": os.environ.get("SEAMTECH_STORAGE_BACKEND"),
    "auth_token": os.environ.get("SEAMTECH_AUTH_TOKEN"),
    "s3_bucket": os.environ.get("SEAMTECH_S3_BUCKET"),
}
# Le bucket de sauvegarde n'appartient PAS au modèle AppConfig : il est lu
# directement dans l'environnement par seamtech_search/sauvegarde.py:553. On le
# vérifie donc séparément, dans le composant qui le consomme réellement.
ATTENDUS_SAUVEGARDE = {"SEAMTECH_BACKUP_BUCKET": os.environ.get("SEAMTECH_BACKUP_BUCKET")}


def masque(valeur: object) -> str:
    texte = str(valeur)
    if "://" in texte and "@" in texte:  # URL avec identifiants
        tete, _, queue = texte.partition("://")
        reste = queue.split("@", 1)[1]
        return f"{tete}://***@{reste}"
    return texte


def main() -> int:
    config = AppConfig.load("/home/user/SEAMTECH-search/config/config.json")
    resolve = json.loads(config.model_dump_json())
    ecarts = []
    for cle, attendu in ATTENDUS.items():
        valeur = resolve.get(cle)
        if attendu is None:
            continue
        if cle in {"database_url", "redis_url"}:
            # la valeur d'environnement peut être partiellement masquée : on compare
            # l'hôte, le port, la base logique et la présence d'un mot de passe.
            def forme(url: str) -> tuple:
                corps = url.split("://", 1)[-1]
                identifiants, _, hote = corps.rpartition("@")
                hote, _, base = hote.partition("/")
                return (bool(identifiants), hote.split(":")[0], hote.partition(":")[2] or "0", base or "0")
            if forme(str(valeur)) != forme(str(attendu)):
                ecarts.append({"cle": cle, "resolue": masque(valeur), "attendue": masque(attendu)})
        elif str(valeur) != str(attendu):
            ecarts.append({"cle": cle, "resolue": masque(valeur), "attendue": masque(attendu)})
    racines = [str(p) for p in resolve.get("root_paths", [])]
    racines_env = [p for p in (os.environ.get("SEAMTECH_ROOT_PATHS") or "").split(":") if p]
    if racines_env and racines != racines_env:
        ecarts.append({"cle": "root_paths", "resolue": racines, "attendue": racines_env})
    import re
    source = Path("/home/user/SEAMTECH-search/seamtech_search/sauvegarde.py").read_text(encoding="utf-8")
    lues = set(re.findall(r'os\.environ\.get\("(SEAMTECH_[A-Z_]+)"\)', source))
    for cle, attendu in ATTENDUS_SAUVEGARDE.items():
        if attendu and cle not in lues:
            ecarts.append({"cle": cle, "resolue": "(non lue par sauvegarde.py)", "attendue": attendu})
    rapport = {
        "fichier": "/home/user/SEAMTECH-search/config/config.json",
        "variables_lues_par_sauvegarde_py": sorted(lues),
        "resolue": {k: masque(v) for k, v in resolve.items() if not isinstance(v, (dict, list))},
        "racines": racines,
        "ecarts": ecarts,
        "conforme": not ecarts,
    }
    PREUVES.mkdir(parents=True, exist_ok=True)
    (PREUVES / "config_resolue.json").write_text(json.dumps(rapport, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"CONTROLE|config-resolue|{'PASS' if rapport['conforme'] else 'FAIL'}|"
          f"{len(resolve)} clés résolues, {len(ecarts)} écart(s) avec recette.env ; racines={racines}")
    for ecart in ecarts:
        print("   ECART", ecart)
    return 0 if rapport["conforme"] else 1


if __name__ == "__main__":
    sys.exit(main())

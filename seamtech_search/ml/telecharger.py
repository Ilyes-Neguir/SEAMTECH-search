"""Téléchargement EXPLICITE des poids e5-small ONNX — action opérateur.

Aucun téléchargement au runtime : ce module ne sert que lorsqu'un humain tape
la commande. Le bac à sable de développement peut être sans accès à
Hugging Face ; dans ce cas la commande échoue FORTEMENT (pas de repli
silencieux) et la recherche continue sans la source vecteurs.

Usage :
    python -m seamtech_search.ml.telecharger [--dossier D] [--repo R] [--fichier F]

Par défaut : ``intfloat/multilingual-e5-small`` — les chemins ONNX sont
SONDÉS dans l'ordre (le dépôt peut évoluer) ; le premier qui répond est
pris. ``--fichier`` force un chemin exact. Le tokenizer vient toujours du
dépôt Hugging Face officiel du modèle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

from seamtech_search.ml.encodeur import FICHIER_META, FICHIER_MODELE, FICHIER_TOKENIZER

REPO_DEFAUT = "intfloat/multilingual-e5-small"
CHEMINS_ONNX_SONDES = (
    "onnx/model.onnx",
    "model.onnx",
    "onnx/model_quantized.onnx",
)
BASE_HF = "https://huggingface.co/{repo}/resolve/main/{chemin}"
TAILLE_TAMPON = 1 << 20


class TelechargementImpossible(RuntimeError):
    pass


def _televerser(url: str, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as flux:  # noqa: S310 — URL construite ici
            octets = 0
            with destination.open("wb") as sortie:
                while True:
                    bloc = flux.read(TAILLE_TAMPON)
                    if not bloc:
                        break
                    sortie.write(bloc)
                    octets += len(bloc)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise TelechargementImpossible(f"{url} : {type(exc).__name__} : {exc}") from exc
    return octets


def _sonder(url: str) -> bool:
    requete = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(requete, timeout=20) as flux:  # noqa: S310
            return flux.status == 200
    except Exception:
        return False


def _sha256(chemin: Path) -> str:
    hache = hashlib.sha256()
    with chemin.open("rb") as entree:
        while True:
            bloc = entree.read(TAILLE_TAMPON)
            if not bloc:
                break
            hache.update(bloc)
    return hache.hexdigest()


def telecharger(dossier: Path, repo: str = REPO_DEFAUT, fichier_force: str | None = None) -> dict[str, object]:
    dossier = Path(dossier)
    chemin_tokenizer = dossier / FICHIER_TOKENIZER
    chemin_modele = dossier / FICHIER_MODELE

    print(f"Téléchargement du tokenizer ({repo}/{FICHIER_TOKENIZER})…")
    _televerser(BASE_HF.format(repo=repo, chemin=FICHIER_TOKENIZER), chemin_tokenizer)

    if fichier_force:
        # Chemin EXPLICITE choisi par l'opérateur : pas de sondage — un échec
        # de téléchargement sera une erreur nette, pas un « chemin non trouvé ».
        chemin_gagnant = fichier_force
    else:
        chemin_gagnant = None
        for chemin in CHEMINS_ONNX_SONDES:
            url = BASE_HF.format(repo=repo, chemin=chemin)
            print(f"Sondage {url} …")
            if _sonder(url):
                chemin_gagnant = chemin
                break
        if chemin_gagnant is None:
            raise TelechargementImpossible(
                "Aucun chemin ONNX n'a répondu pour "
                f"{repo} (essayés : {', '.join(CHEMINS_ONNX_SONDES)}). Forcez avec --fichier <chemin> "
                "ou changez de dépôt avec --repo. Aucun repli silencieux."
            )
    print(f"Téléchargement du modèle ({chemin_gagnant})…")
    octets = _televerser(BASE_HF.format(repo=repo, chemin=chemin_gagnant), chemin_modele)

    meta = {
        "nom": "e5-small-onnx",
        "repo": repo,
        "fichier_modele": chemin_gagnant,
        "octets": octets,
        "sha256_modele": _sha256(chemin_modele),
        "sha256_tokenizer": _sha256(chemin_tokenizer),
    }
    (dossier / FICHIER_META).write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Modèle installé dans {dossier} ({octets} octets).")
    return meta


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description="Télécharge e5-small ONNX (action opérateur).")
    analyseur.add_argument("--dossier", default=None, help="dossier cible (défaut : <cwd>/modeles/e5-small)")
    analyseur.add_argument("--repo", default=REPO_DEFAUT, help="dépôt Hugging Face")
    analyseur.add_argument("--fichier", default=None, help="chemin exact du fichier ONNX dans le dépôt")
    arguments = analyseur.parse_args(argv)
    dossier = Path(arguments.dossier) if arguments.dossier else Path.cwd() / "modeles" / "e5-small"
    try:
        telecharger(dossier, repo=arguments.repo, fichier_force=arguments.fichier)
    except TelechargementImpossible as exc:
        print(f"ÉCHEC : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

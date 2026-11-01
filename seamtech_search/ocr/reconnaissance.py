"""Sous-processus de reconnaissance OCR — modèles locaux, aucun appel réseau.

Protocole : une ligne JSON par échange, sur stdin/stdout.

* première ligne émise au démarrage : ``{"pret": true, "version": "..."}`` ;
* entrée  : ``{"image": "<chemin>", "timeout": <secondes>, "langue": "fra"}`` ;
* sortie  : ``{"texte": "...", "confiance": 87.5, "succes": true, "motif": ""}``.

Rien n'est journalisé : stderr ne reçoit que le minimum du moteur sous-jacent
et stdout ne transporte que les réponses. Les images reçues ont été produites
par l'appelant dans son ``tempfile.TemporaryDirectory`` (jamais dans le dossier
des documents, RG13) ; ce module n'écrit aucun fichier, il se contente de lire
l'image et de répondre sur stdout.

Les modèles PP-OCRv6 sont ceux livrés DANS la roue ``rapidocr`` ; leurs
chemins sont passés explicitement, donc aucun téléchargement n'est tenté
(RG14). Si un fichier de modèle est absent, le sous-processus s'arrête sans
rien écrire : le parent constate l'indisponibilité du moteur, il ne télécharge
jamais un modèle.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_MAX_MEMOIRE = 3 * 1024 * 1024 * 1024
_LIMITE_ENTREE = 8192


def _limiter_processus(timeout_s: int) -> None:
    """Plafonne la mémoire et le CPU ; ne relève jamais un plafond existant."""
    if os.name != "posix":
        return
    import resource

    for ressource, limite in ((resource.RLIMIT_AS, _MAX_MEMOIRE), (resource.RLIMIT_CPU, timeout_s + 5)):
        try:
            _courant, maximum = resource.getrlimit(ressource)
        except (ValueError, OSError):
            continue
        nouveau = limite if maximum == resource.RLIM_INFINITY else min(limite, maximum)
        try:
            resource.setrlimit(ressource, (nouveau, nouveau))
        except (ValueError, OSError):
            continue


def _modeles_locaux() -> dict[str, str]:
    """Chemins des modèles groupés dans la roue ; aucune URL n'est résolue."""
    import rapidocr

    dossier = Path(rapidocr.__file__).parent / "models"
    fichiers = sorted(p for p in dossier.glob("*.onnx") if p.is_file())
    if not fichiers:
        raise RuntimeError("aucun modèle ONNX groupé")

    def _chercher(motif: str, obligatoire: bool = True) -> str | None:
        for chemin in fichiers:
            if motif in chemin.name.lower():
                return str(chemin)
        if obligatoire:
            raise RuntimeError(f"modèle {motif} absent")
        return None

    return {
        "det": _chercher("det") or "",
        "rec": _chercher("rec") or "",
        "cls": _chercher("cls", obligatoire=False) or "",
    }


class _Moteur:
    """RapidOCR (PP-OCRv6) chargé une seule fois pour tout le lot de pages."""

    def __init__(self) -> None:
        from rapidocr import RapidOCR

        modeles = _modeles_locaux()
        params: dict[str, Any] = {
            # Niveau de journalisation : les journaux du moteur ne doivent pas
            # transporter de contenu de document.
            "Global.log_level": "error",
            "Det.model_path": modeles["det"],
            "Rec.model_path": modeles["rec"],
        }
        if modeles["cls"]:
            params["Cls.model_path"] = modeles["cls"]
        self._ocr = RapidOCR(params=params)
        try:
            from importlib.metadata import version

            self.version = f"rapidocr {version('rapidocr')}"
        except Exception:
            self.version = "rapidocr"

    def reconnaitre(self, chemin: str) -> dict[str, Any]:
        try:
            resultat = self._ocr(chemin)
        except Exception as exc:  # aucun détail de document dans le motif
            return {"texte": "", "confiance": None, "succes": False, "motif": f"échec moteur ({type(exc).__name__})"}
        if resultat is None or not getattr(resultat, "txts", None):
            return {"texte": "", "confiance": None, "succes": True, "motif": ""}
        textes = [str(texte) for texte in resultat.txts]
        scores = [
            float(score)
            for score in (getattr(resultat, "scores", None) or [])
            if isinstance(score, (int, float)) and 0.0 <= float(score) <= 1.0
        ]
        confiance = round(100.0 * sum(scores) / len(scores), 2) if scores else None
        return {"texte": "\n".join(textes), "confiance": confiance, "succes": True, "motif": ""}


def main() -> int:
    timeout = 30
    try:
        requete_initiale = sys.stdin.readline(_LIMITE_ENTREE)
        if requete_initiale:
            try:
                timeout = max(1, min(120, int(json.loads(requete_initiale).get("timeout", timeout))))
            except (ValueError, TypeError):
                timeout = 30
        _limiter_processus(timeout)
        moteur = _Moteur()
    except Exception:
        # Aucun détail : le parent se contente de constater l'absence de moteur.
        return 1
    sys.stdout.write(json.dumps({"pret": True, "version": moteur.version}) + "\n")
    sys.stdout.flush()
    for ligne in sys.stdin:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            requete = json.loads(ligne[:_LIMITE_ENTREE])
            chemin = str(requete["image"])
            if not Path(chemin).is_file():
                raise ValueError("image absente")
        except Exception:
            reponse = {"texte": "", "confiance": None, "succes": False, "motif": "requête invalide"}
        else:
            reponse = moteur.reconnaitre(chemin)
        sys.stdout.write(json.dumps(reponse) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

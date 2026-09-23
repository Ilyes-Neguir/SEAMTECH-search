"""État et verrou d'exécution nocturne — Lot M.

Exigences :
- Verrou d'exécution : un seul run à la fois (fichier verrou)
- État reprenable par empreinte SHA-256 (jamais par mtime seul)
- Budget de temps, arrêt propre
- Aucune écriture dans le dossier source (RG13)
- Aucun appel réseau (RG14)

Le répertoire de travail est déclaré par la variable d'environnement
SEAMTECH_OCR_TRAVAIL_DIR (documentée dans docs/OCR_ETAGES.md).
Par défaut : data/ocr_travail (hors archive, créé si besoin).

Structure du répertoire de travail :
- ocr_nuit.lock : verrou d'exécution (PID + timestamp)
- ocr_etat.json : état reprenable (empreintes traitées)
- rapports/ : comptes rendus JSON + texte de chaque run
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_travail_dir(travail_dir: str | Path | None = None) -> Path:
    """Retourne le répertoire de travail OCR.

    Variable d'environnement : SEAMTECH_OCR_TRAVAIL_DIR
    Si non définie, défaut : data/ocr_travail à la racine du projet
    (ou cwd/data/ocr_travail si racine non trouvée).

    Le répertoire est créé s'il n'existe pas.

    Cette fonction est la SEULE à décider où écrire : jamais dans le
    dossier source (RG13).
    """
    if travail_dir is not None:
        chemin = Path(travail_dir)
    else:
        env = os.environ.get("SEAMTECH_OCR_TRAVAIL_DIR")
        if env:
            chemin = Path(env)
        else:
            # Cherche la racine du projet (présence de pyproject.toml)
            cwd = Path.cwd()
            # Remonte jusqu'à trouver pyproject.toml ou s'arrête à cwd
            racine = cwd
            for parent in [cwd] + list(cwd.parents):
                if (parent / "pyproject.toml").exists():
                    racine = parent
                    break
            chemin = racine / "data" / "ocr_travail"

    chemin = chemin.resolve()
    chemin.mkdir(parents=True, exist_ok=True)
    # Sous-dossier rapports
    (chemin / "rapports").mkdir(parents=True, exist_ok=True)
    return chemin


def empreinte_sha256(chemin: Path) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier (lecture seule, RG13)."""
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloc)
    return h.hexdigest()


class VerrouOCR:
    """Verrou d'exécution — un seul run à la fois.

    Fichier verrou : <travail_dir>/ocr_nuit.lock
    Contenu JSON : {"pid": int, "timestamp": str, "debut": float}

    Si le verrou existe et que le PID est vivant, acquire() retourne False
    (second lancement doit sortir proprement avec code distinct).

    Si le verrou existe mais que le PID est mort (stale), il est supprimé
    et le verrou est acquis (reprise après crash).
    """

    def __init__(self, travail_dir: str | Path | None = None):
        self.travail_dir = get_travail_dir(travail_dir)
        self.lock_path = self.travail_dir / "ocr_nuit.lock"
        self._acquis = False

    def _pid_vivant(self, pid: int) -> bool:
        """Vérifie si un PID est vivant (Unix : os.kill(pid, 0))."""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            # Sur Windows ou autre, considère vivant si fichier lock récent (<5min)
            # Pour simplifier, on tente de lire le timestamp
            try:
                if self.lock_path.exists():
                    # Si lock a moins de 10 minutes, on le considère vivant
                    age = time.time() - self.lock_path.stat().st_mtime
                    return age < 600
            except OSError:
                pass
            return False

    def acquire(self) -> bool:
        """Tente d'acquérir le verrou. Retourne True si acquis, False si déjà pris."""
        if self.lock_path.exists():
            try:
                contenu = json.loads(self.lock_path.read_text(encoding="utf-8"))
                pid = int(contenu.get("pid", 0))
                if pid and self._pid_vivant(pid):
                    # Verrou actif
                    return False
                else:
                    # Stale lock : on le supprime
                    try:
                        self.lock_path.unlink()
                    except OSError:
                        pass
            except Exception:
                # Fichier corrompu ou illisible : on tente de le supprimer si ancien
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    if age > 600:
                        self.lock_path.unlink()
                    else:
                        # Récent mais illisible : on le considère actif par prudence
                        return False
                except OSError:
                    pass

        # Acquiert
        try:
            data = {
                "pid": os.getpid(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "debut": time.time(),
            }
            self.lock_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._acquis = True
            return True
        except OSError:
            return False

    def release(self) -> None:
        """Libère le verrou si on le possède."""
        if not self._acquis:
            # On ne supprime que si on est propriétaire (vérifie PID)
            try:
                if self.lock_path.exists():
                    contenu = json.loads(self.lock_path.read_text(encoding="utf-8"))
                    if int(contenu.get("pid", 0)) == os.getpid():
                        self.lock_path.unlink()
            except Exception:
                try:
                    self.lock_path.unlink()
                except OSError:
                    pass
            return

        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except OSError:
            pass
        self._acquis = False

    def __enter__(self) -> "VerrouOCR":
        if not self.acquire():
            raise RuntimeError(
                f"Un run OCR est déjà en cours (verrou {self.lock_path}). "
                "Second lancement refusé proprement — pas de double travail, pas de corruption."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class EtatOCR:
    """État reprenable par empreinte SHA-256.

    Fichier : <travail_dir>/ocr_etat.json
    Structure :
    {
        "fichiers_traites": {
            "<sha256>": {
                "chemin": "...",
                "nb_pages": int,
                "nb_pages_ocerisees": int,
                "timestamp": iso,
                "duree_s": float
            }
        },
        "derniere_execution": {...}
    }

    L'état est sauvegardé après chaque fichier traité (reprise après Ctrl-C,
    SIGTERM ou budget épuisé).
    """

    def __init__(self, travail_dir: str | Path | None = None):
        self.travail_dir = get_travail_dir(travail_dir)
        self.etat_path = self.travail_dir / "ocr_etat.json"
        self._etat: dict[str, Any] = {
            "fichiers_traites": {},
            "derniere_execution": None,
        }
        self.charger()

    def charger(self) -> None:
        """Charge l'état depuis le fichier, ou initialise vide."""
        if self.etat_path.exists():
            try:
                self._etat = json.loads(self.etat_path.read_text(encoding="utf-8"))
                if "fichiers_traites" not in self._etat:
                    self._etat["fichiers_traites"] = {}
            except Exception:
                # Corrompu : on repart de zéro mais on garde une sauvegarde
                try:
                    backup = self.travail_dir / f"ocr_etat_corrompu_{int(time.time())}.json"
                    self.etat_path.rename(backup)
                except OSError:
                    pass
                self._etat = {"fichiers_traites": {}, "derniere_execution": None}

    def sauvegarder(self) -> None:
        """Sauvegarde l'état (écriture atomique via temp file)."""
        try:
            tmp = self.etat_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._etat, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.etat_path)
        except OSError:
            # Dernier recours : écriture directe
            try:
                self.etat_path.write_text(json.dumps(self._etat, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass

    def est_deja_traite(self, empreinte_sha256: str) -> bool:
        """Vérifie si un fichier (par empreinte) a déjà été traité."""
        return empreinte_sha256 in self._etat.get("fichiers_traites", {})

    def marquer_traite(
        self,
        empreinte_sha256: str,
        chemin: str | Path,
        nb_pages: int,
        nb_pages_ocerisees: int,
        duree_s: float,
    ) -> None:
        """Marque un fichier comme traité (par empreinte, jamais mtime)."""
        self._etat.setdefault("fichiers_traites", {})[empreinte_sha256] = {
            "chemin": str(chemin),
            "nb_pages": nb_pages,
            "nb_pages_ocerisees": nb_pages_ocerisees,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duree_s": duree_s,
        }
        self.sauvegarder()

    def fichiers_traites(self) -> dict[str, Any]:
        return dict(self._etat.get("fichiers_traites", {}))

    def reinitialiser(self) -> None:
        """Réinitialise l'état (pour tests)."""
        self._etat = {"fichiers_traites": {}, "derniere_execution": None}
        self.sauvegarder()

    def set_derniere_execution(self, info: dict[str, Any]) -> None:
        self._etat["derniere_execution"] = info
        self.sauvegarder()


def verifier_budget(debut_s: float, budget_minutes: float | None) -> tuple[bool, float]:
    """Vérifie si le budget de temps est dépassé.

    Retourne (depasse, ecoule_s).
    """
    if budget_minutes is None:
        return False, time.time() - debut_s
    ecoule = time.time() - debut_s
    depasse = ecoule >= (budget_minutes * 60)
    return depasse, ecoule

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preuve qu'un document PERDU du stockage objet revient depuis la sauvegarde.

Pourquoi ce script existe — la limite exacte qu'il ferme.

La recette locale prouve déjà une restauration : elle supprime une ligne en
base, lance ``pg_restore``, et constate que l'application sert à nouveau la
fiche. **Mais elle ne supprime jamais l'objet dans le stockage.** Les pièces
jointes n'ont donc jamais disparu : ce que la recette prouve, c'est que le
LIEN base → objet survit à une restauration de la base, pas que les octets
reviennent si le stockage les perd.

C'est pourtant le scénario qui compte : un bucket vidé, un objet écrasé, un
MinIO reconstruit à neuf. La sauvegarde complète COPIE les objets
(``SECTIONS_DOCUMENTS``) et ``verifier`` prouve qu'ils sont intacts dans le
bundle — mais rien ne prouvait qu'on peut les remettre et les resservir.

La chaîne prouvée ici, dans le déploiement réel (Docker, conteneurs Linux) :

    1. l'objet est servi par l'application .................. SHA-A
    2. l'objet est SUPPRIMÉ du stockage
    3. l'application ne le sert plus ............... (preuve de disparition)
    4. la sauvegarde complète est produite
    5. le bundle contient l'objet, intact .......... (verifier)
    6. la restauration le rend, octets identiques .. SHA-A (cible distincte)
    7. l'objet est RÉINJECTÉ dans le stockage
    8. l'application le sert à nouveau ............. SHA-A

Le verdict n'est « PROUVÉ » que si les huit étapes passent. Un échec est
NOMMÉ (étape, commande, code de sortie, extrait de sortie) : un constat
obscur vaudrait un mensonge.

Conception — pourquoi tout passe par un ``executeur`` injectable.
Aucune machine de développement ne dispose de Docker ; l'orchestration ne
peut donc pas y être exercée. Toutes les commandes passent par une fonction
injection, ce qui rend la logique intégralement testable (voir
``tests/test_preuve_objet_perdu.py``) : ce qui reste non vérifiable hors CI,
c'est la syntaxe des commandes Docker elles-mêmes — et la CI, qui a Docker,
les exécute pour de vrai.

Usage :

    python scripts/preuve_objet_perdu.py \\
        --fiche <code> --cle-objet <cle> --jeton <token>

Code de sortie : 0 = PROUVÉ, 1 = NON PROUVÉ (étape nommée), 2 = prérequis
manquant (liste nommée).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

Executeur = Callable[[list[str]], tuple[int, str, str]]


def _executeur_reel(commande: list[str]) -> tuple[int, str, str]:
    """L'exécuteur par défaut : la commande est réellement lancée."""
    try:
        r = subprocess.run(commande, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        return 127, "", f"commande introuvable : {commande[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"délai dépassé : {' '.join(commande)}"
    return r.returncode, r.stdout or "", r.stderr or ""


class Etape:
    """Une étape nommée. Son échec dit toujours POURQUOI."""

    def __init__(self, nom: str) -> None:
        self.nom = nom
        self.reussie = False
        self.detail = ""

    def reussir(self, detail: str) -> "Etape":
        self.reussie = True
        self.detail = detail
        return self

    def echouer(self, detail: str) -> "Etape":
        self.reussie = False
        self.detail = detail
        return self

    def vers_dict(self) -> dict[str, Any]:
        return {"etape": self.nom, "reussie": self.reussie, "detail": self.detail}


def _sha256(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()


class PreuveObjetPerdu:
    """La chaîne de preuve, étape par étape, sans jamais masquer un échec."""

    def __init__(
        self,
        *,
        executeur: Executeur,
        fiche: str,
        cle_objet: str,
        jeton: str,
        compose: tuple[str, ...] = ("docker", "compose"),
        service_web: str = "web",
        service_minio: str = "minio",
        bucket: str = "seamtech-documents",
        base_api: str = "http://127.0.0.1:8000",
        dossier_bundle: str = "/app/data/backups/preuve-objet",
        dossier_cible: str = "/app/data/restauration-preuve-objet",
        base_cible: str = "",
        docker_disponible: bool | None = None,
    ) -> None:
        self.executeur = executeur
        self.fiche = fiche
        self.cle = cle_objet
        self.jeton = jeton
        self.compose = list(compose)
        self.web = service_web
        self.minio = service_minio
        self.bucket = bucket
        self.api = base_api
        self.bundle = dossier_bundle
        self.cible = dossier_cible
        self.base_cible = base_cible
        # Injectable : aucune machine de développement n'a Docker, et un
        # prérequis non injectable rendrait la logique intestable — elle
        # court-circuiterait partout ailleurs que sur un poste équipé.
        self.docker_disponible = (
            shutil.which("docker") is not None if docker_disponible is None else docker_disponible
        )
        self.etapes: list[Etape] = []
        self._piece_id: int | None = None

    # -- primitives --------------------------------------------------------

    def _compose_exec(self, service: str, *args: str) -> tuple[int, str, str]:
        return self.executeur([*self.compose, "exec", "-T", service, *args])

    def _executer(self, commande: list[str]) -> tuple[int, str, str]:
        return self.executeur(commande)

    def _etape(self, nom: str) -> Etape:
        etape = Etape(nom)
        self.etapes.append(etape)
        return etape

    # -- prérequis ---------------------------------------------------------

    def prerequis(self) -> list[str]:
        """Ce qui manque pour courir. Une liste vide = on peut démarrer."""
        manques: list[str] = []
        if not self.docker_disponible:
            manques.append("docker — introuvable dans le PATH")
        if not self.fiche:
            manques.append("--fiche <code> — le code de la fiche à éprouver")
        if not self.cle:
            manques.append("--cle-objet <cle> — la clé de l'objet dans le stockage")
        if not self.jeton:
            manques.append("--jeton <token> — le jeton d'API (X-SEAMTECH-TOKEN)")
        if not self.base_cible:
            manques.append(
                "--base-cible <url> — une base DISTINCTE pour la restauration "
                "(jamais la base de production : la restauration l'écraserait)"
            )
        return manques

    # -- la chaîne ---------------------------------------------------------

    def courir(self) -> dict[str, Any]:
        manques = self.prerequis()
        if manques:
            return {
                "verdict": "NON PROUVÉ",
                "raison": "prérequis manquants",
                "manques": manques,
                "etapes": [],
            }

        services = self._services_actifs()
        if not services:
            self._etape("services").echouer(
                "aucun service en cours : lancer « docker compose up -d » avant la preuve"
            )
            return self._rendre()
        for service in (self.web, self.minio):
            if service not in services:
                self._etape("services").echouer(
                    f"service « {service} » absent des services en cours : {services}"
                )
                return self._rendre()
        self._etape("services").reussir(f"{self.web} et {self.minio} actifs")

        # 1. l'objet est servi, on note son empreinte
        avant = self._servir()
        if avant is None:
            self._etape("objet servi avant").echouer(
                f"l'application ne sert pas la pièce {self.cle} AVANT la suppression : "
                "la preuve n'aurait rien prouvé"
            )
            return self._rendre()
        sha_avant = _sha256(avant)
        self._etape("objet servi avant").reussir(f"{len(avant)} octets, sha256={sha_avant[:16]}…")

        # 2. et 3. suppression, puis preuve de disparition
        code, _, err = self._compose_exec(self.minio, "mc", "rm", f"local/{self.bucket}/{self.cle}")
        if code != 0:
            self._etape("objet supprimé").echouer(f"mc rm a échoué (code={code}) : {err.strip()[:200]}")
            return self._rendre()
        self._etape("objet supprimé").reussir(f"local/{self.bucket}/{self.cle}")

        if self._servir() is not None:
            self._etape("objet disparu").echouer(
                "l'application sert encore la pièce APRÈS la suppression : "
                "soit la suppression n'a rien fait, soit l'objet est servi depuis "
                "un cache — la preuve de perte est alors fausse"
            )
            return self._rendre()
        self._etape("objet disparu").reussir("la pièce n'est plus servie")

        # 4. et 5. sauvegarde complète, et son contenu vérifié
        bundle = self._sauvegarder()
        if bundle is None:
            return self._rendre()
        if not self._verifier(bundle):
            return self._rendre()

        # 6. restauration vers une cible distincte, octets identiques
        chemin = self._restaurer(bundle)
        if chemin is None:
            return self._rendre()
        code, out, err = self._compose_exec(self.web, "sha256sum", chemin)
        if code != 0:
            self._etape("octets restaurés").echouer(f"sha256sum impossible (code={code}) : {err.strip()[:200]}")
            return self._rendre()
        sha_cible = (out.split() or [""])[0]
        if sha_cible != sha_avant:
            self._etape("octets restaurés").echouer(
                f"les octets restaurés diffèrent : attendu {sha_avant[:16]}…, obtenu {sha_cible[:16]}… "
                "— une sauvegarde qui ne rend pas les mêmes octets n'est pas une sauvegarde"
            )
            return self._rendre()
        self._etape("octets restaurés").reussir(f"sha256 identique : {sha_cible[:16]}…")

        # 7. réinjection dans le stockage
        if not self._reinjecter(chemin):
            return self._rendre()

        # 8. l'application ressert les mêmes octets
        apres = self._servir()
        if apres is None:
            self._etape("objet resservi").echouer(
                "la pièce n'est pas resservie après réinjection : la restauration "
                "n'a pas remis l'objet utilisable"
            )
            return self._rendre()
        sha_apres = _sha256(apres)
        if sha_apres != sha_avant:
            self._etape("objet resservi").echouer(
                f"octets resservis différents : attendu {sha_avant[:16]}…, obtenu {sha_apres[:16]}…"
            )
            return self._rendre()
        self._etape("objet resservi").reussir(f"{len(apres)} octets, sha256={sha_apres[:16]}…")

        return self._rendre()

    # -- détails d'étapes --------------------------------------------------

    def _servir(self) -> bytes | None:
        """Les octets tels que l'APPLICATION les sert, ou None si absents.

        Deux appels, et pas un de moins. L'API n'expose **jamais** la clé de
        l'objet (``object_key`` est expurgé des réponses : c'est une règle de
        confidentialité, pas un oubli). On passe donc par l'identifiant de la
        pièce, puis par la route de téléchargement — exactement le chemin que
        ``scripts/recette_verif.py`` éprouve déjà dans son contrôle
        « pdf-presigne ». Réinventer une route aurait produit une preuve qui
        ne prouve rien.
        """
        if self._piece_id is None:
            if not self._trouver_piece():
                return None
        return self._telecharger_piece()

    def _trouver_piece(self) -> bool:
        code, sortie, _ = self._executer(
            [
                "curl", "-s", "--max-time", "60",
                "-H", f"X-SEAMTECH-TOKEN: {self.jeton}",
                f"{self.api}/fiches/{quote(self.fiche, safe='')}/pieces",
            ]
        )
        if code != 0:
            return False
        try:
            corps = json.loads(sortie)
        except json.JSONDecodeError:
            return False
        for piece in corps.get("pieces") or []:
            if isinstance(piece, dict) and piece.get("kind") == "pdf" and isinstance(piece.get("id"), int):
                self._piece_id = int(piece["id"])
                return True
        return False

    def _telecharger_piece(self) -> bytes | None:
        with tempfile.NamedTemporaryFile(delete=False) as temporaire:
            chemin = temporaire.name
        try:
            code, sortie, _ = self._executer(
                [
                    "curl", "-s", "--max-time", "120", "-o", chemin, "-w", "%{http_code}",
                    "-H", f"X-SEAMTECH-TOKEN: {self.jeton}",
                    f"{self.api}/pieces/{self._piece_id}/telecharger",
                ]
            )
            if code != 0 or sortie.strip() != "200":
                return None
            return Path(chemin).read_bytes()
        finally:
            Path(chemin).unlink(missing_ok=True)

    def _services_actifs(self) -> list[str]:
        code, out, _ = self._executer([*self.compose, "ps", "--status", "running", "--services"])
        if code != 0:
            return []
        return [ligne.strip() for ligne in out.splitlines() if ligne.strip()]

    def _sauvegarder(self) -> str | None:
        code, out, err = self._compose_exec(
            self.web, "python", "-m", "seamtech_search.sauvegarde_complete",
            "sauvegarder", "--dossier", self.bundle, "--sans-s3",
        )
        if code != 0:
            self._etape("sauvegarde complète").echouer(f"code={code} : {(err or out).strip()[:300]}")
            return None
        manifeste = f"{self.bundle}/manifeste.json"
        self._etape("sauvegarde complète").reussir(f"bundle produit : {manifeste}")
        return manifeste

    def _verifier(self, manifeste: str) -> bool:
        code, out, err = self._compose_exec(
            self.web, "python", "-m", "seamtech_search.sauvegarde_complete",
            "verifier", "--manifeste", manifeste,
        )
        if code != 0:
            self._etape("bundle vérifié").echouer(f"code={code} : {(err or out).strip()[:300]}")
            return False
        try:
            rapport = json.loads(out)
        except json.JSONDecodeError:
            self._etape("bundle vérifié").echouer(f"sortie illisible : {out.strip()[:200]}")
            return False
        if not rapport.get("nb_documents_verifies"):
            self._etape("bundle vérifié").echouer(
                "aucun document vérifié dans le bundle : une preuve vide n'est pas une preuve"
            )
            return False
        if rapport.get("nb_documents_alteres") or rapport.get("nb_documents_absents"):
            self._etape("bundle vérifié").echouer(
                f"{rapport.get('nb_documents_alteres')} altéré(s), "
                f"{rapport.get('nb_documents_absents')} absent(s)"
            )
            return False
        self._etape("bundle vérifié").reussir(
            f"{rapport.get('nb_documents_verifies')} document(s) intacts"
        )
        return True

    def _restaurer(self, manifeste: str) -> str | None:
        code, out, err = self._compose_exec(
            self.web, "python", "-m", "seamtech_search.sauvegarde_complete",
            "restaurer", "--manifeste", manifeste,
            "--base-cible", self.base_cible, "--fichiers-cible", self.cible,
        )
        if code != 0:
            self._etape("restauration").echouer(f"code={code} : {(err or out).strip()[:300]}")
            return None
        # Le chemin du document dans la cible : ce que la restauration a écrit.
        code, out, err = self._compose_exec(
            self.web, "find", self.cible, "-type", "f", "-name", self.cle.rsplit("/", 1)[-1],
        )
        if code != 0 or not out.strip():
            self._etape("restauration").echouer(
                f"document introuvable dans {self.cible} après restauration : {err.strip()[:200]}"
            )
            return None
        chemin = out.strip().splitlines()[0]
        self._etape("restauration").reussir(f"document rendu dans la cible distincte : {chemin}")
        return chemin

    def _reinjecter(self, chemin_source: str) -> bool:
        nom = self.cle.rsplit("/", 1)[-1]
        temporaire = f"/tmp/preuve-reinjection-{nom}"
        code, _, err = self._executer([*self.compose, "cp", f"{self.web}:{chemin_source}", temporaire])
        if code != 0:
            self._etape("réinjection").echouer(f"copie hors du conteneur impossible : {err.strip()[:200]}")
            return False
        code, _, err = self._executer(
            [*self.compose, "cp", temporaire, f"{self.minio}:{temporaire}"]
        )
        if code != 0:
            self._etape("réinjection").echouer(f"copie vers le stockage impossible : {err.strip()[:200]}")
            return False
        cible = f"local/{self.bucket}/{self.cle}"
        code, _, err = self._compose_exec(self.minio, "mc", "cp", temporaire, cible)
        if code != 0:
            self._etape("réinjection").echouer(f"mc cp a échoué (code={code}) : {err.strip()[:200]}")
            return False
        self._etape("réinjection").reussir(f"objet réinjecté : {cible}")
        return True

    def _rendre(self) -> dict[str, Any]:
        echecs = [e for e in self.etapes if not e.reussie]
        if echecs:
            return {
                "verdict": "NON PROUVÉ",
                "raison": f"« {echecs[0].nom} » : {echecs[0].detail}",
                "manques": [],
                "etapes": [e.vers_dict() for e in self.etapes],
            }
        return {
            "verdict": "PROUVÉ",
            "raison": "objet supprimé du stockage puis resservi, octets identiques",
            "manques": [],
            "etapes": [e.vers_dict() for e in self.etapes],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/preuve_objet_perdu.py",
        description="Prouve qu'un objet perdu du stockage revient depuis la sauvegarde complète.",
    )
    parser.add_argument("--fiche", default="", help="code de la fiche à éprouver")
    parser.add_argument("--cle-objet", default="", help="clé de l'objet dans le stockage")
    parser.add_argument("--jeton", default="", help="jeton d'API (X-SEAMTECH-TOKEN)")
    parser.add_argument("--base-cible", default="", help="base DISTINCTE pour la restauration")
    parser.add_argument("--service-web", default="web")
    parser.add_argument("--service-minio", default="minio")
    parser.add_argument("--bucket", default="seamtech-documents")
    parser.add_argument("--base-api", default="http://127.0.0.1:8000")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    preuve = PreuveObjetPerdu(
        executeur=_executeur_reel,
        fiche=args.fiche,
        cle_objet=args.cle_objet,
        jeton=args.jeton,
        service_web=args.service_web,
        service_minio=args.service_minio,
        bucket=args.bucket,
        base_api=args.base_api,
        base_cible=args.base_cible,
    )
    rapport = preuve.courir()

    print("=" * 72)
    print(f"PREUVE — objet perdu puis restauré — {rapport['verdict']}")
    print("=" * 72)
    for etape in rapport["etapes"]:
        print(f"  [{'OK ' if etape['reussie'] else 'ÉCHEC'}] {etape['etape']} : {etape['detail']}")
    if rapport["manques"]:
        print("\nPrérequis manquants — la preuve n'a pas démarré :")
        for manque in rapport["manques"]:
            print(f"  · {manque}")
    else:
        print(f"\nRaison : {rapport['raison']}")
    if args.verbose:
        print("\n" + json.dumps(rapport, ensure_ascii=False, indent=1))

    if rapport["manques"]:
        return 2
    return 0 if rapport["verdict"] == "PROUVÉ" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Vérification HORS LIGNE des parcours essentiels — par EXÉCUTION, pas par lecture.

Exigence de la revue indépendante du 2026-10-07 (constat n° 3) : « vérifier le
fonctionnement hors ligne AVANT le déploiement en atelier », et ne surtout pas
conclure « prêt hors ligne » en LISANT la configuration.

Ce que ce script fait, concrètement :

* ``--inventaire`` : constate ce qui est provisionné AVANT le blocage (base,
  Redis, stockage objet, OCR, rendu PDF, modèles, images conteneurs) et CLASSE
  chaque capacité en « requise » ou « optionnelle », en disant la conséquence
  exacte d'une absence. Une capacité optionnelle absente est DITE — jamais
  contournée en silence.
* ``--executer`` : installe un GARDE RÉSEAU (toute connexion Python vers une
  adresse NON privée échoue et est comptée), puis exécute les parcours
  essentiels contre la pile locale RÉELLE : démarrage de l'application sur
  PostgreSQL, connexion nominative, import d'un dossier réel (comptabilité des
  fichiers), extraction, correction + validation avec révision, recherche,
  aperçu PDF, téléchargement de l'original et du rapport généré. À la fin :
  zéro connexion externe attendue, et le détail de ce qui a été exercé.
* ``--autoriser-externe`` : même exécution sans blocage — diagnostic de ce qui
  sortirait du serveur.

STATUT RG14_EXCEPTION — assumé et documenté : ce fichier importe ``socket`` et
``urllib`` PARCE QUE sa fonction EST de surveiller et de bloquer les connexions
sortantes (même famille d'outil que ``scripts/mesure_assistant.py``). Il n'est
jamais importé par le service : c'est un outil de VÉRIFICATION, exécuté à la
demande, dont la cible normale est le loopback. L'exception est déclarée dans
``tests/test_garde_fous_preparation.py`` (EXCEPTIONS_RG14) et rappelée dans
``docs/DEPLOYMENT.md`` (§ vérification hors ligne).

LIMITES ÉNONCÉES (à ne pas dépasser) :

* Le garde intercepte les connexions **Python** (urllib, boto3, redis-py,
  httpx…). Les bibliothèques C (libpq) appellent ``connect(2)`` directement :
  elles ne passent pas par le garde. C'est pourquoi ``--inventaire`` vérifie en
  plus que les points de terminaison configurés (base, Redis, S3) sont
  loopback/privés, et que rien ne pointe vers un nom public.
* Ce script ne remplace PAS l'acceptation en atelier (serveur réel, postes
  réels, câble réseau débranché, modèles et images préinstallés) : il exécute
  la partie automatisable et l'annonce. Il n'écrit rien dans le dépôt.

Usage :
    python scripts/verifier_hors_ligne.py --inventaire
    python scripts/verifier_hors_ligne.py --executer
    python scripts/verifier_hors_ligne.py --executer --autoriser-externe
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

#: Noms toujours locaux (loopback).
HOTES_LOCAUX = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


@dataclass
class Resultat:
    nom: str
    statut: str  # "ok" | "echec" | "indisponible"
    detail: str = ""

    def ligne(self) -> str:
        symbole = {"ok": "OK     ", "echec": "ÉCHEC  ", "indisponible": "N/D    "}[self.statut]
        return f"[{symbole}] {self.nom} — {self.detail}"


@dataclass
class Rapport:
    resultats: list[Resultat] = field(default_factory=list)

    def ajouter(self, nom: str, statut: str, detail: str = "") -> Resultat:
        resultat = Resultat(nom, statut, detail)
        self.resultats.append(resultat)
        print(resultat.ligne(), flush=True)
        return resultat

    @property
    def echecs(self) -> list[Resultat]:
        return [r for r in self.resultats if r.statut == "echec"]

    @property
    def indisponibles(self) -> list[Resultat]:
        return [r for r in self.resultats if r.statut == "indisponible"]


# ---------------------------------------------------------------------------
# Garde réseau
# ---------------------------------------------------------------------------


class EgressExterne(OSError):
    """Levée à la place d'une connexion Python vers une adresse NON privée."""


class GardeReseau:
    """Remplace ``socket.socket.connect``/``connect_ex`` par une version surveillée.

    On n'inspecte pas la configuration : on intercepte les connexions RÉELLES.
    Une tentative vers l'extérieur est bloquée (mode atelier) ou journalisée
    (mode diagnostic) — dans les deux cas elle est COMPTÉE et NOMMÉE
    (hôte:port), ce qui désigne la dépendance cachée au lieu de la supposer.
    """

    def __init__(self, *, bloquer: bool = True, hotes_autorises: set[str] | None = None) -> None:
        self.bloquer = bloquer
        self.hotes_autorises = set(hotes_autorises or set()) | HOTES_LOCAUX
        self.tentatives: list[str] = []
        self._connect_origine = socket.socket.connect
        self._connect_ex_origine = socket.socket.connect_ex

    def _est_local(self, adresse: Any) -> bool:
        if isinstance(adresse, str):  # socket AF_UNIX : toujours local
            return True
        if not isinstance(adresse, tuple) or not adresse:
            return True
        hote = str(adresse[0])
        if hote in self.hotes_autorises:
            return True
        try:
            ip = ipaddress.ip_address(hote)
        except ValueError:
            # Un NOM sans autorisation explicite : refusé (un nom peut résoudre
            # vers n'importe où — c'est justement la dépendance qu'on traque).
            return False
        return ip.is_loopback or ip.is_private or ip.is_link_local

    def _verifier(self, adresse: Any) -> None:
        if self._est_local(adresse):
            return
        cible = adresse if isinstance(adresse, str) else f"{adresse[0]}:{adresse[1]}"
        self.tentatives.append(str(cible))
        if self.bloquer:
            raise EgressExterne(
                f"réseau externe BLOQUÉ par la vérification hors ligne : {cible} "
                "(une dépendance externe a été touchée pendant un parcours essentiel)"
            )

    def installer(self) -> None:
        garde = self
        origine = self._connect_origine

        def connect(soquette: socket.socket, adresse: Any) -> Any:
            garde._verifier(adresse)
            return origine(soquette, adresse)

        def connect_ex(soquette: socket.socket, adresse: Any) -> int:
            garde._verifier(adresse)
            return garde._connect_ex_origine(soquette, adresse)

        socket.socket.connect = connect  # type: ignore[method-assign]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]

    def retirer(self) -> None:
        socket.socket.connect = self._connect_origine  # type: ignore[method-assign]
        socket.socket.connect_ex = self._connect_ex_origine  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Configuration depuis l'environnement (mêmes variables que la CI)
# ---------------------------------------------------------------------------


def config_depuis_environnement() -> Any:
    from seamtech_search.config import AppConfig

    base = os.environ.get("SEAMTECH_DATABASE_URL") or os.environ.get("SEAMTECH_TEST_DATABASE_URL")
    redis_url = os.environ.get("SEAMTECH_REDIS_URL") or os.environ.get("SEAMTECH_TEST_REDIS_URL")
    racines = os.environ.get("SEAMTECH_ROOT_PATHS") or str(RACINE / "sample_data")
    return AppConfig(
        root_paths=[Path(p) for p in racines.split(os.pathsep) if p],
        database_path=Path(tempfile.mkdtemp(prefix="hors-ligne-")) / "search.db",
        database_url=base,
        s3_endpoint_url=os.environ.get("SEAMTECH_S3_ENDPOINT_URL") or None,
        s3_bucket=os.environ.get("SEAMTECH_S3_BUCKET") or "seamtech-documents",
        s3_access_key=os.environ.get("SEAMTECH_S3_ACCESS_KEY") or None,
        s3_secret_key=os.environ.get("SEAMTECH_S3_SECRET_KEY") or None,
        redis_url=redis_url,
        auth_token=os.environ.get("SEAMTECH_AUTH_TOKEN") or "jeton-hors-ligne",
        min_free_bytes=0,
    )


def _adresse_locale(url: str | None) -> tuple[bool, str]:
    """Vrai si l'URL pointe un hôte loopback/privé (aucune résolution DNS)."""
    if not url:
        return True, "non configuré"
    morceaux = urllib.parse.urlsplit(url)
    hote = morceaux.hostname or ""
    if not hote:
        return True, "sans hôte (socket Unix ?)"
    if hote in HOTES_LOCAUX:
        return True, hote
    try:
        ip = ipaddress.ip_address(hote)
    except ValueError:
        return False, f"{hote} (nom : doit être autorisé explicitement, ex. alias Compose)"
    return (ip.is_loopback or ip.is_private), f"{hote} ({'privé' if ip.is_private else 'PUBLIC'})"


# ---------------------------------------------------------------------------
# Inventaire
# ---------------------------------------------------------------------------


def _commande_presente(nom: str) -> bool:
    return shutil.which(nom) is not None


def _langues_tesseract() -> set[str]:
    if not _commande_presente("tesseract"):
        return set()
    try:
        sortie = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=30, check=False
        )
    except Exception:
        return set()
    return {ligne.strip() for ligne in sortie.stdout.splitlines()[1:] if ligne.strip()}


def inventaire(rapport: Rapport, config: Any) -> dict[str, Any]:
    capacites: dict[str, Any] = {}

    # --- Points de terminaison : d'abord vérifier qu'ils sont LOCAUX ---------
    for nom, url, requis in (
        ("base de données (adresse)", config.database_url, True),
        ("redis (adresse)", config.redis_url, True),
        ("stockage objet (adresse)", config.s3_endpoint_url, bool(config.s3_endpoint_url)),
    ):
        local, detail = _adresse_locale(url)
        if url is None and not requis:
            rapport.ajouter(nom, "indisponible", "non configuré — capacité optionnelle absente")
        elif local:
            rapport.ajouter(nom, "ok", detail)
        else:
            rapport.ajouter(
                nom,
                "echec",
                f"{detail} — un serveur hors ligne ne doit dépendre d'aucun nom/point de terminaison public",
            )
        capacites[nom] = local if url else False

    # --- Base de données : joignable ? --------------------------------------
    try:
        import psycopg2

        with psycopg2.connect(config.database_url, connect_timeout=5) as connexion:
            with connexion.cursor() as curseur:
                curseur.execute("SELECT 1")
        rapport.ajouter("postgresql (joignable)", "ok", "registre de vérité répondant")
        capacites["postgresql"] = True
    except Exception as exc:
        rapport.ajouter(
            "postgresql (joignable)",
            "echec",
            f"{type(exc).__name__} — REQUIS : sans base, ni fiches ni validation humaine (§17.1)",
        )
        capacites["postgresql"] = False

    # --- Redis : joignable ? (file durable) ---------------------------------
    try:
        import redis

        client = redis.from_url(config.redis_url, socket_connect_timeout=5)
        client.ping()
        rapport.ajouter("redis (joignable)", "ok", "file durable opérationnelle")
        capacites["redis"] = True
    except Exception as exc:
        rapport.ajouter(
            "redis (joignable)",
            "echec",
            f"{type(exc).__name__} — REQUIS en production : sans Redis, l'API refuse les imports (503) "
            "au lieu de promettre une durabilité qu'elle n'a pas",
        )
        capacites["redis"] = False

    # --- Stockage objet : vivant ? ------------------------------------------
    if config.s3_endpoint_url:
        try:
            import urllib.request

            with urllib.request.urlopen(
                f"{config.s3_endpoint_url.rstrip('/')}/minio/health/live", timeout=5
            ) as reponse:
                vivant = reponse.status == 200
            if not vivant:
                raise RuntimeError(f"HTTP {reponse.status}")
            rapport.ajouter("stockage objet (vivant)", "ok", f"{config.s3_endpoint_url} répond")
            capacites["stockage"] = True
        except Exception as exc:
            rapport.ajouter(
                "stockage objet (vivant)",
                "echec",
                f"{type(exc).__name__} — REQUIS si configuré : les vérifications d'intégrité et la "
                "purge locale s'appuient dessus",
            )
            capacites["stockage"] = False
    else:
        rapport.ajouter(
            "stockage objet (vivant)",
            "indisponible",
            "SEAMTECH_S3_ENDPOINT_URL absent — capacité optionnelle : stockage local seulement, "
            "aucune copie hors-site (les parcours restent exécutables ; l'intégrité objet n'est pas exercée)",
        )
        capacites["stockage"] = False

    # --- OCR (optionnel) -----------------------------------------------------
    langues = _langues_tesseract()
    if "fra" in langues:
        rapport.ajouter("ocr (tesseract + fra)", "ok", "étage 3 disponible pour les pages sans texte")
        capacites["ocr"] = True
    else:
        rapport.ajouter(
            "ocr (tesseract + fra)",
            "indisponible",
            "absent — capacité OPTIONNELLE : l'étage 3 (pages sans texte) ne s'exécute pas ; "
            "les PDF porteurs de texte sont traités normalement",
        )
        capacites["ocr"] = False

    # --- Rendu PDF (optionnel) ----------------------------------------------
    if _commande_presente("pdftoppm"):
        rapport.ajouter("rendu PDF (pdftoppm)", "ok", "rendu d'image disponible")
        capacites["rendu_pdf"] = True
    else:
        rapport.ajouter(
            "rendu PDF (pdftoppm)",
            "indisponible",
            "absent — capacité OPTIONNELLE : pas de rendu image, l'aperçu PDF natif reste servi",
        )
        capacites["rendu_pdf"] = False

    # --- Modèles e5 (optionnel) ---------------------------------------------
    # Même règle de résolution que l'application (api._dossier_modeles_ml) :
    # surcharge SEAMTECH_ML_MODELE_DIR, sinon <données>/modeles.
    try:
        from seamtech_search.ml.encodeur import FICHIER_MODELE, FICHIER_TOKENIZER

        surclasse = os.environ.get("SEAMTECH_ML_MODELE_DIR")
        dossier = Path(surclasse) if surclasse else Path(config.database_path).resolve().parent / "modeles"
        if (dossier / FICHIER_MODELE).exists() and (dossier / FICHIER_TOKENIZER).exists():
            rapport.ajouter("recherche vectorielle (modèles e5)", "ok", f"modèles présents dans {dossier}")
            capacites["vecteurs"] = True
        else:
            rapport.ajouter(
                "recherche vectorielle (modèles e5)",
                "indisponible",
                f"modèles absents de {dossier} ({FICHIER_MODELE}/{FICHIER_TOKENIZER}) — capacité "
                "OPTIONNELLE : la recherche textuelle/pondérée et les filtres restent complets. "
                "Provisionnement explicite AVANT la coupure réseau : "
                "python -m seamtech_search.ml.telecharger",
            )
            capacites["vecteurs"] = False
    except Exception as exc:  # pragma: no cover - dépend de l'installation
        rapport.ajouter("recherche vectorielle (modèles e5)", "indisponible", f"non évaluable ({type(exc).__name__})")
        capacites["vecteurs"] = False

    # --- Images conteneurs (nécessaire à l'installation atelier) -------------
    if not _commande_presente("docker"):
        rapport.ajouter(
            "images conteneurs",
            "indisponible",
            "docker absent de CETTE machine — inspection seulement ; le serveur d'atelier doit "
            "avoir ses images AVANT la coupure réseau (elles ne se téléchargent plus ensuite)",
        )
    else:
        rapport.ajouter("images conteneurs", "ok", "docker présent sur cette machine")

    return capacites


# ---------------------------------------------------------------------------
# Parcours essentiels
# ---------------------------------------------------------------------------


def _premier_pdf(dossier: Path) -> Path | None:
    fichiers = sorted(p for p in dossier.rglob("*") if p.suffix.lower() == ".pdf")
    return fichiers[0] if fichiers else None


def parcours(
    rapport: Rapport, config: Any, capacites: dict[str, Any], racine_essai: Path
) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.comptes.comptes import creer_utilisateur
    from seamtech_search.indexer import SearchIndex

    # Les dossiers d'essai sont des COPIES : l'archive d'origine n'est jamais
    # modifiée (ni par l'import, ni par les rapports qu'il produit). Le
    # répertoire de travail est STABLE d'une exécution à l'autre : une fiche
    # déjà déposée garde un chemin source DANS les racines autorisées, sinon sa
    # pièce principale devient introuvable au 2e passage (et la vérification
    # accuserait à tort l'application).
    #    Le RÉPERTOIRE PARENT est unique à chaque exécution (nouveau chemin
    #    source ⇒ nouveau dossier à traiter, donc pas de « déjà traité » qui
    #    ferait sauter le dépôt), mais il reste SOUS la racine stable : les
    #    fiches réutilisées gardent un chemin source autorisé.
    etiquette = datetime.now().strftime("%Y%m%d-%H%M%S")
    dossier_import = racine_essai / etiquette / "CLIENT-123"
    dossier_fiche = racine_essai / etiquette / "CLIENT-GENOA"
    for source, cible in (
        (RACINE / "sample_data" / "CLIENT-123", dossier_import),
        (RACINE / "sample_data" / "CLIENT-GENOA", dossier_fiche),
    ):
        cible.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(cible, ignore_errors=True)
        shutil.copytree(source, cible)
    config.root_paths = [racine_essai, RACINE / "sample_data"]

    detail: dict[str, Any] = {}
    client = TestClient(create_app(config))
    jeton_service = {"X-SEAMTECH-TOKEN": config.auth_token} if config.auth_token else {}

    with client:
        # 1. CONNEXION nominative : compte créé par le CLI, session en base.
        try:
            index = SearchIndex(config.database_path, config.database_url)
            index.initialize()
            index.run_migrations()
            try:
                creer_utilisateur(
                    index, "hors-ligne-operateur", "Opérateur hors ligne", "motdepasse-hors-ligne"
                )
            except Exception:
                pass  # déjà créé : la vérification est rejouable
            finally:
                index.close()

            reponse = client.post(
                "/auth/connexion",
                json={"identifiant": "hors-ligne-operateur", "mot_de_passe": "motdepasse-hors-ligne"},
                headers=jeton_service,
            )
            assert reponse.status_code == 200, reponse.text
            session = reponse.json()
            entetes = {
                **jeton_service,
                "X-SEAMTECH-UTILISATEUR": session["identifiant"],
                "X-SEAMTECH-ROLE": session["role"],
                "X-SEAMTECH-SESSION": str(session["id_session"]),
                "X-SEAMTECH-SESSION-JETON": str(session["jeton_session"]),
            }
            rapport.ajouter("connexion nominative", "ok", f"session ouverte pour {session['identifiant']}")
        except Exception as exc:
            rapport.ajouter("connexion nominative", "echec", f"{type(exc).__name__} : {exc}")
            return detail

        # 2. IMPORT d'un dossier réel (pipeline complet) + comptabilité.
        try:
            reponse = client.post(
                "/imports", params={"wait": "true"}, json={"source_path": str(dossier_import)}, headers=entetes
            )
            assert reponse.status_code == 200, reponse.text
            resultat = reponse.json()
            import_id = resultat["import_id"]
            assert resultat["files_detected"] >= 1, resultat
            rapport.ajouter(
                "import + extraction",
                "ok",
                f"{resultat['files_detected']} fichier(s) détecté(s), {resultat['analyzed_files']} analysé(s), "
                f"statut {resultat['status']}",
            )
            detail["import_id"] = import_id
        except Exception as exc:
            rapport.ajouter("import + extraction", "echec", f"{type(exc).__name__} : {exc}")
            return detail

        # 3. RAPPORT généré, téléchargé par l'API (octets PDF vérifiés).
        try:
            reponse = client.get(f"/imports/{import_id}/artifacts/report_pdf", headers=entetes)
            assert reponse.status_code == 200, reponse.text
            assert reponse.content[:4] == b"%PDF", reponse.content[:16]
            rapport.ajouter("téléchargement du rapport généré", "ok", f"{len(reponse.content)} octets PDF servis")
        except Exception as exc:
            rapport.ajouter("téléchargement du rapport généré", "echec", f"{type(exc).__name__} : {exc}")

        # 4. DÉPÔT d'un dossier dans la base métier (fiche + pièces + lot).
        #    Le dossier est une COPIE, et le verrou RG11 (corrections humaines)
        #    est levé EXPLICITEMENT par le chemin documenté (`rouvrir` avec
        #    `effacer_corrections`) : c'est ce que ferait un opérateur, et cela
        #    rend la vérification rejouable sans jamais forcer la base.
        code: str | None = None

        def deposer() -> Any:
            return client.post("/imports/dossier", json={"dossier": str(dossier_fiche)}, headers=entetes)

        try:
            reponse = deposer()
            assert reponse.status_code in (200, 201, 422), reponse.text
            resultat = reponse.json() if reponse.status_code < 400 else {"statut": "echec", "raison": reponse.text}
            statut = str(resultat.get("statut") or "")
            raison = str(resultat.get("raison") or "")
            code = str(resultat.get("fiche") or "")

            if statut not in ("traite", "deja_traite") and ("RG11" in raison or "verrou" in raison.lower()):
                # Ré-exécution : la fiche porte des corrections humaines.
                fiches = client.get("/fiches", params={"taille": 200}, headers=entetes).json()
                liste = fiches.get("fiches", fiches) if isinstance(fiches, dict) else fiches
                code = str(liste[0]["code"]) if liste else ""
                assert code, f"aucune fiche retrouvée après refus RG11 : {raison}"
                rouvrir = client.post(
                    f"/fiches/{code}/rouvrir",
                    json={"effacer_corrections": True, "commentaire": "vérification hors ligne"},
                    headers=entetes,
                )
                assert rouvrir.status_code == 200, rouvrir.text
                reponse = deposer()
                assert reponse.status_code in (200, 201), reponse.text
                resultat = reponse.json()
                statut = str(resultat.get("statut") or "")
                code = str(resultat.get("fiche") or code)
                rapport.ajouter(
                    "levée du verrou RG11 (chemin documenté)",
                    "ok",
                    f"corrections humaines effacées explicitement, dossier redéposé (fiche {code})",
                )

            if statut in ("traite", "deja_traite"):
                if not code:
                    fiches = client.get("/fiches", params={"taille": 200}, headers=entetes).json()
                    liste = fiches.get("fiches", fiches) if isinstance(fiches, dict) else fiches
                    code = str(liste[0]["code"]) if liste else ""
                rapport.ajouter(
                    "dépôt d'un dossier (fiche + pièces)",
                    "ok",
                    f"fiche {code} traitée (statut {statut}), lot {resultat.get('id_lot')} suivi, "
                    f"{len(resultat.get('artifacts') or [])} artefact(s)",
                )
            else:
                raise AssertionError(f"dépôt refusé : {raison or resultat}")
            assert code, "code de fiche introuvable"
            detail["code"] = code
        except Exception as exc:
            rapport.ajouter("dépôt d'un dossier (fiche + pièces)", "echec", f"{type(exc).__name__} : {exc}")

        # 5. CORRECTION + VALIDATION avec révision (verrou optimiste).
        if code:
            try:
                champs = client.get(f"/fiches/{code}/champs", headers=entetes).json()
                assert champs, "aucun champ extrait"
                fiche = client.get(f"/fiches/{code}", headers=entetes).json()
                revision = fiche.get("revision")
                assert isinstance(revision, int) and revision >= 1, fiche
                cible = next((c for c in champs if not str(c["champ"]).startswith("cotes.")), champs[0])
                reponse = client.post(
                    f"/fiches/{code}/corriger",
                    json={
                        "champ": cible["champ"],
                        "valeur": "VALEUR-HORS-LIGNE",
                        "rang": cible.get("rang"),
                        "revision": revision,
                    },
                    headers=entetes,
                )
                assert reponse.status_code == 200, reponse.text
                nouvelle = reponse.json().get("revision")
                assert nouvelle == revision + 1, (revision, nouvelle)
                # Une correction fondée sur l'ANCIENNE révision doit être refusée.
                refus = client.post(
                    f"/fiches/{code}/corriger",
                    json={"champ": cible["champ"], "valeur": "PERIMEE", "rang": cible.get("rang"), "revision": revision},
                    headers=entetes,
                )
                assert refus.status_code == 409, refus.text
                rapport.ajouter(
                    "correction + verrou optimiste",
                    "ok",
                    f"révision {revision} → {nouvelle}, correction périmée refusée (409 conflit_revision)",
                )
            except Exception as exc:
                rapport.ajouter("correction + verrou optimiste", "echec", f"{type(exc).__name__} : {exc}")

        # 6. RECHERCHE : la fiche déposée doit être TROUVÉE (visibilité après
        #    import), et la requête ne doit produire aucun appel externe.
        #    NB : un code de fiche qui contient « nombre + unité » (0901-MM) est
        #    d'abord lu comme une COTE par l'analyseur dimensionnel ; on essaie
        #    donc aussi la partie numérique, et on DIT lequel des deux a trouvé.
        try:
            if code:
                reponses: dict[str, Any] = {}
                trouve_avec: str | None = None
                for requete in (code, code.split("-")[0]):
                    reponse = client.get("/recherche", params={"q": requete}, headers=entetes)
                    if reponse.status_code == 404:
                        break
                    assert reponse.status_code == 200, reponse.text
                    corps = reponse.json()
                    resultats = corps.get("resultats", corps) if isinstance(corps, dict) else corps
                    reponses[requete] = corps
                    if any(str(r.get("code") or "") == code for r in resultats):
                        trouve_avec = requete
                        break
                if not reponses:
                    rapport.ajouter(
                        "recherche (visibilité après import)",
                        "indisponible",
                        "route /recherche non montée (couche optionnelle absente de cette installation)",
                    )
                elif trouve_avec is None:
                    raise AssertionError(
                        f"la fiche {code} déposée n'est visible par aucune requête : "
                        f"{ {q: c.get('nb_resultats') for q, c in reponses.items()} }"
                    )
                else:
                    lecture_dimension = trouve_avec != code
                    rapport.ajouter(
                        "recherche (visibilité après import)",
                        "ok",
                        f"fiche {code} retrouvée par « {trouve_avec} » "
                        + (
                            f"(« {code} » seul est d'abord interprété comme une dimension : 0 résultat — "
                            "comportement de l'analyseur dimensionnel, à connaître pour la formation)"
                            if lecture_dimension
                            else "(code entier)"
                        ),
                    )
            else:
                rapport.ajouter(
                    "recherche (visibilité après import)",
                    "indisponible",
                    "aucune fiche déposée dans cette exécution — rien à chercher",
                )
        except Exception as exc:
            rapport.ajouter("recherche (visibilité après import)", "echec", f"{type(exc).__name__} : {exc}")

        # 7. APERÇU + TÉLÉCHARGEMENT DE L'ORIGINAL (pièce de la fiche).
        if code:
            try:
                pieces = client.get(f"/fiches/{code}/pieces", headers=entetes).json().get("pieces", [])
                assert pieces, "aucune pièce rattachée à la fiche"
                piece = next((p for p in pieces if p.get("is_primary_pdf")), pieces[0])
                identifiant = piece["id"]  # clé publique des écrans actuels (§pieces_de_fiche)
                apercu = client.get(f"/pieces/{identifiant}/apercu", headers=entetes)
                assert apercu.status_code == 200, apercu.text
                assert apercu.content[:4] == b"%PDF", apercu.content[:16]
                original = client.get(f"/pieces/{identifiant}/telecharger", headers=entetes)
                assert original.status_code in (200, 302), original.text
                if original.status_code == 200:
                    assert original.content[:4] == b"%PDF", original.content[:16]
                rapport.ajouter(
                    "aperçu + original PDF",
                    "ok",
                    f"pièce {identifiant} : aperçu servi, original servi par l'API "
                    "(proxy par défaut : aucune URL interne exposée au navigateur)",
                )
            except Exception as exc:
                rapport.ajouter("aperçu + original PDF", "echec", f"{type(exc).__name__} : {exc}")

        # 8. OUVERTURE de l'original par chemin (poste de travail, /open).
        try:
            cible = _premier_pdf(dossier_fiche)
            assert cible is not None, "aucun PDF dans le dossier d'exemple"
            reponse = client.post("/open", params={"path": str(cible)}, headers=entetes)
            assert reponse.status_code in (200, 302), reponse.text
            rapport.ajouter("ouverture directe d'un original (/open)", "ok", f"HTTP {reponse.status_code} pour {cible.name}")
        except Exception as exc:
            rapport.ajouter("ouverture directe d'un original (/open)", "echec", f"{type(exc).__name__} : {exc}")

    return detail


# ---------------------------------------------------------------------------
# Entrée
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--inventaire", action="store_true", help="constater le provisionnement (aucun blocage)")
    analyseur.add_argument("--executer", action="store_true", help="exécuter les parcours, réseau externe BLOQUÉ")
    analyseur.add_argument(
        "--autoriser-externe",
        action="store_true",
        help="diagnostic : ne pas bloquer, mais LISTER les connexions externes tentées",
    )
    analyseur.add_argument("--rapport", type=Path, help="écrire le rapport JSON à ce chemin")
    analyseur.add_argument(
        "--travail",
        type=Path,
        default=Path(os.environ.get("SEAMTECH_HORS_LIGNE_TRAVAIL") or "/tmp/seamtech-hors-ligne"),
        help="répertoire de travail STABLE (copies des dossiers d'essai) — défaut /tmp/seamtech-hors-ligne",
    )
    analyseur.add_argument(
        "--autoriser-hote",
        action="append",
        default=[],
        help="hôte local supplémentaire autorisé (alias du serveur d'atelier), répétable",
    )
    arguments = analyseur.parse_args(argv)
    if not (arguments.inventaire or arguments.executer):
        analyseur.error("préciser --inventaire et/ou --executer")

    rapport = Rapport()
    print("=== Vérification HORS LIGNE — SEAMTECH Search ===", flush=True)
    config = config_depuis_environnement()
    capacites: dict[str, Any] = {}

    print("\n-- 1. Inventaire du provisionnement (AVANT blocage)", flush=True)
    capacites = inventaire(rapport, config)

    tentatives: list[str] = []
    if arguments.executer:
        bloquer = not arguments.autoriser_externe
        garde = GardeReseau(bloquer=bloquer, hotes_autorises=set(arguments.autoriser_hote))
        print(
            "\n-- 2. Parcours essentiels ("
            + ("réseau externe BLOQUÉ" if bloquer else "réseau externe OBSERVÉ — diagnostic")
            + ")",
            flush=True,
        )
        arguments.travail.mkdir(parents=True, exist_ok=True)
        garde.installer()
        try:
            parcours(rapport, config, capacites, arguments.travail)
        finally:
            garde.retirer()
        tentatives = garde.tentatives
        if tentatives:
            rapport.ajouter(
                "aucune dépendance externe",
                "echec",
                f"{len(tentatives)} connexion(s) externe(s) tentée(s) : " + ", ".join(sorted(set(tentatives))[:10]),
            )
        else:
            rapport.ajouter(
                "aucune dépendance externe",
                "ok",
                "aucune connexion hors réseau privé tentée pendant les parcours essentiels (garde Python)",
            )

    print("\n-- 3. Ce qui reste à faire par un HUMAIN (non couvert ici)", flush=True)
    rapport.ajouter(
        "acceptation en atelier",
        "indisponible",
        "à exécuter sur le serveur d'atelier, réseau physiquement coupé et postes réels : cette "
        "vérification automatique ne la remplace pas",
    )

    echecs = rapport.echecs
    print("\n=== RÉSULTAT ===", flush=True)
    print(f"  échecs : {len(echecs)} | capacités indisponibles (dites, non contournées) : {len(rapport.indisponibles)}")
    if not arguments.executer:
        conclusion = (
            "INVENTAIRE SEUL — aucun parcours n'a été exécuté (relancer avec --executer) : "
            "cet inventaire ne prouve PAS l'aptitude hors ligne"
        )
    elif not echecs:
        conclusion = (
            "parcours essentiels VERTS sans réseau externe — acceptation atelier (serveur réel, réseau "
            "coupé, postes réels) TOUJOURS requise"
        )
    else:
        conclusion = "ÉCHEC — corriger les points ci-dessus avant tout déploiement"
    print("  conclusion : " + conclusion)

    if arguments.rapport:
        arguments.rapport.parent.mkdir(parents=True, exist_ok=True)
        arguments.rapport.write_text(
            json.dumps(
                {
                    "capacites": capacites,
                    "resultats": [r.__dict__ for r in rapport.resultats],
                    "tentatives_externes": tentatives,
                    "echecs": len(echecs),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  rapport JSON : {arguments.rapport}")

    return 1 if echecs else 0


if __name__ == "__main__":
    raise SystemExit(main())

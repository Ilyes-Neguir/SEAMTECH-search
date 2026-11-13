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
        --fiche <code> --cle-objet <cle> --jeton <token> \\
        --base-api <adresse de l'API> --base-cible <base DISTINCTE>

Aucune adresse n'est inscrite dans le code : une URL écrite est une URL qu'on
ne questionne plus (RG14). L'adresse de l'API est donc exigée à l'appel.

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
from urllib.parse import quote, urlparse

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
        cle_objet: str = "",
        jeton: str,
        compose: tuple[str, ...] = ("docker", "compose"),
        service_web: str = "web",
        service_minio: str = "minio",
        service_postgres: str = "postgres",
        utilisateur_bd: str = "seamtech",
        nom_base: str = "seamtech_search",
        base_source: str = "",
        preparer_base_cible: bool = True,
        utilisateur_session: str = "",
        mot_de_passe_session: str = "",
        bucket: str = "seamtech-documents",
        alias_stockage: str = "local",
        url_stockage: str = "",
        utilisateur_stockage: str = "",
        mot_de_passe_stockage: str = "",
        base_api: str = "",
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
        self.postgres = service_postgres
        self.utilisateur_bd = utilisateur_bd
        self.nom_base = nom_base
        self.base_source = base_source
        # Session NOMINATIVE : depuis le Lot 0b, le contenu des pièces exige
        # un humain en PostgreSQL — le jeton de service seul ne suffit plus
        # (securite_requetes.py:279, et le commentaire de recette_verif.py:100).
        # Sans elle, /pieces/{id}/telecharger répond 401 et la preuve
        # s'arrête au premier jalon, ici COMME sur le poste de l'exploitante.
        self.utilisateur_session = utilisateur_session
        self.mot_de_passe_session = mot_de_passe_session
        self.session: dict[str, str] = {}
        self.cle = cle_objet
        self.preparer_base_cible = preparer_base_cible
        self.bucket = bucket
        # L'alias « local » n'existe pas par défaut dans le conteneur : le
        # provisionnement du dépôt crée « provisionnement »
        # (scripts/provisionner_stockage.sh:70). Supposer « local » aurait
        # fait échouer CHAQUE commande mc par « alias introuvable » — une
        # erreur d'outillage lue, à tort, comme une erreur de sauvegarde.
        self.alias = alias_stockage
        self.url_stockage = url_stockage
        self.utilisateur_stockage = utilisateur_stockage
        self.mot_de_passe_stockage = mot_de_passe_stockage
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
        if not self.base_source:
            manques.append(
                "--base-source <url> — l'adresse de la base SOURCE. Elle sert à "
                "REFUSER une cible identique : restaurer par-dessus la source "
                "transformerait la preuve en destructeur."
            )
        if not self.jeton:
            manques.append("--jeton <token> — le jeton d'API (X-SEAMTECH-TOKEN)")
        # Prérequis, et non jalon : ce sont des DONNÉES D'ENTRÉE. S'ils
        # manquent, la preuve n'a pas démarré (code 2) — ce n'est pas le
        # système qui a échoué (code 1). Les refuser au premier jalon
        # ferait rapporter « la preuve a échoué » là où il fallait dire
        # « vous ne m'avez pas donné de compte ».
        if not self.utilisateur_session:
            manques.append(
                "--utilisateur <compte> — le compte nominatif. Depuis le Lot 0b, "
                "le téléchargement des pièces exige une session humaine : le "
                "jeton de service seul répond 401 (securite_requetes.py:279)."
            )
        if not self.mot_de_passe_session:
            manques.append(
                "--mot-de-passe <secret> — le mot de passe du compte nominatif. "
                "Aucun secret n'est inscrit dans le code : il est transmis en "
                "argument et jamais écrit sur disque."
            )
        if not self.api:
            manques.append(
                "--base-api <url> — l'adresse de l'API à éprouver. Aucune adresse "
                "n'est inscrite dans le code : une URL écrite est une URL qu'on ne "
                "questionne plus (RG14)."
            )
        if not self.url_stockage:
            manques.append(
                "--url-stockage <adresse> — l'adresse du stockage objet vue "
                "depuis le conteneur. Aucune adresse n'est inscrite dans le "
                "code, pas même une adresse de boucle locale (RG14)."
            )
        if not self.utilisateur_stockage or not self.mot_de_passe_stockage:
            manques.append(
                "--utilisateur-stockage / --mot-de-passe-stockage — les identités "
                "du stockage objet, pour créer l'alias « mc » depuis lequel "
                "l'objet sera supprimé puis réinjecté. Aucun secret n'est "
                "inscrit dans le code."
            )
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

        # 0. session nominative : SANS ELLE, RIEN NE DÉMARRE.
        # Depuis le Lot 0b, le contenu des pièces exige un humain en
        # PostgreSQL (securite_requetes.py:279). Le jeton de service seul
        # répond 401 sur /pieces/{id}/telecharger. Ouvrir la session AVANT
        # la première mesure évite de prendre un 401 pour une « pièce
        # absente » — l'erreur la plus coûteuse : elle ressemble au succès
        # de l'étape suivante.
        if not self._ouvrir_session():
            return self._rendre()

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

        # 3 bis. APPARIEMENT PIÈCE ↔ OBJET — lu en BASE, jamais par l'API.
        #
        # L'identifiant renvoyé par /fiches/{code}/pieces EST documents.id :
        # c'est ce que construit pieces_de_fiche (jointure fiche_piece_jointe
        # → documents). La clé de l'objet, elle, est délibérément ABSENTE des
        # réponses de l'API — choix de confidentialité, à ne pas contourner.
        # On la lit donc directement en base, ce qui est exactement le but :
        # sans cet appariement, supprimer « un objet au hasard » rendrait le
        # verdict PROUVÉ invalide, puisqu'on ne prouverait rien sur LA pièce
        # qu'on vient de mesurer.
        if not self._apparier_objet():
            return self._rendre()


        # 2 bis. l'alias « mc » : sans lui, CHAQUE commande de stockage
        # échoue par « alias introuvable ». Le créer ici rend la preuve
        # auto-suffisante au lieu de dépendre d'un alias qu'aucun
        # provisionnement du dépôt ne garantit (le leur s'appelle
        # « provisionnement »). « mc alias set » est idempotent.
        if not self._preparer_alias():
            return self._rendre()

        # 3. et 4. sauvegarde complète AVANT la perte, et contenu vérifié.
        #
        # L'ORDRE EST UNE CONTRAINTE, pas une préférence. La sauvegarde
        # complète copie les objets du bucket documents ; si l'objet est déjà
        # supprimé, ``head_object`` répond 404, ``copier_objets_s3`` le range
        # dans ``manquants`` et ``_exiger_complet`` LÈVE (sauvegarde.py:744,
        # sauvegarde_complete.py:113). Le bundle ne peut donc pas contenir un
        # objet déjà perdu, et la restauration ne peut pas le rendre : une
        # preuve qui supprime avant de sauvegarder ne prouverait rien, elle
        # échouerait en nommant « sauvegarde complète ». C'est aussi
        # l'exercice de reprise honnête : le bundle précède la perte, comme
        # dans la réalité — sinon la sauvegarde est faite pour l'occasion.
        bundle = self._sauvegarder()
        if bundle is None:
            return self._rendre()
        if not self._verifier(bundle):
            return self._rendre()

        # 5. et 6. suppression, puis preuve de disparition
        code, _, err = self._compose_exec(self.minio, "mc", "rm", f"{self.alias}/{self.bucket}/{self.cle}")
        if code != 0:
            self._etape("objet supprimé").echouer(f"mc rm a échoué (code={code}) : {err.strip()[:200]}")
            return self._rendre()
        self._etape("objet supprimé").reussir(f"{self.alias}/{self.bucket}/{self.cle}")

        if self._servir() is not None:
            self._etape("objet disparu").echouer(
                "l'application sert encore la pièce APRÈS la suppression : "
                "soit la suppression n'a rien fait, soit l'objet est servi depuis "
                "un cache — la preuve de perte est alors fausse"
            )
            return self._rendre()
        self._etape("objet disparu").reussir("la pièce n'est plus servie")

        # 7. restauration vers une cible distincte, octets identiques
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
        pièce, puis par la route de téléchargement — le chemin que
        ``scripts/recette_verif.py`` éprouve dans son contrôle
        « pdf-presigne », session nominative comprise. Réinventer une route
        aurait produit une preuve qui ne prouve rien.
        """
        if self._piece_id is None:
            if not self._trouver_piece():
                return None
        return self._telecharger_piece()

    def _ouvrir_session(self) -> bool:
        """Ouvre une session nominative APPROBATEUR.

        Pourquoi une session et pas seulement le jeton : depuis le Lot 0b, le
        téléchargement des pièces exige un humain (``exiger_acteur_humain``).
        Le rôle approbateur est celui qui autorise le téléchargement DIRECT,
        sans demande préalable (approbations.py:784) — sinon la preuve
        dépendrait d'une approbation qu'elle ne peut pas s'accorder seule.
        """
        if self.session:
            return True
        # Le corps passe en ARGUMENT, jamais dans un fichier : l'exécuteur
        # lance une liste sans shell, donc sans injection possible, et le
       # mot de passe ne s'écrit sur aucun disque.
        corps = json.dumps(
            {"identifiant": self.utilisateur_session, "mot_de_passe": self.mot_de_passe_session}
        )
        code, sortie, _ = self._executer(
            [
                "curl", "-s", "--max-time", "60", "-X", "POST",
                "-H", f"X-SEAMTECH-TOKEN: {self.jeton}",
                "-H", "Content-Type: application/json",
                "--data", corps,
                f"{self.api}/auth/connexion",
            ]
        )
        if code != 0:
            self._etape("session nominative").echouer(f"appel de connexion impossible (code={code})")
            return False
        try:
            reponse = json.loads(sortie)
        except json.JSONDecodeError:
            self._etape("session nominative").echouer(f"réponse illisible : {sortie.strip()[:200]}")
            return False
        if not isinstance(reponse, dict) or not reponse.get("id_session"):
            self._etape("session nominative").echouer(
                f"connexion refusée pour « {self.utilisateur_session} » : {sortie.strip()[:200]}"
            )
            return False
        self.session = {
            "X-SEAMTECH-UTILISATEUR": str(reponse.get("identifiant") or self.utilisateur_session),
            "X-SEAMTECH-ROLE": str(reponse.get("role") or ""),
            "X-SEAMTECH-SESSION": str(reponse["id_session"]),
            "X-SEAMTECH-SESSION-JETON": str(reponse.get("jeton_session") or ""),
        }
        self._etape("session nominative").reussir(
            f"session ouverte pour « {self.utilisateur_session} » (rôle "
            f"{self.session['X-SEAMTECH-ROLE'] or 'non précisé'})"
        )
        return True

    def _entetes(self) -> list[str]:
        """Jeton de service ET session nominative, dans cet ordre."""
        entetes = ["-H", f"X-SEAMTECH-TOKEN: {self.jeton}"]
        for nom, valeur in self.session.items():
            entetes += ["-H", f"{nom}: {valeur}"]
        return entetes

    def _trouver_piece(self) -> bool:
        code, sortie, _ = self._executer(
            [
                "curl", "-s", "--max-time", "60",
                *self._entetes(),
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
                    *self._entetes(),
                    f"{self.api}/pieces/{self._piece_id}/telecharger",
                ]
            )
            if code != 0 or sortie.strip() != "200":
                return None
            return Path(chemin).read_bytes()
        finally:
            Path(chemin).unlink(missing_ok=True)

    def _preparer_alias(self) -> bool:
        """Crée l'alias « mc » dans le conteneur de stockage.

        Sans alias, ``mc`` n'a aucune cible : il n'y a pas d'alias « local »
        par défaut dans le conteneur MinIO, et le provisionnement du dépôt
        crée le sien sous un AUTRE nom (provisionnement). Supposer un alias
        aurait transformé une erreur d'outillage en faux diagnostic de
        sauvegarde.
        """
        code, out, err = self._compose_exec(
            self.minio, "mc", "alias", "set", self.alias, self.url_stockage,
            self.utilisateur_stockage, self.mot_de_passe_stockage,
        )
        if code != 0:
            self._etape("alias de stockage").echouer(
                f"création de l'alias « {self.alias} » impossible (code={code}) : "
                f"{(err or out).strip()[:200]}"
            )
            return False
        code, out, err = self._compose_exec(
            self.minio, "mc", "ls", f"{self.alias}/{self.bucket}/",
        )
        if code != 0:
            self._etape("alias de stockage").echouer(
                f"alias « {self.alias} » créé mais le seau « {self.bucket} » est "
                f"inaccessible (code={code}) : {(err or out).strip()[:200]}"
            )
            return False
        self._etape("alias de stockage").reussir(
            f"{self.alias} → {self.url_stockage}, seau « {self.bucket} » joignable"
        )
        return True

    def _apparier_objet(self) -> bool:
        """Lit ``documents.object_key`` pour LA pièce mesurée, en base."""
        if self.cle:
            self._etape("appariement pièce ↔ objet").reussir(
                f"clé fournie à l'appel : {self.cle}"
            )
            return True
        if self._piece_id is None:
            self._etape("appariement pièce ↔ objet").echouer(
                "aucun identifiant de pièce : l'appariement est impossible"
            )
            return False
        code, out, err = self._compose_exec(
            self.postgres, "psql", "-U", self.utilisateur_bd, "-d", self.nom_base,
            "-tA", "-c", f"SELECT object_key FROM documents WHERE id = {int(self._piece_id)}",
        )
        if code != 0:
            self._etape("appariement pièce ↔ objet").echouer(
                f"lecture en base impossible (code={code}) : {(err or out).strip()[:200]}"
            )
            return False
        cle = out.strip()
        if not cle or cle.upper() == "NULL":
            self._etape("appariement pièce ↔ objet").echouer(
                f"la pièce {self._piece_id} n'a aucune clé d'objet en base "
                "(documents.object_key vide) : elle n'est pas servie depuis le "
                "stockage objet, la preuve n'aurait rien prouvé"
            )
            return False
        self.cle = cle
        self._etape("appariement pièce ↔ objet").reussir(
            f"pièce {self._piece_id} → objet {cle} (lu en base, pas par l'API)"
        )
        return True

    def _nom_base_cible(self, url: str | None = None) -> str | None:
        """Le nom de base porté par une URL, sans la casser en morceaux."""
        cible = self.base_cible if url is None else url
        if not cible:
            return None
        nom = urlparse(cible).path.lstrip("/")
        return nom or None

    def _base_existe(self, nom: str) -> bool | None:
        """``True``/``False``, ou ``None`` si la lecture a échoué."""
        code, out, err = self._compose_exec(
            self.postgres, "psql", "-U", self.utilisateur_bd, "-d", "postgres",
            "-tA", "-c", f"SELECT 1 FROM pg_database WHERE datname = '{nom}'",
        )
        if code != 0:
            self._etape("base cible").echouer(
                f"lecture de pg_database impossible (code={code}) : {(err or out).strip()[:200]}"
            )
            return None
        return out.strip() == "1"

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
        cible = self._nom_base_cible()
        if cible is None:
            self._etape("base cible").echouer(
                f"--base-cible ne nomme aucune base : {self.base_cible!r}. "
                "Une URL sans nom de base ne désigne rien."
            )
            return None
        source = self._nom_base_cible(self.base_source)
        if source is None:
            self._etape("base cible").echouer(
                f"--base-source ne nomme aucune base : {self.base_source!r}. "
                "Sans la source, impossible de garantir que la cible est distincte."
            )
            return None
        if source == cible:
            self._etape("base cible").echouer(
                f"la base cible ({cible}) est IDENTIQUE à la base source : "
                "restaurer par-dessus la source détruirait les données que la "
                "preuve est censée protéger. Fournir une base DISTINCTE."
            )
            return None

        # SENS DE LA PRÉPARATION — et il est contre-intuitif.
        # ``sauvegarde.restaurer`` crée LUI-MÊME la base cible et REFUSE de
        # poursuivre si elle existe déjà (« la base cible « X » existe déjà —
        # restauration refusée »). Pré-créer la base ferait donc échouer la
        # restauration qu'on cherche à prouver. Le bon geste est l'inverse :
        # s'assurer qu'elle n'existe PAS, pour que la restauration la crée.
        if self.preparer_base_cible:
            existe = self._base_existe(cible)
            if existe is None:
                return None
            if existe:
                code, out, err = self._compose_exec(
                    self.postgres, "dropdb", "-U", self.utilisateur_bd, cible,
                )
                if code != 0:
                    self._etape("base cible").echouer(
                        f"dropdb impossible (code={code}) : {(err or out).strip()[:200]}"
                    )
                    return None
                self._etape("base cible").reussir(
                    f"{cible} libérée d'une exécution précédente ; distincte de {source}"
                )
            else:
                self._etape("base cible").reussir(
                    f"{cible} absente : la restauration la créera ; distincte de {source}"
                )

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
        cible = f"{self.alias}/{self.bucket}/{self.cle}"
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


def _aplatir(texte: str, limite: int = 300) -> str:
    """Aplatit un texte : une annotation CI ne garde que la première ligne."""
    return " ".join(str(texte).split())[:limite]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/preuve_objet_perdu.py",
        description="Prouve qu'un objet perdu du stockage revient depuis la sauvegarde complète.",
    )
    parser.add_argument("--fiche", default="", help="code de la fiche à éprouver")
    parser.add_argument(
        "--cle-objet", default="",
        help="clé de l'objet dans le stockage ; par défaut LUE EN BASE depuis "
             "l'identifiant de la pièce, l'API ne l'exposant jamais",
    )
    parser.add_argument("--jeton", default="", help="jeton d'API (X-SEAMTECH-TOKEN)")
    parser.add_argument(
        "--utilisateur", default="",
        help="compte nominatif pour la session (Lot 0b : le téléchargement "
             "des pièces exige un humain, le jeton de service ne suffit plus)",
    )
    parser.add_argument(
        "--mot-de-passe", default="",
        help="mot de passe du compte nominatif (aucun secret n'est inscrit "
             "dans le code : il est transmis en argument, jamais écrit sur disque)",
    )
    parser.add_argument("--base-cible", default="", help="base DISTINCTE pour la restauration")
    parser.add_argument(
        "--base-source", default="",
        help="URL de la base SOURCE — sert à REFUSER une cible identique",
    )
    parser.add_argument("--service-postgres", default="postgres")
    parser.add_argument("--utilisateur-bd", default="seamtech")
    parser.add_argument("--nom-base", default="seamtech_search")
    parser.add_argument("--service-web", default="web")
    parser.add_argument("--service-minio", default="minio")
    parser.add_argument("--bucket", default="seamtech-documents")
    parser.add_argument("--alias-stockage", default="local", help="alias « mc » à créer dans le conteneur")
    parser.add_argument(
        "--url-stockage", default="",
        help="adresse du stockage objet TELLE QUE VUE DEPUIS LE CONTENEUR "
             "(ex. le nom du service suivi de son port). Aucune adresse n'est "
             "inscrite dans le code : une adresse écrite est une adresse qu'on "
             "ne questionne plus (RG14).",
    )
    parser.add_argument("--utilisateur-stockage", default="", help="identité du stockage objet")
    parser.add_argument("--mot-de-passe-stockage", default="", help="secret du stockage objet")
    parser.add_argument(
        "--base-api", default="",
        help="adresse de l'API, schéma compris (ex. 127.0.0.1:8000 en précédant "
             "du schéma voulu) — exigée ; aucune adresse n'est inscrite dans "
             "le code (RG14)",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--annonces", action="store_true",
        help="publie le VERDICT en une seule annotation CI. Pourquoi : les "
             "journaux bruts de ce dépôt sont servis par un hôte inaccessible "
             "depuis l'environnement de travail, GitHub plafonne les "
             "annotations à dix par étape, et une étape bash qui capture la "
             "sortie perd la raison si elle meurt. Le script est le seul "
             "endroit qui connaît le verdict au moment où il le rend.",
    )
    args = parser.parse_args(argv)

    preuve = PreuveObjetPerdu(
        executeur=_executeur_reel,
        fiche=args.fiche,
        cle_objet=args.cle_objet,
        jeton=args.jeton,
        utilisateur_session=args.utilisateur,
        mot_de_passe_session=args.mot_de_passe,
        service_web=args.service_web,
        service_minio=args.service_minio,
        bucket=args.bucket,
        alias_stockage=args.alias_stockage,
        url_stockage=args.url_stockage,
        utilisateur_stockage=args.utilisateur_stockage,
        mot_de_passe_stockage=args.mot_de_passe_stockage,
        base_api=args.base_api,
        base_cible=args.base_cible,
        base_source=args.base_source,
        service_postgres=args.service_postgres,
        utilisateur_bd=args.utilisateur_bd,
        nom_base=args.nom_base,
    )
    rapport = preuve.courir()

    if args.annonces:
        # UNE annotation, émise AVANT tout le reste : c'est le seul canal
        # lisible d'ici, et le plafond est de dix par étape.
        manques = rapport.get("manques") or []
        detail = " ; ".join(manques) if manques else str(rapport.get("raison", ""))
        print(
            f"::notice title=preuve-{rapport['verdict'].replace(' ', '-')}::"
            f"{_aplatir(detail, 300)}",
            flush=True,
        )

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

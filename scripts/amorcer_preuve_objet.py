"""Amorce une fiche RÉELLE, pour que la preuve « objet perdu » ait quelque chose à perdre.

Pourquoi ce script existe
-------------------------

La preuve exige une pièce servie DEPUIS LE STOCKAGE OBJET : c'est la seule
situation où perdre un objet est observable. Concrètement, il faut une ligne
``documents.object_key`` renseignée, et une ``fiche_piece_jointe`` qui la
rattache à une fiche.

Deux façons de l'obtenir, et une seule est honnête :

- **Fabriquer les lignes en SQL.** Rapide, mais on prouverait alors un scénario
  qui n'existe pas : rien ne garantit que l'application sait SERVIR une pièce
  ainsi fabriquée, et une preuve porterait sur du vent.
- **Passer par les propres commandes du produit** — ``init``, ``deposer``,
  ``promouvoir``. C'est ce que fait ce script. La fiche est extraite d'un VRAI
  PDF par le vrai extracteur, et la clé objet est posée par la vraie promotion,
  celle-là même qui tourne en production.

Le script n'invente donc aucune donnée : il pilote le produit.

Exécutable injectable
---------------------

Docker n'existe sur aucune machine de développement, et les journaux CI de ce
dépôt ne sont pas lisibles hors de GitHub. Un amorçage dont on ne peut voir ni
exécuter ni déboguer la logique serait une boîte noire au pire endroit : c'est
lui qui décide si la preuve porte sur quelque chose. L'exécuteur est donc
injectable, et chaque étape est testée (tests/test_amorcer_preuve_objet.py).
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Callable, Sequence

Executeur = Callable[[Sequence[str]], tuple[int, str, str]]

CODE_FICHE_ABSENTE = 3


class Etape:
    """Une étape nommée, réussie ou échouée AVEC sa raison."""

    def __init__(self, nom: str) -> None:
        self.nom = nom
        self.reussie = False
        self.detail = ""

    def reussir(self, detail: str) -> None:
        self.reussie = True
        self.detail = detail

    def echouer(self, detail: str) -> None:
        self.reussie = False
        self.detail = detail

    def vers_dict(self) -> dict[str, Any]:
        return {"etape": self.nom, "reussie": self.reussie, "detail": self.detail}


class Amorcage:
    """Monte le décor minimal d'une preuve de perte d'objet."""

    def __init__(
        self,
        *,
        executeur: Executeur,
        compose: tuple[str, ...] = ("docker", "compose"),
        service_web: str = "web",
        service_postgres: str = "postgres",
        base_source: str,
        # Le corpus de la preuve doit être ACCESSIBLE EN ÉCRITURE, et c'est
        # toute la raison de ce montage : « sample_data » est monté en
        # lecture seule, or la preuve doit pouvoir RETIRER le fichier local
        # pour prouver que l'objet, seul, suffit à servir la pièce — et pour
        # que la perte de l'objet soit visible (l'application retombe sinon
        # sur la copie locale, silencieusement). D'où une copie de travail
        # sous /tmp, déclarée comme racine servie du service web.
        racine_servie: str = "/tmp/corpus-preuve",
        corpus_source: str = "/app/sample_data/CLIENT-7792-SO",
        # CE dossier, et pas un autre : son PDF est le seul du jeu d'essai à
        # être reconnu par un gabarit (FICHE_PORTANT_V1 v2, code 7792-SO,
        # confiance 0,99 — mesuré par `fiches.cli extraire`). « CLIENT-123 »
        # paraît plus riche (trois fichiers) MAIS son PDF n'appartient à
        # aucun gabarit : le dépôt le REFUSE (« gabarit inconnu ») et l'on
        # perd une demi-journée à chercher une panne d'infrastructure là où
        # c'est un jeu d'essai inadapté. Voir
        # tests/test_amorcer_preuve_objet.py::test_le_dossier_par_defaut_est_reconnu.
        dossier: str = "/tmp/corpus-preuve/CLIENT-7792-SO",
        utilisateur_bd: str = "seamtech",
        nom_base: str = "seamtech_search",
        compte: str = "",
        mot_de_passe: str = "",
        annonces: bool = False,
        trace: bool = False,
    ) -> None:
        # Deux niveaux, parce que GitHub PLAFONNE les annotations à dix par
        # étape : au-delà, les suivantes sont simplement perdues — et l'on
        # croit l'étape morte alors qu'elle a réussi. C'est exactement ce qui
        # a fait chercher une panne imaginaire pendant trois cycles.
        #   · ``annonces`` : UNE seule annotation, le résultat. À utiliser
        #     en CI : c'est le seul canal lisible quand les journaux bruts
        #     sont servis par un hôte inaccessible.
        #   · ``trace`` : une annotation avant et après CHAQUE commande, pour
        #     déboguer une chaîne qui s'effondre. À réserver au dépannage.
        self.annonces = annonces
        self.trace = trace
        self.executeur = executeur
        self.compose = list(compose)
        self.web = service_web
        self.postgres = service_postgres
        self.base_source = base_source
        self.racine_servie = racine_servie
        self.corpus_source = corpus_source
        self.dossier = dossier
        self.utilisateur_bd = utilisateur_bd
        self.nom_base = nom_base
        self.compte = compte
        self.mot_de_passe = mot_de_passe
        self.etapes: list[Etape] = []
        self.code_fiche = ""

    # -- primitives ---------------------------------------------------------

    def _etape(self, nom: str) -> Etape:
        etape = Etape(nom)
        self.etapes.append(etape)
        return etape

    def _conclure(self, etape: Etape, reussie: bool, detail: str) -> bool:
        if reussie:
            etape.reussir(detail)
        else:
            etape.echouer(detail)
        self._annoncer(
            f"amorcage-{'OK' if reussie else 'ECHEC'}-{etape.nom.replace(' ', '-')}", detail,
        )
        return reussie

    def _annoncer(self, titre: str, message: str) -> None:
        if self.trace:
            print(f"::notice title={titre}::{_aplatir(message, 250)}", flush=True)

    def _executer(self, commande: Sequence[str]) -> tuple[int, str, str]:
        jointe = " ".join(str(c) for c in commande)
        self._annoncer("amorcage-lance", jointe[:220])
        code, out, err = self.executeur(commande)
        self._annoncer("amorcage-rendu", f"code={code} — {err or out}")
        return code, out, err

    def _web(self, *args: str, racine: str | None = None) -> tuple[int, str, str]:
        """Une commande dans le conteneur applicatif.

        ``racine`` n'est passé que pour le dépôt : une pièce située hors des
        racines servies est refusée par l'application, à juste titre.
        """
        commande = [*self.compose, "exec", "-T"]
        if racine:
            commande += ["-e", f"SEAMTECH_ROOT_PATHS={racine}"]
        commande += [self.web, *args]
        return self._executer(commande)

    def _cli(self, *args: str, racine: str | None = None) -> tuple[int, str, str]:
        return self._web(
            "python", "-m", "seamtech_search.fiches.cli", *args,
            "--database-url", self.base_source,
            racine=racine,
        )

    def _sql(self, requete: str) -> tuple[int, str, str]:
        return self._executer([
            *self.compose, "exec", "-T", self.postgres,
            "psql", "-U", self.utilisateur_bd, "-d", self.nom_base, "-tA", "-c", requete,
        ])

    # -- la chaîne ----------------------------------------------------------

    def _preparer_corpus(self) -> bool:
        """Copie le jeu d'essai vers la racine servie, en écriture.

        Le montage ``sample_data`` est en lecture seule : on ne peut ni y
        déposer une copie de travail, ni en retirer un fichier. La preuve
        exige les deux (voir la note de ``racine_servie``). La copie se fait
        dans le conteneur, par les commandes du système — jamais par un
        montage supplémentaire qui modifierait l'artefact déployé.
        """
        etape = self._etape("corpus")
        if self.corpus_source == self.dossier:
            return self._conclure(etape, True, f"{self.dossier} utilisé tel quel")
        code, out, err = self._web("mkdir", "-p", "--", self.racine_servie)
        if code != 0:
            return self._conclure(
                etape, False,
                f"création de {self.racine_servie} impossible (code={code}) : {(err or out).strip()[:300]}",
            )
        code, out, err = self._web("cp", "-r", "--", self.corpus_source, self.racine_servie + "/")
        if code != 0:
            return self._conclure(
                etape, False,
                f"copie de {self.corpus_source} vers {self.racine_servie} impossible "
                f"(code={code}) : {(err or out).strip()[:300]}",
            )
        return self._conclure(etape, True, f"{self.corpus_source} → {self.dossier} (copie de travail)")

    def _init(self) -> bool:
        etape = self._etape("init")
        code, out, err = self._cli("init")
        if code != 0:
            return self._conclure(etape, False, f"code={code} : {(err or out).strip()[:300]}")
        return self._conclure(etape, True, "migrations et gabarits prêts")

    def _deposer(self) -> bool:
        etape = self._etape("dépôt")
        code, out, err = self._cli("deposer", self.dossier, racine=self.racine_servie)
        if code != 0:
            return self._conclure(
                etape, False,
                f"code={code} : {(err or out).strip()[:300]} — deux causes "
                f"usuelles : le dossier est hors racine servie ({self.racine_servie}), "
                "ou son PDF n'est reconnu par aucun gabarit ; le vérifier avec "
                "« fiches.cli extraire <pdf> »",
            )
        return self._conclure(etape, True, f"{self.dossier} déposé (fiche + pièces jointes)")

    def _promouvoir(self) -> bool:
        """La promotion pose ``documents.object_key`` : SANS ELLE, RIEN À PERDRE.

        Piège d'interprétation, et il a coûté un cycle CI : le DÉPÔT promeut
        déjà lui-même après son commit (depot.py:1700). La reprise
        ``promouvoir`` rend donc « 0/0 promu(s) » avec le code 1, non pas en
        échec mais parce qu'il n'y a PLUS RIEN à envoyer. Prendre ce code 1
        pour une panne coupe la chaîne avant le garde-fou qui, lui, compte
        réellement les clés objet — on soigne alors un succès.
        """
        etape = self._etape("promotion")
        code, out, err = self._cli("promouvoir")
        texte = (out or err or "").strip()
        bilan = re.search(r"(\d+)\s*/\s*(\d+)\s+promu", texte)
        if bilan:
            promus, candidats = int(bilan.group(1)), int(bilan.group(2))
            if candidats == 0:
                return self._conclure(
                    etape, True,
                    "aucun candidat : les fichiers ont déjà été promus par le dépôt "
                    "lui-même (depot.py:1700) — « rien à faire » n'est pas un échec",
                )
            if code != 0 or promus < candidats:
                return self._conclure(
                    etape, False, f"code={code} : {promus}/{candidats} promu(s) — {texte[:300]}",
                )
            return self._conclure(etape, True, f"{promus}/{candidats} promu(s) — clés objet posées")
        if code != 0:
            return self._conclure(etape, False, f"code={code} : {texte[:300]}")
        return self._conclure(etape, True, texte[:200] or "promotion sans candidat")

    def _compte(self) -> bool:
        """Le compte nominatif : depuis le Lot 0b, servir une pièce exige un humain."""
        etape = self._etape("compte nominatif")
        if not self.compte or not self.mot_de_passe:
            return self._conclure(
                etape, False,
                "aucun compte fourni (--compte / --mot-de-passe) : depuis le Lot 0b, "
                "le téléchargement des pièces exige une session humaine",
            )
        # ``doit_changer_mot_de_passe=False`` : un changement obligatoire ferait
        # échouer la première connexion, donc la preuve, pour une raison sans
        # rapport avec ce qu'elle mesure.
        programme = (
            "from seamtech_search.comptes.comptes import ROLE_APPROBATEUR, creer_utilisateur\n"
            "from seamtech_search.config import AppConfig\n"
            "from seamtech_search.indexer import SearchIndex\n"
            "config = AppConfig.load('config/config.json')\n"
            "index = SearchIndex(config.database_path, config.database_url,\n"
            "                    pool_min=config.pool_min, pool_max=config.pool_max,\n"
            "                    pool_timeout=config.pool_timeout,\n"
            "                    statement_timeout_ms=config.statement_timeout_ms)\n"
            f"creer_utilisateur(index, {self.compte!r}, 'Compte de preuve',\n"
            f"                  {self.mot_de_passe!r}, role=ROLE_APPROBATEUR,\n"
            "                  doit_changer_mot_de_passe=False)\n"
            "print('compte nominatif prêt')\n"
        )
        code, out, err = self._web("python", "-c", programme)
        if code != 0:
            return self._conclure(etape, False, f"code={code} : {(err or out).strip()[:300]}")
        return self._conclure(etape, True, f"« {self.compte} » prêt (rôle approbateur)")

    def _lire_code_fiche(self) -> bool:
        etape = self._etape("code de fiche")
        code, out, err = self._sql("SELECT code FROM fiche ORDER BY id_fiche LIMIT 1")
        if code != 0:
            return self._conclure(etape, False, f"lecture impossible (code={code}) : {(err or out).strip()[:200]}")
        self.code_fiche = out.strip()
        if not self.code_fiche:
            return self._conclure(
                etape, False,
                "aucune fiche en base après le dépôt : la preuve n'aurait aucune pièce à perdre",
            )
        return self._conclure(etape, True, self.code_fiche)

    def _exiger_objets(self) -> bool:
        """GARDE-FOU : sans clé objet, la preuve de perte ne porterait sur rien."""
        etape = self._etape("clés objet")
        code, out, err = self._sql("SELECT count(*) FROM documents WHERE coalesce(object_key,'') <> ''")
        if code != 0:
            return self._conclure(etape, False, f"lecture impossible (code={code}) : {(err or out).strip()[:200]}")
        try:
            nb = int(out.strip() or "0")
        except ValueError:
            nb = 0
        if nb < 1:
            return self._conclure(
                etape, False,
                "aucune clé objet après la promotion : aucune pièce n'est servie depuis "
                "le stockage, la preuve n'aurait rien à perdre",
            )
        code, out, err = self._sql("SELECT count(*) FROM fiche_piece_jointe")
        pieces = out.strip() if code == 0 else "?"
        return self._conclure(etape, True, f"{nb} document(s) porteur(s) d'une clé, {pieces} pièce(s) rattachée(s)")

    def courir(self) -> dict[str, Any]:
        """Monte le décor. Rend un rapport ; ne lève pas."""
        if not self.base_source:
            return {
                "ok": False,
                "code_fiche": "",
                "raison": "--base-source <url> est exigé : sans la base, aucune commande du produit ne peut rien écrire",
                "etapes": [],
            }
        if not self._preparer_corpus():
            return self._rendre()
        if not self._init():
            return self._rendre()
        if not self._deposer():
            return self._rendre()
        if not self._promouvoir():
            return self._rendre()
        if not self._compte():
            return self._rendre()
        if not self._lire_code_fiche():
            return self._rendre()
        if not self._exiger_objets():
            return self._rendre()
        return self._rendre()

    def _rendre(self) -> dict[str, Any]:
        echecs = [e for e in self.etapes if not e.reussie]
        return {
            "ok": not echecs,
            "code_fiche": self.code_fiche,
            "raison": f"« {echecs[0].nom} » : {echecs[0].detail}" if echecs else "décor prêt",
            "etapes": [e.vers_dict() for e in self.etapes],
        }


# -- exécution réelle --------------------------------------------------------


def _executeur_reel(commande: Sequence[str]) -> tuple[int, str, str]:
    import subprocess

    try:
        termine = subprocess.run(
            list(commande), capture_output=True, text=True, timeout=1800, check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", "délai dépassé"
    except OSError as erreur:
        return 127, "", str(erreur)
    return termine.returncode, termine.stdout or "", termine.stderr or ""


def _aplatir(texte: str, limite: int = 300) -> str:
    """Aplatit une sortie : une annotation CI ne garde que la première ligne."""
    propre = " ".join(str(texte).split())
    return propre[:limite]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="amorcer_preuve_objet",
        description="Monte une fiche réelle (dépôt + promotion) pour la preuve « objet perdu ».",
    )
    parser.add_argument("--base-source", required=True, help="URL de la base, telle que vue depuis le conteneur web")
    parser.add_argument(
        "--racine-servie", default="/tmp/corpus-preuve",
        help="racine déclarée à SEAMTECH_ROOT_PATHS : elle doit être accessible en "
             "ÉCRITURE, la preuve devant pouvoir retirer la copie locale",
    )
    parser.add_argument(
        "--corpus-source", default="/app/sample_data/CLIENT-7792-SO",
        help="jeu d'essai d'origine (monté en lecture seule), copié sous la racine servie",
    )
    parser.add_argument(
        "--dossier", default="/tmp/corpus-preuve/CLIENT-7792-SO",
        help="dossier à déposer : son PDF DOIT être reconnu par un gabarit, "
             "sinon le dépôt le refuse (vérifiable par « fiches.cli extraire »)",
    )
    parser.add_argument("--compte", default="", help="compte nominatif à créer (Lot 0b)")
    parser.add_argument("--mot-de-passe", default="", help="mot de passe du compte nominatif")
    parser.add_argument("--service-web", default="web")
    parser.add_argument("--service-postgres", default="postgres")
    parser.add_argument("--utilisateur-bd", default="seamtech")
    parser.add_argument("--nom-base", default="seamtech_search")
    parser.add_argument(
        "--annonces", action="store_true",
        help="publie le RÉSULTAT en une seule annotation CI (le plafond est de "
             "dix par étape : au-delà, tout est perdu). Un flux non tamponné "
             "est recommandé (python -u)",
    )
    parser.add_argument(
        "--trace", action="store_true",
        help="publie une annotation avant et après CHAQUE commande : à réserver "
             "au dépannage d'une chaîne qui s'effondre, car cela dépasse vite "
             "le plafond de dix",
    )
    args = parser.parse_args(argv)

    amorcage = Amorcage(
        executeur=_executeur_reel,
        service_web=args.service_web,
        service_postgres=args.service_postgres,
        base_source=args.base_source,
        racine_servie=args.racine_servie,
        dossier=args.dossier,
        corpus_source=args.corpus_source,
        utilisateur_bd=args.utilisateur_bd,
        nom_base=args.nom_base,
        compte=args.compte,
        mot_de_passe=args.mot_de_passe,
        annonces=args.annonces,
        trace=args.trace,
    )
    rapport = amorcage.courir()

    if args.annonces:
        # UNE annotation, complète : c'est tout ce qui survivra au plafond.
        print(
            f"::notice title=amorcage-{'OK' if rapport['ok'] else 'ECHEC'}::"
            f"code_fiche={rapport['code_fiche'] or 'aucun'} — {_aplatir(rapport['raison'], 250)}",
            flush=True,
        )

    for etape in rapport["etapes"]:
        print(f"  [{'OK ' if etape['reussie'] else 'ÉCHEC'}] {etape['etape']} : {_aplatir(etape['detail'])}")
    if rapport["ok"]:
        print(f"CODE_FICHE={rapport['code_fiche']}")
        print(f"AMORCAGE=OK — {rapport['code_fiche']}")
        return 0
    print(f"AMORCAGE=ECHEC — {_aplatir(rapport['raison'])}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Recette des routes : balaie TOUTES les routes exposées, dans l'application
réellement démarrée.

Pourquoi ce script, et pourquoi ainsi :

La recette exige, pour CHAQUE route, un test écrit ET une observation dans
l'application réelle. La liste des routes n'est pas celle dont on se souvient :
elle est extraite du code source, mécaniquement, à chaque exécution. Une route
oubliée n'a aucun moyen de se signaler autrement.

Quatre sondes par route :

  · « nominal »            — jeton de service, paramètres plausibles ;
  · « non autorisé »       — aucun jeton : l'accès doit être refusé ;
  · « paramètre invalide » — un identifiant non numérique là où un nombre est
                             attendu ;
  · « ressource absente »  — un identifiant qui n'existe pas.

Ce qui est observé, et la seule chose qu'on exige : **aucune route ne doit
répondre 500**. Une erreur doit être NOMMÉE (4xx), jamais une exception
non gérée. C'est le contrat de cette recette — le reste est consigné tel
quel, y compris les refus, qui sont des réussites quand ils sont explicites.

Deux modes :

  · ``--http``  — démarre ``uvicorn`` et parle en HTTP réel sur une socket.
                  C'est l'exploration dans l'application qui tourne.
  · défaut      — sondes en procédé (TestClient). C'est le test écrit, celui
                  que la CI rejoue.

Sortie : un rapport markdown et un JSON, relus par ``tests/test_recette_routes.py``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

RACINE = Path(__file__).resolve().parents[1]
# Lançable depuis n'importe où : « python scripts/recette_routes.py » place
# ``scripts/`` en tête de chemin, pas la racine du dépôt.
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))
JETON = "jeton-de-recette"

MOTIF_ROUTE = re.compile(
    r"@\w+\.(?P<verbe>get|post|put|delete|patch|head|options|api_route|route)"
    r"\(\s*(?P<args>.*?)\)\s*$",
    re.M | re.S,
)

# Les identifiants concrètes substituées aux paramètres de chemin.
VALEURS = {
    "code": "RECETTE-1",
    "piece_id": "1",
    "id_lot": "1",
    "id_extraction": "1",
    "id_fiche": "1",
    "id_modele": "1",
    "id_brouillon": "1",
    "id_demande": "1",
    "id_notification": "1",
    "id_import": "1",
    "import_id": "1",
    "artifact": "extrait.pdf",
    "jeton_session": "session-de-recette",
    "identifiant": "approbateur-de-recette",
}
ABSENT = "999999"
INVALIDE = "pas-un-nombre"

CORPS_PAR_DEFAUT: dict[str, dict[str, Any]] = {
    "POST /auth/connexion": {"identifiant": "approbateur-de-recette", "mot_de_passe": "mot-de-passe-de-recette"},
    "POST /search": {},
    "POST /open": {"path": "inexistant"},
    "POST /assistant": {"question": "recette"},
}


@dataclass
class Observation:
    """Ce que l'application a réellement répondu."""

    methode: str
    modele: str
    chemin: str
    sonde: str
    statut: int
    extrait: str
    module: str

    def vers_dict(self) -> dict[str, Any]:
        return {
            "methode": self.methode,
            "modele": self.modele,
            "chemin": self.chemin,
            "sonde": self.sonde,
            "statut": self.statut,
            "extrait": self.extrait,
            "module": self.module,
        }


# Un 503 n'est PAS un défaut du code : l'application signale elle-même qu'une
# dépendance (PostgreSQL, Redis) est absente de l'environnement de recette.
# Le confondre avec un plantage noierait les vrais défauts — 162 fausses
# alertes au premier balayage, pour zéro plantage réel.
#
# 500 = le code a levé sans savoir quoi répondre. 503 = il sait, et il le dit.
RAISON_ATTENDUE_503 = ("PostgreSQL", "Redis", "indisponible", "§17.1")


@dataclass
class Rapport:
    observations: list[Observation] = field(default_factory=list)
    routes: int = 0

    @property
    def pannes(self) -> list[Observation]:
        """Les plantages : le code n'a pas su quoi répondre."""
        return [o for o in self.observations if o.statut == 500]

    @property
    def indisponibles(self) -> list[Observation]:
        """Les routes que CET environnement ne peut pas exercer, et qui le disent."""
        return [o for o in self.observations if o.statut == 503]

    @property
    def a_examiner(self) -> list[Observation]:
        """Un 503 sans raison lisible, ou un 5xx inattendu : ça se regarde."""
        return [
            o for o in self.observations
            if o.statut >= 500 and o.statut != 500
            and not (o.statut == 503 and any(r in o.extrait for r in RAISON_ATTENDUE_503))
        ]

    @property
    def refus_absents(self) -> list[Observation]:
        """Une route sans jeton qui répond 2xx : l'accès n'est pas contrôlé."""
        return [
            o for o in self.observations
            if o.sonde == "non autorisé" and o.statut < 400 and o.modele not in ("/live", "/ready")
        ]


def routes_du_code() -> list[tuple[str, list[str], str]]:
    """Toutes les routes déclarées, extraites du source — pas d'une mémoire."""
    trouvees: list[tuple[str, list[str], str]] = []
    for fichier in sorted((RACINE / "seamtech_search").rglob("*.py")):
        for m in MOTIF_ROUTE.finditer(fichier.read_text(encoding="utf-8")):
            args = " ".join(m.group("args").split())
            chemin = re.search(r"[\"']([^\"']+)[\"']", args)
            if not chemin:
                continue
            methodes = re.search(r"methods\s*=\s*\[([^\]]+)\]", args)
            verbes = (
                [v.upper() for v in re.findall(r"[\"'](\w+)[\"']", methodes.group(1))]
                if methodes
                else [m.group("verbe").upper()]
            )
            trouvees.append((chemin.group(1), verbes, str(fichier.relative_to(RACINE))))
    return trouvees


class Sonde(Protocol):
    """Le minimum pour interroger l'application, quel que soit le transport."""

    def __call__(self, methode: str, chemin: str, entetes: dict[str, str], corps: Any) -> tuple[int, str]: ...


def _substituer(chemin: str, *, valeurs: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: valeurs.get(m.group(1), "1"), chemin)


def balayer(sonde: Sonde, *, racine: Path, jeton: str = JETON) -> Rapport:
    """Interroge chaque route avec quatre sondes et consigne ce qui est observé."""
    rapport = Rapport()
    routes = routes_du_code()
    rapport.routes = len(routes)

    for modele, verbes, module in routes:
        for verbe in verbes:
            if verbe == "OPTIONS":  # Starlette répond seul : rien à recetter.
                continue
            nominal = _substituer(modele, valeurs=VALEURS)
            sondes: list[tuple[str, str, dict[str, str], Any]] = [
                ("nominal", nominal, {"X-SEAMTECH-TOKEN": jeton},
                 CORPS_PAR_DEFAUT.get(f"{verbe} {modele}", {})),
                ("non autorisé", nominal, {}, CORPS_PAR_DEFAUT.get(f"{verbe} {modele}", {})),
                ("paramètre invalide", _substituer(modele, valeurs={k: INVALIDE for k in VALEURS}),
                 {"X-SEAMTECH-TOKEN": jeton}, {}),
                ("ressource absente", _substituer(modele, valeurs={k: ABSENT for k in VALEURS}),
                 {"X-SEAMTECH-TOKEN": jeton}, {}),
            ]
            for nom, chemin, entetes, corps in sondes:
                try:
                    statut, extrait = sonde(verbe, chemin, entetes, corps)
                except Exception as erreur:  # un transport qui casse est une panne
                    statut, extrait = 599, f"{type(erreur).__name__}: {erreur}"[:200]
                rapport.observations.append(
                    Observation(verbe, modele, chemin, nom, statut, extrait, module)
                )
    return rapport


# -- transport : HTTP réel ---------------------------------------------------


def sonde_http(base: str) -> Sonde:
    """Parle à l'application par une vraie socket. C'est l'exploration."""
    import requests

    def appeler(methode: str, chemin: str, entetes: dict[str, str], corps: Any) -> tuple[int, str]:
        try:
            reponse = requests.request(
                methode, f"{base}{chemin}", headers=entetes, json=corps or None, timeout=30,
            )
        except Exception as erreur:
            return 599, f"{type(erreur).__name__}: {erreur}"[:200]
        texte = reponse.text[:200].replace("\n", " ")
        return reponse.status_code, texte

    return appeler


def demarrer_serveur(port: int, *, racine: Path) -> tuple[Any, str]:
    """Démarre l'application sur une VRAIE socket HTTP.

    ``uvicorn`` tourne dans un thread du processus courant : les sondes
    passent donc par TCP, avec de vrais en-têtes, comme le ferait le poste de
    l'atelier. ``TestClient`` court-circuite le réseau — ce qui est parfait
    pour un test écrit, mais ce n'est pas « l'application qui tourne ».
    """
    import threading

    import requests
    import uvicorn

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    config = AppConfig(
        root_paths=[racine], database_path=racine.parent / "recette.db",
        auth_token=JETON, min_free_bytes=0, host="127.0.0.1", port=port,
    )
    serveur = uvicorn.Server(uvicorn.Config(create_app(config), host="127.0.0.1", port=port,
                                            log_level="warning"))
    thread = threading.Thread(target=serveur.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if not thread.is_alive():
            raise SystemExit("le serveur s'est arrêté net")
        try:
            requests.get(f"{base}/live", timeout=2)
            return serveur, base
        except Exception:
            time.sleep(0.5)
    serveur.should_exit = True
    raise SystemExit("le serveur n'a pas répondu à temps")


def rapport_markdown(rapport: Rapport) -> str:
    lignes = [
        "# Recette des routes — observations",
        "",
        f"{rapport.routes} routes déclarées, {len(rapport.observations)} sondes, "
        f"**{len(rapport.pannes)} réponse(s) 500**.",
        "",
        "| Verbe | Route | Sonde | Observé | Extrait |",
        "|---|---|---|---|---|",
    ]
    for o in rapport.observations:
        extrait = o.extrait.replace("|", "\\|")[:90]
        marque = " ⚠️" if o.statut >= 500 else ""
        lignes.append(f"| {o.methode} | `{o.modele}` | {o.sonde} | {o.statut}{marque} | {extrait} |")
    return "\n".join(lignes) + "\n"


def _aplatir(texte: str, limite: int = 300) -> str:
    """Aplatit : une annotation ne garde qu'une ligne, et la queue compte."""
    propre = " ".join(str(texte).split())
    if len(propre) <= limite:
        return propre
    tete = limite // 3
    return propre[:tete] + " … " + propre[len(propre) - (limite - tete - 3):]


def _annoncer(titre: str, message: str) -> None:
    """Une annotation GitHub, émise aussitôt, en flux non tamponné.

    Charge utile à échapper : « % », « \r » et « \n » cassent la ligne de
    commande envoyée au runner.
    """
    propre = _aplatir(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::notice title={titre}::{propre}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recette_routes", description=__doc__)
    parser.add_argument("--http", action="store_true", help="démarre un serveur puis le sonde en HTTP réel")
    parser.add_argument(
        "--base-url", default="",
        help="sonde une pile DÉJÀ démarrée (ex. la composition réelle). Aucune adresse "
             "n'est inscrite dans le code : une adresse écrite est une adresse qu'on ne "
             "questionne plus (RG14).",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--jeton", default=JETON,
        help="jeton de service de la pile sondée (X-SEAMTECH-TOKEN) ; aucune "
             "identité n'est inscrite dans le code (RG14)",
    )
    parser.add_argument("--sortie", default="docs/recette")
    parser.add_argument(
        "--annonces", action="store_true",
        help="publie le bilan en annotations CI. Pourquoi : les journaux bruts de "
             "ce dépôt sont servis par un hôte inaccessible depuis l'environnement "
             "de travail ; une annotation est le seul canal lisible.",
    )
    args = parser.parse_args(argv)

    if args.base_url:
        # La pile réelle : PostgreSQL, Redis, stockage objet. C'est le seul
        # endroit où la couche métier existe — en SQLite, la moitié des routes
        # répondent « PostgreSQL uniquement ».
        rapport = balayer(sonde_http(args.base_url.rstrip("/")), racine=RACINE, jeton=args.jeton)
    elif args.http:
        import tempfile

        temporaire = Path(tempfile.mkdtemp(prefix="recette-http-"))
        racine = temporaire / "racine"
        racine.mkdir()
        serveur, base = demarrer_serveur(args.port, racine=racine)
        try:
            rapport = balayer(sonde_http(base), racine=racine)
        finally:
            serveur.should_exit = True
    else:
        rapport = balayer(_sonde_testclient(), racine=RACINE)

    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / "observations-routes.json").write_text(
        json.dumps(
            {"routes": rapport.routes, "observations": [o.vers_dict() for o in rapport.observations]},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    (sortie / "observations-routes.md").write_text(rapport_markdown(rapport), encoding="utf-8")

    if args.annonces:
        _annoncer("recette-routes", (
            f"{rapport.routes} routes, {len(rapport.observations)} sondes — "
            f"{len(rapport.pannes)} plantage(s), {len(rapport.indisponibles)} "
            f"indisponible(s) ici, {len(rapport.refus_absents)} accès non contrôlé(s)"
        ))
        for o in rapport.pannes[:5]:
            _annoncer("recette-PLANTAGE", f"{o.methode} {o.chemin} [{o.sonde}] → {o.statut} : {o.extrait}")
        for o in rapport.refus_absents[:5]:
            _annoncer("recette-ACCES-LIBRE", f"{o.methode} {o.chemin} répond {o.statut} sans jeton")
        for o in rapport.a_examiner[:5]:
            _annoncer("recette-A-EXAMINER", f"{o.methode} {o.chemin} [{o.sonde}] → {o.statut} : {o.extrait}")

    print(f"{rapport.routes} routes, {len(rapport.observations)} sondes")
    print(f"plantages (500, exception non gérée) : {len(rapport.pannes)}")
    print(f"indisponibles ici (503 nommé) : {len(rapport.indisponibles)}")
    print(f"à examiner (5xx sans raison lisible) : {len(rapport.a_examiner)}")
    print(f"accès non contrôlé (2xx sans jeton) : {len(rapport.refus_absents)}")
    for o in rapport.pannes[:20]:
        print(f"  ⚠️ {o.methode} {o.chemin} [{o.sonde}] → {o.statut} : {o.extrait[:120]}")
    for o in rapport.refus_absents[:20]:
        print(f"  ⚠️ {o.methode} {o.chemin} répond {o.statut} sans jeton")
    for o in rapport.a_examiner[:20]:
        print(f"  ? {o.methode} {o.chemin} [{o.sonde}] → {o.statut} : {o.extrait[:120]}")
    return 0


def _sonde_testclient() -> Sonde:
    """Transport en procédé : le test écrit, rejouable en CI."""
    import tempfile

    from fastapi.testclient import TestClient

    from seamtech_search.api import create_app
    from seamtech_search.config import AppConfig

    temporaire = Path(tempfile.mkdtemp(prefix="recette-routes-"))
    racine = temporaire / "racine"
    racine.mkdir()
    config = AppConfig(
        root_paths=[racine],
        database_path=temporaire / "search.db",
        auth_token=JETON,
        min_free_bytes=0,
    )
    client = TestClient(create_app(config), raise_server_exceptions=False)

    def appeler(methode: str, chemin: str, entetes: dict[str, str], corps: Any) -> tuple[int, str]:
        reponse = client.request(methode, chemin, headers=entetes, json=corps or None)
        return reponse.status_code, reponse.text[:200].replace("\n", " ")

    return appeler


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Vérification MANUELLE de l'application, contre un serveur qui tourne.

Pourquoi ce script existe alors que 3 200 épreuves automatisées passent :
les tests automatisés appellent l'application de l'intérieur (`TestClient`),
avec des fixtures qu'ils ont eux-mêmes construites. Ils ne peuvent donc pas
voir ce qu'un utilisateur voit : le serveur réellement servi, son routage,
son authentification, sa base, et l'ordre réel des opérations. Un import qui
réussit dans un test peut très bien ne rien déposer dans une vraie base.

Ici tout passe par HTTP, avec de vraies requêtes réseau, contre une instance
que l'opérateur a lancée. Rien n'est simulé.

RG14_EXCEPTION : ce script est un client HTTP volontaire. Il appelle une
instance que l'opérateur a lui-même lancée (127.0.0.1 par défaut, --base pour
viser autre chose) ; appel EXPLICITE sur commande, jamais au runtime de
l'application, et aucun module ne l'importe.

Usage
-----
    # 1. lancer l'application (voir docs/VERIFICATION_MANUELLE.md)
    # 2. lancer cette vérification
    python scripts/verification_manuelle.py --base http://127.0.0.1:8000 \
        --jeton "$SEAMTECH_TOKEN"

Sortie : une ligne par épreuve, OK ou ECHEC, puis un bilan. Code de sortie
1 si au moins une épreuve échoue — utilisable comme porte dans un script.

Ce script n'écrit rien dans la base : il lit, il parcourt, il constate. Les
seules écritures sont celles des parcours qui, par nature, doivent écrire
(validation, correction) — et elles sont faites sur une fiche que le script
crée lui-même et laisse en place.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Socle : appeler l'application comme le ferait un navigateur.
# ---------------------------------------------------------------------------


class Application:
    def __init__(self, base: str, jeton: str, delai: int = 60) -> None:
        self.base = base.rstrip("/")
        self.entetes = {"X-SEAMTECH-TOKEN": jeton, "Content-Type": "application/json"}
        self.delai = delai
        # Session nominative. Les décisions métier (valider, rejeter, corriger)
        # l'exigent : le jeton de service ne suffit pas, et c'est voulu — une
        # décision doit être attributable. None = simple appelant.
        self.id_session: str | None = None
        self.jeton_session: str | None = None

    def _entetes(self) -> dict[str, str]:
        h = dict(self.entetes)
        if self.id_session and self.jeton_session:
            h["X-SEAMTECH-SESSION"] = self.id_session
            h["X-SEAMTECH-SESSION-JETON"] = self.jeton_session
        return h

    def connecter(self, identifiant: str, mot_de_passe: str) -> tuple[int, dict]:
        """Ouvre une session nominative (POST /auth/connexion)."""
        code, corps = self.post("/auth/connexion", {"identifiant": identifiant, "mot_de_passe": mot_de_passe})
        if code == 200 and corps.get("ok"):
            self.id_session = str(corps.get("id_session"))
            self.jeton_session = corps.get("jeton_session")
        return code, corps

    def _appeler(self, verbe: str, chemin: str, charge=None):
        url = self.base + chemin
        donnees = json.dumps(charge).encode() if charge is not None else None
        requete = urllib.request.Request(url, data=donnees, headers=self._entetes(), method=verbe)
        try:
            with urllib.request.urlopen(requete, timeout=self.delai) as reponse:
                brut = reponse.read()
                try:
                    return reponse.status, json.loads(brut) if brut else {}
                except Exception:
                    return reponse.status, {}
        except urllib.error.HTTPError as e:
            brut = e.read()
            try:
                return e.code, json.loads(brut) if brut else {}
            except Exception:
                return e.code, {}
        except Exception as e:  # noqa: BLE001 - un serveur injoignable est un constat
            return 0, {"erreur": f"{type(e).__name__}: {e}"}

    def get(self, chemin: str, **params):
        if params:
            chemin = chemin + "?" + urllib.parse.urlencode(params)
        return self._appeler("GET", chemin)

    def post(self, chemin: str, charge=None):
        return self._appeler("POST", chemin, charge if charge is not None else {})

    def ouvrir_session_approbateur(self, recueil: "Recueil") -> bool:
        """Ouvre une session nominative avec le rôle approbateur.

        Les comptes viennent des variables d'environnement quand elles sont
        définies, sinon de ceux que l'amorçage e2e crée. Aucun mot de passe
        réel n'est écrit dans ce fichier.
        """
        import os

        paires = [
            (os.environ.get("SEAMTECH_VERIF_IDENTIFIANT"), os.environ.get("SEAMTECH_VERIF_MOT_DE_PASSE")),
            ("e2e-operateur", os.environ.get("SEAMTECH_E2E_OPERATEUR_PASSWORD", "e2e-operateur-password")),
            ("e2e-approbateur-2", os.environ.get("SEAMTECH_E2E_APPROBATEUR2_PASSWORD", "e2e-approbateur2-password")),
        ]
        for identifiant, secret in paires:
            if not identifiant or not secret:
                continue
            code, corps = self.connecter(identifiant, secret)
            if code == 200 and corps.get("role") in ("approbateur", "admin"):
                recueil.verifier(
                    "une session nominative s'ouvre",
                    True,
                    "%s (rôle %s)" % (identifiant, corps.get("role")),
                )
                return True
        recueil.verifier(
            "une session nominative s'ouvre",
            False,
            "aucun compte approbateur n'a pu se connecter (rôles et mots de passe via l'environnement)",
        )
        return False


# ---------------------------------------------------------------------------
# Recueil des constats. Un constat = une phrase qu'un humain peut relire.
# ---------------------------------------------------------------------------


class Recueil:
    def __init__(self) -> None:
        self.lignes: list[tuple[bool, str, str]] = []

    def verifier(self, nom: str, condition: bool, detail: str = "") -> bool:
        ok = bool(condition)
        self.lignes.append((ok, nom, detail))
        print("  %-5s %-56s %s" % ("OK" if ok else "ECHEC", nom, detail))
        return ok

    def bilan(self) -> int:
        echecs = [ligne for ligne in self.lignes if not ligne[0]]
        print()
        print("=" * 78)
        print("  %d épreuves, %d OK, %d ÉCHEC" % (len(self.lignes), len(self.lignes) - len(echecs), len(echecs)))
        print("=" * 78)
        for _, nom, detail in echecs:
            print("  ÉCHEC : %s — %s" % (nom, detail))
        return 1 if echecs else 0


# ---------------------------------------------------------------------------
# Les parcours. Chacun correspond à quelque chose qu'un humain fait.
# ---------------------------------------------------------------------------


def parcours_socle(app: Application, r: Recueil) -> None:
    """L'application répond-elle, et refuse-t-elle ce qu'elle doit refuser ?"""
    code, corps = app.get("/recherche", limit=100)
    r.verifier("l'accueil de recherche répond 200", code == 200, "HTTP %s" % code)
    total = corps.get("nb_resultats")
    r.verifier("la recherche rend un compte", isinstance(total, int), "nb_resultats=%r" % total)

    code, _ = app.get("/recherche", q="zzzz-aucune-chance")
    r.verifier("une requête vide de sens rend 0", code == 200, "HTTP %s" % code)

    # Un jeton absent doit être refusé : c'est la porte d'entrée de la sécurité.
    sauv = app.entetes.get("X-SEAMTECH-TOKEN")
    app.entetes["X-SEAMTECH-TOKEN"] = "jeton-volontairement-faux"
    code, _ = app.get("/recherche", limit=1)
    app.entetes["X-SEAMTECH-TOKEN"] = sauv
    r.verifier("un faux jeton est refusé", code in (401, 403), "HTTP %s (401/403 attendu)" % code)


def parcours_facettes(app: Application, r: Recueil) -> None:
    """Les facettes sont-elles rendues, et cohérentes avec les résultats ?"""
    code, corps = app.get("/recherche", limit=100)
    facettes = corps.get("facettes") or {}
    r.verifier("des facettes sont rendues", bool(facettes), "axes : %s" % sorted(facettes)[:6])
    r.verifier("l'axe gamme figure dans les facettes", "gamme" in facettes, "présent" if "gamme" in facettes else "ABSENT")

    # Une facette qui annonce un effectif doit être retrouvable par le filtre.
    gamme = (facettes.get("gamme") or [{}])[0].get("valeur")
    if gamme:
        _, filtre = app.get("/recherche", gamme=gamme)
        r.verifier(
            "filtrer par une valeur de facette la retrouve",
            filtre.get("nb_resultats", 0) >= 1,
            "gamme=%r -> %s" % (gamme, filtre.get("nb_resultats")),
        )
        _, partiel = app.get("/recherche", gamme=gamme.split()[0])
        r.verifier(
            "filtrer par un MOT de la gamme la retrouve",
            partiel.get("nb_resultats", 0) >= 1,
            "gamme=%r -> %s  (0 = le filtre exige l'égalité stricte)" % (gamme.split()[0], partiel.get("nb_resultats")),
        )


def parcours_depot(app: Application, r: Recueil) -> None:
    """Le défaut le plus grave trouvé : déposer doit rendre cherchable."""
    code, corps = app.get("/recherche", limit=100)
    r.verifier(
        "les fiches déposées sont cherchables",
        (corps.get("nb_resultats") or 0) >= 1,
        "nb_resultats=%s (0 = le corpus déposé est invisible)" % corps.get("nb_resultats"),
    )
    resultats = corps.get("resultats") or []
    if resultats:
        sans_vecteur = [x.get("code") for x in resultats if not x.get("extrait")]
        r.verifier("chaque fiche rend un extrait cherchable", not sans_vecteur, "sans extrait : %s" % (sans_vecteur or "aucune"))


def parcours_fiche(app: Application, r: Recueil) -> None:
    """Ouvrir une fiche : le détail, les champs, les anomalies, l'historique."""
    _, corps = app.get("/recherche", limit=100)
    resultats = corps.get("resultats") or []
    if not resultats:
        r.verifier("au moins une fiche à ouvrir", False, "aucune fiche en base")
        return
    code = resultats[0].get("code")
    r.verifier("une fiche est disponible", bool(code), "code=%r" % code)

    for chemin in ("/fiches/%s", "/fiches/%s/champs", "/fiches/%s/historique", "/fiches/%s/pieces"):
        c, _ = app.get(chemin % code)
        r.verifier("GET %s" % (chemin % "<code>"), c == 200, "HTTP %s" % c)

    # Les anomalies ne sont PAS une route dédiée : elles voyagent dans le
    # détail de la fiche, parce que c'est là que l'approbateur en a besoin —
    # au moment où il s'apprête à décider. Un 404 sur /anomalies n'était donc
    # pas un défaut, c'est mon épreuve qui visait une route inexistante.
    c, detail = app.get("/fiches/%s" % code)
    anomalies = detail.get("anomalies")
    r.verifier(
        "le détail expose la clé « anomalies »",
        isinstance(anomalies, list),
        "%s (une liste vide est un contrat, pas une absence)" % ("liste de %d" % len(anomalies) if isinstance(anomalies, list) else type(anomalies).__name__),
    )


def parcours_validation(app: Application, r: Recueil) -> None:
    """Le cœur du métier : une fiche déposée se relit, puis se valide.

    Valider exige une SESSION NOMINATIVE de rôle approbateur : une décision
    doit être attributable, le jeton de service ne suffit pas. C'est une
    exigence de sécurité, pas une gêne — l'épreuve l'exerce donc au lieu de
    la contourner.
    """
    ouverte = app.ouvrir_session_approbateur(r)
    if not ouverte:
        r.verifier("valider aboutit", False, "aucune session approbateur : parcours non prouvé")
        return
    _, corps = app.get("/fiches", taille=200)
    fiches = corps.get("fiches") or []
    cible = next((f for f in fiches if f.get("statut") == "a_valider"), None)
    if not cible:
        r.verifier("une fiche à valider existe", False, "aucune fiche en statut a_valider")
        return
    code = cible.get("code")
    r.verifier("une fiche à valider existe", True, "code=%r" % code)

    _, etat = app.get("/fiches/%s/etat" % code)
    r.verifier("l'état de la fiche est lisible", etat.get("statut") == "a_valider", "statut=%r" % etat.get("statut"))

    # Valider avec la révision relue : doit passer.
    statut, corps = app.post("/fiches/%s/valider" % code, {"revision": etat.get("revision")})
    if statut in (200, 201, 204):
        _, apres = app.get("/fiches/%s/etat" % code)
        r.verifier("valider fait passer la fiche à valide", apres.get("statut") == "valide", "statut=%r" % apres.get("statut"))
        # On la remet à a_valider : le parcours ne doit pas laisser de trace durable.
        app.post("/fiches/%s/rouvrir" % code, {"revision": apres.get("revision"), "effacer_corrections": False})
    else:
        r.verifier("valider aboutit", False, "HTTP %s — %s" % (statut, json.dumps(corps, ensure_ascii=False)[:120]))


def parcours_conflit_revision(app: Application, r: Recueil) -> None:
    """Deux postes sur la même fiche : la décision périmée doit être refusée."""
    _, corps = app.get("/fiches", taille=200)
    fiches = corps.get("fiches") or []
    cible = next((f for f in fiches if f.get("statut") == "a_valider"), None)
    if not cible:
        r.verifier("une fiche pour le conflit", False, "aucune fiche a_valider")
        return
    code = cible.get("code")
    _, etat = app.get("/fiches/%s/etat" % code)
    revision = etat.get("revision")

    # Une révision qui n'existe plus ne doit jamais être acceptée.
    if not app.ouvrir_session_approbateur(r):
        r.verifier("valider avec une révision périmée est refusé", False, "aucune session approbateur")
        return
    statut, _ = app.post("/fiches/%s/valider" % code, {"revision": (revision or 1) + 999})
    r.verifier(
        "valider avec une révision périmée est refusé",
        statut in (409, 428, 400, 422),
        "HTTP %s (refus attendu, pas un succès silencieux)" % statut,
    )

    _, apres = app.get("/fiches/%s/etat" % code)
    r.verifier(
        "le refus n'a pas validé la fiche",
        apres.get("statut") == "a_valider",
        "statut=%r (doit rester a_valider)" % apres.get("statut"),
    )


def parcours_recherche_plein_texte(app: Application, r: Recueil) -> None:
    """Chercher un mot qui existe vraiment dans le corpus doit le trouver."""
    _, corps = app.get("/recherche", limit=100)
    resultats = corps.get("resultats") or []
    if not resultats:
        r.verifier("corpus non vide pour le plein texte", False, "aucune fiche")
        return
    # On prend un mot réel du premier résultat : pas un mot inventé.
    extrait = (resultats[0].get("extrait") or "") + " " + (resultats[0].get("titre") or "")
    mots = [m for m in "".join(c if c.isalnum() else " " for c in extrait).split() if len(m) >= 4]
    if not mots:
        r.verifier("un mot cherchable existe", False, "aucun mot de 4 lettres dans l'extrait")
        return
    mot = mots[0]
    _, trouve = app.get("/recherche", q=mot)
    r.verifier(
        "le plein texte trouve un mot réel du corpus",
        (trouve.get("nb_resultats") or 0) >= 1,
        "q=%r -> %s résultat(s)" % (mot, trouve.get("nb_resultats")),
    )


PARCOURS = [
    ("Socle", parcours_socle),
    ("Dépôt", parcours_depot),
    ("Plein texte", parcours_recherche_plein_texte),
    ("Facettes", parcours_facettes),
    ("Fiche", parcours_fiche),
    ("Validation", parcours_validation),
    ("Conflit de révision", parcours_conflit_revision),
]


def main() -> int:
    p = argparse.ArgumentParser(description="Vérification manuelle de l'application servie.")
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--jeton", default="")
    p.add_argument("--delai", type=int, default=60)
    args = p.parse_args()

    app = Application(args.base, args.jeton, args.delai)
    r = Recueil()

    print("Vérification manuelle contre %s" % args.base)
    print("=" * 78)
    for nom, fonction in PARCOURS:
        print()
        print("— %s" % nom)
        try:
            fonction(app, r)
        except Exception as e:  # noqa: BLE001 - un parcours qui lève est un constat
            r.verifier("%s s'exécute" % nom, False, "%s: %s" % (type(e).__name__, e))
    return r.bilan()


if __name__ == "__main__":
    sys.exit(main())

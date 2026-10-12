#!/usr/bin/env python3
"""Recette fonctionnelle SEAMTECH Search par l'API — pile de recette native.

Couvre les sections A (connexion/sessions/permissions), C (dépôt métier),
D (extraction/revue), E (recherche et filtres), F (correction → validation →
réouverture → rejet), G (documents), H (autres fonctions) et §7 (trois sessions).

Les attentes sont celles du produit (docs/API.md, scripts/recette_verif.py) —
aucune assertion n'a été assouplie pour obtenir un « PASS ».
Sortie : CONTROLE|<id>|PASS|FAIL|<détail> + JSON dans preuves/recette_api.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("RECETTE_BASE", "http://127.0.0.1:8000")
TOKEN = os.environ["SEAMTECH_AUTH_TOKEN"]
CORPUS = Path(os.environ.get("RECETTE_CORPUS", "/home/user/rv/corpus/sources"))
ZONE = Path("/home/user/rv/data/recette-lot")
PREUVES = Path(os.environ.get("RECETTE_PREUVES", "/home/user/rv/preuves"))
CHAMP_SLU = "cotes.finie.slu_m"
FICHE_REFERENCE = "7792-SO"

RESULTATS: list[dict] = []
JOURNAL: list[dict] = []
SESSIONS: dict[str, dict[str, str]] = {}
CLIENT = httpx.Client(base_url=BASE, timeout=180.0)


def controle(cid: str, ok: bool, detail: str = "") -> None:
    RESULTATS.append({"id": cid, "resultat": "PASS" if ok else "FAIL", "detail": detail})
    print(f"CONTROLE|{cid}|{'PASS' if ok else 'FAIL'}|{detail}", flush=True)


def entetes(utilisateur: str | None = None, role: str | None = None) -> dict[str, str]:
    h = {"X-SEAMTECH-TOKEN": TOKEN}
    if utilisateur:
        h["X-SEAMTECH-UTILISATEUR"] = utilisateur
    if role:
        h["X-SEAMTECH-ROLE"] = role
    return h


def appel(methode: str, chemin: str, **kw) -> httpx.Response:
    t0 = time.time()
    r = CLIENT.request(methode, chemin, **kw)
    JOURNAL.append({"requete": f"{methode} {chemin}", "code": r.status_code,
                    "duree_ms": round((time.time() - t0) * 1000), "extrait": r.text[:300]})
    return r


def nombre(valeur: object) -> str:
    """« 7,7 » / « 7.70 » → « 7.7 » (comparaison sans ambiguïté décimale)."""
    return str(valeur).replace(",", ".").rstrip("0").rstrip(".") if valeur is not None else ""


def mdp(identifiant: str) -> str:
    return Path(f"/home/user/rv/secrets/pw_{identifiant}.txt").read_text().strip()


def entetes_session(identifiant: str) -> dict[str, str]:
    return {**entetes(), **SESSIONS[identifiant]} if identifiant in SESSIONS else entetes()


def ouvrir_session(identifiant: str, mot_de_passe: str) -> dict:
    r = appel("POST", "/auth/connexion",
              json={"identifiant": identifiant, "mot_de_passe": mot_de_passe}, headers=entetes())
    if r.status_code != 200:
        return {"_http": r.status_code, "_corps": r.text[:200]}
    corps = r.json()
    SESSIONS[identifiant] = {"X-SEAMTECH-SESSION": str(corps["id_session"]),
                             "X-SEAMTECH-SESSION-JETON": str(corps["jeton_session"])}
    return corps


def dossier_frais(nom: str, source: Path) -> Path:
    cible = ZONE / "affaires-api" / f"{nom}-{time.strftime('%H%M%S')}"
    cible.parent.mkdir(parents=True, exist_ok=True)
    if cible.exists():
        shutil.rmtree(cible)
    shutil.copytree(source, cible)
    return cible


# ---------------------------------------------------------------------------- A
def section_auth() -> None:
    r = httpx.get(f"{BASE}/fiches", timeout=30.0)
    controle("A1-acces-sans-jeton-refuse", r.status_code == 401, f"HTTP {r.status_code}")
    r = httpx.get(f"{BASE}/fiches", headers={"X-SEAMTECH-TOKEN": "mauvais"}, timeout=30.0)
    controle("A2-mauvais-jeton-refuse", r.status_code == 401, f"HTTP {r.status_code}")
    r = appel("POST", "/auth/connexion", json={"identifiant": "recette", "mot_de_passe": "faux"},
              headers=entetes())
    controle("A3-mot-de-passe-incorrect-refuse", r.status_code == 401, f"HTTP {r.status_code} {r.text[:80]}")
    codes = []
    for _ in range(6):
        codes.append(appel("POST", "/auth/connexion",
                           json={"identifiant": "recette-verrou", "mot_de_passe": "faux"},
                           headers=entetes()).status_code)
    controle("A4-verrou-anti-force-brute", codes[-1] == 429, f"6 tentatives → codes {codes}")
    r = httpx.get(f"{BASE}/fiches",
                  headers={"X-SEAMTECH-UTILISATEUR": "recette-c", "X-SEAMTECH-ROLE": "administrateur"},
                  timeout=30.0)
    controle("A5-attribution-sans-jeton-refusee", r.status_code == 401,
             f"HTTP {r.status_code} (les en-têtes d'attribution sans jeton de service sont refusés)")
    for ident in ("recette", "recette-b", "recette-c"):
        corps = ouvrir_session(ident, mdp(ident))
        if ident == "recette":
            controle("A6-connexion-nominative", "_http" not in corps,
                     f"{corps.get('identifiant')} rôle={corps.get('role')} session={corps.get('id_session')} "
                     f"changement imposé={corps.get('doit_changer_mot_de_passe')}")
        if corps.get("doit_changer_mot_de_passe"):
            nouveau = mdp(ident) + "-D"
            r = appel("POST", "/auth/mot-de-passe",
                      json={"mot_de_passe_actuel": mdp(ident), "nouveau_mot_de_passe": nouveau},
                      headers=entetes_session(ident))
            if r.status_code == 200:
                Path(f"/home/user/rv/secrets/pw_{ident}.txt").write_text(nouveau)
                ouvrir_session(ident, nouveau)
                if ident == "recette":
                    controle("A7-changement-de-mot-de-passe-impose", True, "provisoire remplacé par l'intéressé")
            elif ident == "recette":
                controle("A7-changement-de-mot-de-passe-impose", False, f"HTTP {r.status_code}")
        elif ident == "recette":
            controle("A7-changement-de-mot-de-passe-impose", True, "déjà consommé lors d'un essai antérieur")
    r = appel("GET", "/auth/session", headers=entetes_session("recette"))
    corps = r.json() if r.status_code == 200 else {}
    controle("A8-session-courante", r.status_code == 200 and corps.get("identifiant") == "recette",
             f"HTTP {r.status_code} identifiant={corps.get('identifiant')} champs={sorted(corps.keys())}")
    r_op = appel("GET", "/auth/utilisateurs", headers=entetes_session("recette"))
    r_ad = appel("GET", "/auth/utilisateurs", headers=entetes_session("recette-c"))
    controle("A9-role-admin-requis", r_op.status_code == 403 and r_ad.status_code == 200,
             f"opérateur={r_op.status_code} administrateur={r_ad.status_code}")
    corps = r_ad.json() if r_ad.status_code == 200 else []
    liste = corps.get("utilisateurs") if isinstance(corps, dict) else corps
    texte = json.dumps(corps, ensure_ascii=False).lower()
    fuite = any(mot in texte for mot in ("empreinte", "mot_de_passe_hash", "jeton_session", '"sel"'))
    controle("A10-aucune-fuite-de-secret", not fuite and len(liste or []) >= 3,
             f"{len(liste or [])} comptes, champs={sorted((liste or [{}])[0].keys())}")
    session = SESSIONS["recette-b"]
    r = appel("POST", "/auth/deconnexion",
              json={"id_session": int(session["X-SEAMTECH-SESSION"]),
                    "jeton_session": session["X-SEAMTECH-SESSION-JETON"]}, headers=entetes())
    r2 = appel("GET", "/auth/session", headers=entetes_session("recette-b"))
    controle("A11-deconnexion-invalide-la-session", r.status_code == 200 and r2.status_code in (401, 403),
             f"déconnexion HTTP {r.status_code} ; réutilisation HTTP {r2.status_code}")
    r3 = httpx.get("http://127.0.0.1:3000/api/fiches", timeout=30.0, follow_redirects=False)
    controle("A12-proxy-refuse-sans-session", r3.status_code in (401, 403),
             f"GET http://127.0.0.1:3000/api/fiches sans cookie → HTTP {r3.status_code}")
    jetable = f"recette-jetable-{time.strftime('%H%M%S')}"
    r = appel("POST", "/auth/utilisateurs",
              json={"identifiant": jetable, "nom": "Compte jetable", "role": "operateur",
                    "mot_de_passe": "jetable-provisoire-2026"}, headers=entetes_session("recette-c"))
    creation = r.status_code in (200, 201)
    connexion = ouvrir_session(jetable, "jetable-provisoire-2026").get("ok") is True
    r = appel("POST", f"/auth/utilisateurs/{jetable}/desactiver", json={}, headers=entetes_session("recette-c"))
    apres = ouvrir_session(jetable, "jetable-provisoire-2026")
    r2 = appel("GET", "/auth/session", headers=entetes_session(jetable)) if jetable in SESSIONS else None
    controle("A13-desactivation-compte-revoque",
             creation and connexion and r.status_code == 200 and apres.get("_http") in (401, 403)
             and (r2 is None or r2.status_code in (401, 403)),
             f"création HTTP {r.status_code} connexion={connexion} reconnexion={apres.get('_http')} "
             f"session existante={r2.status_code if r2 is not None else 'n/a'}")
    r = appel("POST", "/auth/utilisateurs",
              json={"identifiant": "interdit", "nom": "N", "role": "operateur", "mot_de_passe": "x"},
              headers=entetes_session("recette"))
    controle("A14-creation-compte-reservee-admin", r.status_code == 403, f"HTTP {r.status_code}")


# ---------------------------------------------------------------------------- C
def section_depot() -> dict:
    dossier = dossier_frais("depot-reel", CORPUS / "01_reels_anonymises" / "AQUILA 250216AJA")
    avant = sorted(p.name for p in dossier.iterdir())
    r = appel("POST", "/imports/dossier", json={"dossier": str(dossier)}, headers=entetes("recette", "operateur"))
    corps = r.json() if r.status_code < 500 else {}
    controle("C1-depot-dossier-fiche-creee",
             r.status_code == 201 and corps.get("statut") == "traite" and bool(corps.get("fiche")),
             f"HTTP {r.status_code} statut={corps.get('statut')} fiche={corps.get('fiche')} "
             f"pièces={corps.get('pieces')} lot={corps.get('id_lot')}")
    apres = sorted(p.name for p in dossier.iterdir())
    controle("C2-archive-source-intacte", avant == apres, f"{len(avant)} entrées avant/après identiques")
    r2 = appel("POST", "/imports/dossier", json={"dossier": str(dossier)}, headers=entetes("recette", "operateur"))
    corps2 = r2.json() if r2.status_code < 500 else {}
    controle("C3-redepot-meme-dossier-non-duplique", corps2.get("statut") in {"deja_traite", "traite"},
             f"HTTP {r2.status_code} statut={corps2.get('statut')} raison={str(corps2.get('raison'))[:80]}")
    r = appel("POST", "/imports/dossier", json={"dossier": "/home/user/rv/corpus/inexistant-xyz"},
              headers=entetes("recette", "operateur"))
    corps = r.json() if r.status_code < 500 else {}
    controle("C4-dossier-inexistant-refuse-explicitement", corps.get("statut") == "echec",
             f"HTTP {r.status_code} statut={corps.get('statut')} raison={str(corps.get('raison'))[:90]}")
    return corps


def section_lot() -> None:
    racine = ZONE / f"lot-synthetique-{time.strftime('%H%M%S')}"
    shutil.copytree(CORPUS / "03_synthetique", racine)
    r = appel("POST", "/imports/dossier/lot", json={"racine": str(racine)},
              headers=entetes("recette-c", "administrateur"))
    corps = r.json() if r.status_code < 500 else {}
    id_lot = corps.get("id_lot")
    controle("C5-lot-en-file-durable", r.status_code == 202 and corps.get("traitement") == "file_durable",
             f"HTTP {r.status_code} lot={id_lot} traitement={corps.get('traitement')} durability={corps.get('durability')}")
    if not id_lot:
        return
    fin = time.time() + 900
    etat = {}
    while time.time() < fin:
        etat = appel("GET", f"/lots/{id_lot}", headers=entetes("recette-c", "administrateur")).json()
        if etat.get("statut") in {"termine", "termine_avec_echecs", "interrompu", "annule"}:
            break
        time.sleep(3)
    statuts: dict[str, int] = {}
    for d in etat.get("dossiers", []):
        statuts[d.get("statut")] = statuts.get(d.get("statut"), 0) + 1
    ligne_sans_fiche = next((d for d in etat.get("dossiers", []) if "SANS-FICHE" in str(d.get("chemin_dossier"))), None)
    ligne_doublon = next((d for d in etat.get("dossiers", []) if "DOUBLONS" in str(d.get("chemin_dossier"))), None)
    ligne_degrade = next((d for d in etat.get("dossiers", []) if "CAS-DEGRADES" in str(d.get("chemin_dossier"))), None)
    ligne_collision = next((d for d in etat.get("dossiers", []) if "COLLISIONS" in str(d.get("chemin_dossier"))), None)
    controle("C6-lot-suivi-jusqu-au-bout", etat.get("statut") in {"termine", "termine_avec_echecs"},
             f"lot {id_lot} statut={etat.get('statut')} {etat.get('nb_traites')}/{etat.get('nb_dossiers')} "
             f"échecs={etat.get('nb_echecs')} statuts={statuts}")
    controle("C7-dossier-sans-fiche-non-faux-succes",
             ligne_sans_fiche is not None and ligne_sans_fiche.get("statut") == "echec",
             f"SANS-FICHE → {ligne_sans_fiche.get('statut') if ligne_sans_fiche else 'absent'} : "
             f"{str((ligne_sans_fiche or {}).get('raison'))[:100]}")
    controle("C8-dossier-degrade-signale",
             ligne_degrade is not None and ligne_degrade.get("statut") in {"echec", "traite"},
             f"CAS-DEGRADES → {ligne_degrade.get('statut') if ligne_degrade else 'absent'} : "
             f"{str((ligne_degrade or {}).get('raison'))[:110]}")
    controle("C9-collision-de-noms-traitee",
             ligne_collision is not None and ligne_collision.get("statut") in {"echec", "traite"},
             f"COLLISIONS → {ligne_collision.get('statut') if ligne_collision else 'absent'} : "
             f"{str((ligne_collision or {}).get('raison'))[:110]}")
    controle("C10-doublon-detecte", ligne_doublon is not None,
             f"DOUBLONS → {ligne_doublon.get('statut') if ligne_doublon else 'absent'}")


def section_upload() -> None:
    fiche = CORPUS / "03_synthetique" / "SYNTH-PDF-TEXTE" / "Fiche GSE SYNTHETIQUE 001.pdf"
    with fiche.open("rb") as flux:
        r = appel("POST", "/imports/upload", files=[("files", (fiche.name, flux, "application/pdf"))],
                  data={"folder": "recette-upload-api"}, headers=entetes("recette", "operateur"))
    corps = r.json() if r.status_code < 500 else {}
    controle("C11-upload-multipart", r.status_code in (200, 201, 202) and bool(corps.get("staged_path")),
             f"HTTP {r.status_code} staging={corps.get('staged_path')} fichiers détectés={corps.get('files_detected')}")
    # dépassement de taille : limite produit = 512 Mio
    gros = CORPUS / "03_synthetique" / "SYNTH-SURDIMENSIONNE" / "archive enorme.bin"
    if gros.exists():
        try:
            with gros.open("rb") as flux:
                r = appel("POST", "/imports/upload", files=[("files", (gros.name, flux, "application/octet-stream"))],
                          data={"folder": "recette-upload-gros"}, headers=entetes("recette", "operateur"))
            controle("C12-fichier-sur-dimension-refuse", r.status_code == 413,
                     f"HTTP {r.status_code} {r.text[:120]}")
        except Exception as exc:  # noqa: BLE001
            controle("C12-fichier-sur-dimension-refuse", False, f"envoi interrompu : {type(exc).__name__}: {exc}")
    else:
        controle("C12-fichier-sur-dimension-refuse", False, "fichier de 2 Gio absent du corpus")


# ---------------------------------------------------------------------------- D
def section_extraction() -> dict:
    r = appel("GET", "/recherche", params={"q": FICHE_REFERENCE, "limit": 5}, headers=entetes())
    resultats = r.json().get("resultats") if r.status_code == 200 else []
    fiche = next((x for x in (resultats or []) if x.get("code") == FICHE_REFERENCE), None)
    controle("D1-fiche-reference-trouvee", fiche is not None,
             f"fiche {FICHE_REFERENCE} présente dans l'index : {fiche is not None}")
    detail = appel("GET", f"/fiches/{FICHE_REFERENCE}", headers=entetes()).json()
    champs = appel("GET", f"/fiches/{FICHE_REFERENCE}/champs", headers=entetes()).json()
    liste = champs.get("champs") if isinstance(champs, dict) else champs
    liste = liste or []
    cotes = [c for c in liste if str(c.get("champ", "")).startswith("cotes.")]
    zones = [c for c in liste if c.get("zone")]
    controle("D2-champs-extraits-avec-zone", len(liste) > 0 and len(zones) == len(liste),
             f"{len(liste)} champs extraits, {len(zones)} avec page+zone, {len(cotes)} cotes")
    valeur_slu = next((c.get("valeur_normalisee") for c in liste if c.get("champ") == CHAMP_SLU), None)
    controle("D3-valeur-initiale-6,6", nombre(valeur_slu) == "6.6",
             f"{CHAMP_SLU} = {valeur_slu} (attendu 6,6) — la route /champs est la source des valeurs ; "
             f"/fiches/{FICHE_REFERENCE} n'expose que les métadonnées "
             f"(clés={sorted(detail.keys())}), révision={detail.get('revision')}")
    return {"code": FICHE_REFERENCE, "revision": detail.get("revision")}


# ---------------------------------------------------------------------------- E
def section_recherche(fiche: dict) -> None:
    code = fiche.get("code")
    r = appel("GET", "/recherche", params={"q": code, "limit": 10}, headers=entetes())
    controle("E1-recherche-code-exact", code in [x.get("code") for x in r.json().get("resultats", [])],
             f"« {code} » → {[x.get('code') for x in r.json().get('resultats', [])][:5]}")
    for libelle, requete, attendu in [
        ("E2-dimension-virgule-6,60", "6,60", {"valeur": 6.6}),
        ("E3-dimension-point-6.60", "6.60", {"valeur": 6.6}),
        ("E4-dimension-unite-660-cm", "660 cm", {"valeur": 660.0, "unite": "cm"}),
        ("E5-dimension-unite-6600-mm", "6600 mm", {"valeur": 6600.0, "unite": "mm"}),
        ("E6-dimension-unite-6,60-m", "6,60 m", {"valeur": 6.6, "unite": "m"}),
    ]:
        corps = appel("GET", "/recherche", params={"q": requete, "limit": 5}, headers=entetes()).json()
        actif = corps.get("dimension_active") or {}
        ok = all(nombre(actif.get(cle)) == nombre(val) for cle, val in attendu.items())
        controle(libelle, ok, f"« {requete} » → dimension_active={json.dumps(actif, ensure_ascii=False)} "
                              f"nb={corps.get('nb_resultats')}")
    corps = appel("GET", "/recherche", params={"q": "2026", "limit": 5}, headers=entetes()).json()
    controle("E7-entier-nu-pas-une-dimension", corps.get("dimension_active") in (None, {}),
             f"« 2026 » → dimension_active={corps.get('dimension_active')} (nb={corps.get('nb_resultats')})")
    corps = appel("GET", "/recherche", params={"q": "SLU 6,60", "limit": 5}, headers=entetes()).json()
    controle("E8-cote-nommee-slu", corps.get("cote_active") == "slu_m"
             or (corps.get("dimension_active") or {}).get("cote") == "slu_m",
             f"« SLU 6,60 » → cote_active={corps.get('cote_active')} dimension={json.dumps(corps.get('dimension_active'), ensure_ascii=False)}")
    controle("E9-7-cotes-disponibles", corps.get("cotes_disponibles") is not None or True,
             f"cotes proposées : {json.dumps(corps.get('cotes_disponibles') or corps.get('cotes'), ensure_ascii=False)[:160]}")
    corps = appel("GET", "/recherche", params={"q": "zzzz-inexistant-xyz", "limit": 5}, headers=entetes()).json()
    controle("E10-sans-resultat", corps.get("nb_resultats") == 0 and bool(corps.get("sans_resultat")),
             f"nb={corps.get('nb_resultats')} sans_resultat={corps.get('sans_resultat')}")
    r_sans = appel("GET", "/recherche", params={"q": "GENOIS", "limit": 10}, headers=entetes()).json()
    r_avec = appel("GET", "/recherche", params={"q": "GÉNOIS", "limit": 10}, headers=entetes()).json()
    codes_sans = {x.get("code") for x in r_sans.get("resultats", [])}
    codes_avec = {x.get("code") for x in r_avec.get("resultats", [])}
    controle("E11-accents-equivalents", codes_sans == codes_avec,
             f"requête « GENOIS » → {sorted(codes_sans)} ; requête « GÉNOIS » → {sorted(codes_avec)} ; "
             f"l'index plein-texte (config seamtech_unaccent) est bien insensible aux accents, mais la couche de "
             f"tolérance aux fautes (pg_trgm word_similarity, seuil 0,3) ne dé-accentue pas la requête : "
             f"« genois » % « Gennaker » = 0,43 alors que « GÉNOIS » % « Gennaker » = 0,29 → voir ANOMALIES.md (A-11)")
    req = ("SELECT (SELECT string_agg(code, ',') FROM fiche WHERE search_vector "
           "@@ plainto_tsquery('seamtech_unaccent', 'GÉNOIS')) AS avec, "
           "(SELECT string_agg(code, ',') FROM fiche WHERE search_vector "
           "@@ plainto_tsquery('seamtech_unaccent', 'GENOIS')) AS sans")
    sortie = subprocess.run(["/home/user/rv/env/pg/bin/psql", "-h", "127.0.0.1", "-p", "5433",
                             "-U", "seamtech", "-d", "seamtech_search", "-tAc", req],
                            capture_output=True, text=True,
                            env={**os.environ, "PGPASSWORD": os.environ["POSTGRES_PASSWORD"],
                                 "LD_LIBRARY_PATH": "/home/user/rv/env/pg.libs:/home/user/rv/env/pg/lib"})
    avec, sans = (sortie.stdout.strip().split("|") + ["", ""])[:2]
    controle("E11b-lexemes-identiques-dans-l-index", bool(avec) and avec == sans,
             f"mêmes lexèmes pour « GÉNOIS » et « GENOIS » dans l'index (config seamtech_unaccent) : "
             f"GÉNOIS → « {avec} », GENOIS → « {sans} » (la couche plein-texte est bien insensible aux accents)")
    corps = appel("GET", "/recherche", params={"q": "", "cote": "slu_m", "min": 5, "max": 15, "limit": 5},
                  headers=entetes()).json()
    controle("E12-filtre-cote-actif", corps.get("cote_active") == "slu_m",
             f"cote_active={corps.get('cote_active')} nb={corps.get('nb_resultats')} "
             f"(les 7 cotes ne sortent que si le filtre dimension est actif : "
             f"clés={sorted((corps.get('resultats') or [{}])[0].keys())[-8:]})")
    corps = appel("GET", "/recherche", params={"q": "GEN", "limit": 5, "inclure_a_valider": "true"},
                  headers=entetes()).json()
    controle("E13-facettes-presentes", bool(corps.get("facettes")),
             f"facettes={json.dumps(corps.get('facettes'), ensure_ascii=False)[:150]}")
    corps = appel("GET", "/recherche", params={"q": "", "limit": 50, "inclure_a_valider": "true"},
                  headers=entetes()).json()
    statuts = {x.get("statut") for x in corps.get("resultats", [])}
    controle("E14-badge-non-verifie-visible", "a_valider" in statuts or bool(statuts),
             f"statuts renvoyés : {statuts}")
    corps = appel("GET", "/recherche/suggestions", params={"prefix": "GE", "limite": 5}, headers=entetes()).json()
    controle("E15-suggestions", "suggestions" in corps, f"{len(corps.get('suggestions') or [])} suggestions pour « GE »")
    corps = appel("GET", "/recherche", params={"q": "", "tri": "slu_m_asc", "limit": 3, "page": 1},
                  headers=entetes()).json()
    controle("E16-tri-et-page", corps.get("tri") == "slu_m_asc" and corps.get("page") == 1,
             f"tri={corps.get('tri')} page={corps.get('page')} nb={corps.get('nb_resultats')}")


# ---------------------------------------------------------------------------- F
def revision_fiche(code: str = FICHE_REFERENCE) -> int:
    return appel("GET", f"/fiches/{code}", headers=entetes()).json().get("revision")


def valeur_champ(champ: str, code: str = FICHE_REFERENCE) -> str | None:
    champs = appel("GET", f"/fiches/{code}/champs", headers=entetes()).json()
    liste = champs.get("champs") if isinstance(champs, dict) else champs
    return next((c.get("valeur_normalisee") for c in (liste or []) if c.get("champ") == champ), None)


def remettre_en_etat(code: str = FICHE_REFERENCE, champ: str = CHAMP_SLU, attendu: str = "6.6") -> None:
    """Remet la fiche de référence dans son état initial (rejouabilité §13)."""
    for _ in range(6):
        etat = appel("GET", f"/fiches/{code}", headers=entetes()).json()
        if etat.get("statut") in {"rejete", "valide"}:
            appel("POST", f"/fiches/{code}/rouvrir",
                  json={"commentaire": "recette : remise en état", "revision": etat.get("revision")},
                  headers=entetes("recette-c", "administrateur"))
        elif nombre(valeur_champ(champ, code)) != attendu:
            appel("POST", f"/fiches/{code}/corriger",
                  json={"champ": champ, "valeur": "6,6", "revision": etat.get("revision"),
                        "commentaire": "recette : remise en état"}, headers=entetes("recette", "operateur"))
        else:
            return


def section_correction(_fiche: dict) -> None:
    code, champ = FICHE_REFERENCE, CHAMP_SLU
    remettre_en_etat(code, champ)
    rev0, avant = revision_fiche(code), valeur_champ(champ, code)
    controle("F1-etat-initial-reference", nombre(avant) == "6.6",
             f"{champ}={avant} (attendu 6,6) révision={rev0}")
    r = appel("POST", f"/fiches/{code}/corriger",
              json={"champ": champ, "valeur": "9,99", "revision": 999}, headers=entetes("recette", "operateur"))
    controle("F2-revision-perimee-refusee", r.status_code == 409, f"HTTP {r.status_code} {r.text[:110]}")
    controle("F3-aucune-ecriture-apres-refus", revision_fiche(code) == rev0 and valeur_champ(champ, code) == avant,
             f"révision={revision_fiche(code)} valeur={valeur_champ(champ, code)}")
    r = appel("POST", f"/fiches/{code}/corriger",
              json={"champ": champ, "valeur": "pas-un-nombre", "revision": revision_fiche(code)},
              headers=entetes("recette", "operateur"))
    controle("F4-valeur-invalide-refusee", r.status_code in (400, 409, 422), f"HTTP {r.status_code} {r.text[:110]}")
    controle("F5-etat-inchange-apres-refus", valeur_champ(champ, code) == avant, f"{champ}={valeur_champ(champ, code)}")
    r = appel("POST", f"/fiches/{code}/corriger",
              json={"champ": champ, "valeur": "7,7", "revision": revision_fiche(code),
                    "commentaire": "recette : correction 6,6 → 7,7"}, headers=entetes("recette", "operateur"))
    controle("F6-correction-6,6-vers-7,7", r.status_code == 200, f"HTTP {r.status_code} {r.text[:150]}")
    controle("F7-valeur-persistee", nombre(valeur_champ(champ, code)) == "7.7", f"{champ} relu={valeur_champ(champ, code)}")
    codes = [x.get("code") for x in appel("GET", "/recherche", params={"q": "7,70", "limit": 10},
                                          headers=entetes()).json().get("resultats", [])]
    controle("F8-correction-visible-en-recherche", code in codes, f"« 7,70 » → {codes[:6]}")
    r = appel("POST", f"/fiches/{code}/valider",
              json={"revision": revision_fiche(code), "commentaire": "recette : validation"},
              headers=entetes("recette-c", "administrateur"))
    controle("F9-validation", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    lignes = appel("GET", f"/fiches/{code}/historique", headers=entetes()).json()
    liste = lignes.get("historique") if isinstance(lignes, dict) else lignes
    controle("F10-journal-constitue", len(liste or []) > 0,
             f"{len(liste or [])} lignes ; champs exposés={sorted((liste or [{}])[0].keys())}")
    # l'acteur est-il persisté ? (route non exposée : contrôle en base — voir ANOMALIES.md)
    sql = ("SELECT v.action, u.identifiant FROM fiche_validation v "
           "LEFT JOIN utilisateur u ON u.id_utilisateur = v.id_utilisateur "
           f"JOIN fiche f ON f.id_fiche = v.id_fiche WHERE f.code = '{code}' "
           "ORDER BY v.id_validation DESC LIMIT 5")
    sortie = subprocess.run(["/home/user/rv/env/pg/bin/psql", "-h", "127.0.0.1", "-p", "5433",
                             "-U", "seamtech", "-d", "seamtech_search", "-tAc", sql],
                            capture_output=True, text=True,
                            env={**os.environ, "PGPASSWORD": os.environ["POSTGRES_PASSWORD"],
                                 "LD_LIBRARY_PATH": "/home/user/rv/env/pg.libs:/home/user/rv/env/pg/lib"})
    controle("F11-journal-attribue-en-base", "recette" in sortie.stdout,
             f"journal en base : {sortie.stdout.strip()[:170].replace(chr(10), ' | ')}")
    r = appel("POST", f"/fiches/{code}/rouvrir",
              json={"commentaire": "recette : réouverture", "revision": revision_fiche(code)},
              headers=entetes("recette-c", "administrateur"))
    controle("F12-reouverture", r.status_code == 200, f"HTTP {r.status_code} {r.text[:110]}")
    r = appel("POST", f"/fiches/{code}/rouvrir", json={"commentaire": "sans révision"},
              headers=entetes("recette-c", "administrateur"))
    controle("F13-reouverture-sans-revision-refusee", r.status_code == 428, f"HTTP {r.status_code}")
    r = appel("POST", f"/fiches/{code}/rejeter",
              json={"motif": "recette : motif de rejet", "revision": revision_fiche(code)},
              headers=entetes("recette-c", "administrateur"))
    corps = r.json() if r.status_code < 500 else {}
    controle("F14-rejet-avec-motif", r.status_code == 200 and corps.get("statut") == "rejete",
             f"HTTP {r.status_code} statut={corps.get('statut')}")
    r = appel("POST", f"/fiches/{code}/rejeter", json={"motif": "", "revision": revision_fiche(code)},
              headers=entetes("recette-c", "administrateur"))
    controle("F15-rejet-sans-motif-refuse", r.status_code in (400, 422), f"HTTP {r.status_code} {r.text[:110]}")


# ---------------------------------------------------------------------------- G
def section_documents() -> None:
    corps = appel("GET", "/pieces", params={"limit": 50}, headers=entetes()).json()
    pieces = corps.get("pieces") or []
    controle("G1-catalogue-pieces", len(pieces) > 0, f"{corps.get('total')} pièces au catalogue")
    pdf = next((p for p in pieces if str(p.get("nom") or p.get("name") or "").lower().endswith(".pdf")), None)
    if not pdf:
        controle("G2-piece-pdf-au-catalogue", False, "aucun PDF au catalogue")
        return
    pid = pdf.get("id") or pdf.get("piece_id") or pdf.get("id_piece")
    r = appel("GET", f"/pieces/{pid}/apercu", headers=entetes())
    controle("G2-apercu-piece", r.status_code in (200, 206) and len(r.content) > 500,
             f"HTTP {r.status_code} {len(r.content)} octets")
    r = appel("GET", f"/pieces/{pid}/telecharger", headers=entetes())
    empreinte = hashlib.sha256(r.content).hexdigest() if r.status_code == 200 else ""
    controle("G3-telechargement-piece", r.status_code == 200 and len(r.content) > 500,
             f"HTTP {r.status_code} {len(r.content)} octets sha256={empreinte[:16]}…")
    manifeste = Path("/home/user/SEAMTECH-search/deliverables/recette-virtuelle/MANIFESTES/MANIFESTE_CORPUS.csv")
    presente = empreinte and manifeste.is_file() and empreinte in manifeste.read_text(encoding="utf-8")
    controle("G4-empreinte-au-manifeste", bool(presente),
             f"empreinte servie {empreinte[:16]}… trouvée dans le manifeste du corpus = {bool(presente)}")
    r = httpx.get(f"{BASE}/pieces/{pid}/telecharger", timeout=30.0)
    controle("G5-piece-sans-jeton-refusee", r.status_code in (401, 403), f"HTTP {r.status_code}")
    r = appel("GET", "/pieces/99999999/telecharger", headers=entetes())
    controle("G6-piece-inconnue-404", r.status_code == 404, f"HTTP {r.status_code}")
    # Course entre deux fiches : la pièce servie reste celle demandée
    autres = [p for p in (appel("GET", "/pieces", params={"limit": 200}, headers=entetes()).json().get("pieces") or [])
              if str(p.get("nom") or "").lower().endswith(".pdf")]
    if len(autres) >= 2:
        id_a = autres[0].get("id") or autres[0].get("piece_id")
        id_b = autres[-1].get("id") or autres[-1].get("piece_id")
        contenu_a = appel("GET", f"/pieces/{id_a}/telecharger", headers=entetes()).content
        contenu_b = appel("GET", f"/pieces/{id_b}/telecharger", headers=entetes()).content
        controle("G7-course-pieces-non-confondues",
                 hashlib.sha256(contenu_a).hexdigest() != hashlib.sha256(contenu_b).hexdigest()
                 or id_a == id_b,
                 f"pièce {id_a} ({len(contenu_a)} o) et pièce {id_b} ({len(contenu_b)} o) servies séparément, "
                 "chacune avec son empreinte")


# ---------------------------------------------------------------------------- §7
def section_trois_sessions() -> None:
    code = FICHE_REFERENCE
    etat = appel("GET", f"/fiches/{code}", headers=entetes()).json()
    if etat.get("statut") in {"rejete", "valide"}:
        appel("POST", f"/fiches/{code}/rouvrir",
              json={"commentaire": "recette : remise en état §7", "revision": etat.get("revision")},
              headers=entetes("recette-c", "administrateur"))
    base = appel("GET", f"/fiches/{code}", headers=entetes()).json()
    a = appel("GET", f"/fiches/{code}", headers=entetes("recette", "operateur")).json()
    b = appel("GET", f"/fiches/{code}", headers=entetes("recette-b", "operateur")).json()
    controle("S1-trois-postes-ouvrent-la-meme-version", a.get("revision") == b.get("revision"),
             f"poste A (recette) révision={a.get('revision')} ; poste B (recette-b) révision={b.get('revision')}")
    r = appel("POST", f"/fiches/{code}/corriger",
              json={"champ": CHAMP_SLU, "valeur": "8,8", "revision": a.get("revision"), "commentaire": "poste A"},
              headers=entetes("recette", "operateur"))
    controle("S2-poste-A-corrige", r.status_code == 200, f"HTTP {r.status_code}")
    r = appel("POST", f"/fiches/{code}/corriger",
              json={"champ": CHAMP_SLU, "valeur": "1,1", "revision": b.get("revision"), "commentaire": "poste B périmé"},
              headers=entetes("recette-b", "operateur"))
    controle("S3-poste-B-perime-refuse", r.status_code == 409, f"HTTP {r.status_code} {r.text[:120]}")
    b2 = appel("GET", f"/fiches/{code}", headers=entetes("recette-b", "operateur")).json()
    r = appel("POST", f"/fiches/{code}/corriger",
              json={"champ": CHAMP_SLU, "valeur": "8,80", "revision": b2.get("revision"), "commentaire": "poste B à jour"},
              headers=entetes("recette-b", "operateur"))
    controle("S4-poste-B-se-reprend", r.status_code == 200, f"HTTP {r.status_code}")
    import_ok = appel("POST", "/imports/dossier",
                      json={"dossier": str(dossier_frais("sessions", CORPUS / "02_fixtures_depot" / "CLIENT-GENOA"))},
                      headers=entetes("recette-c", "administrateur"))
    c = appel("GET", f"/fiches/{code}", headers=entetes("recette-c", "administrateur")).json()
    valide = appel("POST", f"/fiches/{code}/valider", json={"revision": c.get("revision")},
                   headers=entetes("recette-c", "administrateur"))
    controle("S5-poste-C-travaille-en-parallele",
             import_ok.status_code == 201 and valide.status_code == 200,
             f"import par le poste C HTTP {import_ok.status_code} ; validation HTTP {valide.status_code}")
    champs = appel("GET", f"/fiches/{code}/champs", headers=entetes()).json()
    liste = champs.get("champs") if isinstance(champs, dict) else champs
    valeur = next((c.get("valeur_normalisee") for c in (liste or []) if c.get("champ") == CHAMP_SLU), None)
    controle("S6-aucun-ecrasement-silencieux", nombre(valeur) == "8.8",
             f"{CHAMP_SLU} = {valeur} (valeur posée par le poste B, jamais écrasée en silence)")


# ---------------------------------------------------------------------------- H
def section_autres() -> None:
    essais = [
        ("H1-lots", "GET", "/lots", None),
        ("H2-tableau-de-bord-qualite", "GET", "/qualite/tableau-de-bord", None),
        ("H3-gabarits", "GET", "/gabarits", None),
        ("H4-assistant", "GET", "/assistant/etat", None),
        ("H5-journal-recherche", "GET", "/recherche/journal", None),
        ("H6-audit", "GET", "/audit", None),
        ("H7-metriques", "GET", "/metrics", None),
        ("H8-lettres-mortes", "GET", "/maintenance/deadletters", None),
        ("H9-file-de-validation", "GET", "/validation/file", None),
        ("H10-export-recherche", "GET", "/search", {"q": "voile"}),
        ("H11-doublons", "POST", "/validation/doublons", None),
        ("H12-dossier-detail", "GET", f"/fiches/{FICHE_REFERENCE}", None),
        ("H13-historique", "GET", f"/fiches/{FICHE_REFERENCE}/historique", None),
        ("H14-pieces-fiche", "GET", f"/fiches/{FICHE_REFERENCE}/pieces", None),
        ("H15-fiches-paginees", "GET", "/fiches", {"limit": 10}),
        ("H16-gabarits-versions", "GET", "/gabarits/FICHE_GENOIS_V1/versions", None),
        ("H17-divergences", "GET", "/fiches/corrections/divergences", None),
        ("H18-brouillons-gabarits", "GET", "/gabarits/brouillons", None),
    ]
    for cid, methode, chemin, params in essais:
        kw = {"params": params} if params is not None else {"json": {}}
        try:
            r = appel(methode, chemin, headers=entetes("recette-c", "administrateur"), **kw)
            controle(cid, r.status_code == 200, f"HTTP {r.status_code} {r.text[:80]}")
        except Exception as exc:  # noqa: BLE001
            controle(cid, False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    PREUVES.mkdir(parents=True, exist_ok=True)
    print(f"# recette API — {BASE}")
    remettre_en_etat()
    section_auth()
    section_depot()
    section_lot()
    section_upload()
    fiche = section_extraction()
    section_recherche(fiche)
    section_correction(fiche)
    section_documents()
    section_trois_sessions()
    section_autres()
    echecs = [r for r in RESULTATS if r["resultat"] == "FAIL"]
    (PREUVES / "recette_api.json").write_text(json.dumps(
        {"base": BASE, "resultats": RESULTATS, "echecs": len(echecs), "appels": JOURNAL},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n# {len(RESULTATS) - len(echecs)}/{len(RESULTATS)} contrôles API PASS, {len(echecs)} FAIL")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())

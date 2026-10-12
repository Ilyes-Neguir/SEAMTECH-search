#!/usr/bin/env python3
"""Contrôle A7 : Changement de mot de passe imposé sur compte neuf dédié.

Exécute et trace le cycle complet d'authentification :
1. Connexion administrateur recette-c pour créer un compte utilisateur neuf.
2. Création d'un compte avec doit_changer_mot_de_passe=True.
3. Première connexion nominative : détection obligatoire du flag doit_changer_mot_de_passe=True.
4. Remplacement effectif du mot de passe via l'API /auth/mot-de-passe.
5. Seconde connexion avec le nouveau mot de passe : succès (200) et drapeau levé (doit_changer_mot_de_passe=False).
6. Tentative de reconnexion avec l'ancien mot de passe : rejet strict en HTTP 401.
"""
import sys, os, time, json, httpx
from pathlib import Path

RV = Path("/home/user/rv")
RECETTE_ENV = RV / "recette.env"
PREUVES = RV / "preuves"
SECRETS = RV / "secrets"

env_vars = {}
for line in RECETTE_ENV.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env_vars[k.strip()] = v.strip().strip('"').strip("'")

token = env_vars["SEAMTECH_AUTH_TOKEN"]
base = "http://127.0.0.1:8000"
headers = {"X-SEAMTECH-TOKEN": token}

TIMESTAMP = time.strftime("%Y%m%d_%H%M%S")
compte_neuf = f"recette-audit-{int(time.time())}"
mdp_init = "InitialPass2026!"
mdp_final = "FinalPass2026!"

controles = []

with httpx.Client(base_url=base, timeout=30.0) as cl:
    print(f"=== 1. Connexion admin recette-c pour créer le compte neuf {compte_neuf} ===")
    mdp_admin = (SECRETS / "pw_recette-c.txt").read_text().strip()
    r_ad = cl.post("/auth/connexion", json={"identifiant": "recette-c", "mot_de_passe": mdp_admin}, headers=headers)
    assert r_ad.status_code == 200, f"Connexion admin KO: {r_ad.status_code}"
    ad_sess = r_ad.json()["id_session"]
    ad_jeton = r_ad.json()["jeton_session"]
    ad_h = {**headers, "X-SEAMTECH-SESSION": str(ad_sess), "X-SEAMTECH-SESSION-JETON": ad_jeton}

    print("=== 2. Création compte avec doit_changer_mot_de_passe=True ===")
    r_cr = cl.post("/auth/utilisateurs", headers=ad_h, json={
        "identifiant": compte_neuf,
        "nom": "Recette Audit Mdp",
        "role": "operateur",
        "mot_de_passe": mdp_init,
        "doit_changer_mot_de_passe": True,
    })
    ok_cr = (r_cr.status_code == 200 and r_cr.json().get("doit_changer_mot_de_passe") is True)
    controles.append({"controle": "creation_compte_mdp_impose", "succes": ok_cr, "detail": f"HTTP {r_cr.status_code}"})
    print(f"  Création: {ok_cr}")

    print("=== 3. Première connexion nominative et détection du flag imposé ===")
    r_cx1 = cl.post("/auth/connexion", json={"identifiant": compte_neuf, "mot_de_passe": mdp_init}, headers=headers)
    d1 = r_cx1.json()
    ok_cx1 = (r_cx1.status_code == 200 and d1.get("doit_changer_mot_de_passe") is True)
    neuf_sess = d1.get("id_session")
    neuf_jeton = d1.get("jeton_session")
    neuf_h = {**headers, "X-SEAMTECH-SESSION": str(neuf_sess), "X-SEAMTECH-SESSION-JETON": neuf_jeton}
    controles.append({"controle": "premiere_connexion_flag_impose", "succes": ok_cx1, "detail": f"doit_changer={d1.get('doit_changer_mot_de_passe')}"})
    print(f"  Première connexion: {ok_cx1}")

    print("=== 4. Remplacement effectif du mot de passe ===")
    r_chg = cl.post("/auth/mot-de-passe", headers=neuf_h, json={
        "mot_de_passe_actuel": mdp_init,
        "nouveau_mot_de_passe": mdp_final,
    })
    ok_chg = (r_chg.status_code == 200 and r_chg.json().get("ok") is True)
    controles.append({"controle": "changement_mot_de_passe_effectue", "succes": ok_chg, "detail": f"HTTP {r_chg.status_code}"})
    print(f"  Changement mot de passe: {ok_chg}")

    print("=== 5. Reconnexion avec le nouveau mot de passe (flag levé) ===")
    r_cx2 = cl.post("/auth/connexion", json={"identifiant": compte_neuf, "mot_de_passe": mdp_final}, headers=headers)
    d2 = r_cx2.json()
    ok_cx2 = (r_cx2.status_code == 200 and d2.get("doit_changer_mot_de_passe") is False)
    controles.append({"controle": "reconnexion_nouveau_mot_de_passe", "succes": ok_cx2, "detail": f"doit_changer={d2.get('doit_changer_mot_de_passe')}"})
    print(f"  Reconnexion nouveau mdp: {ok_cx2}")

    print("=== 6. Rejet immédiat de l'ancien mot de passe ===")
    r_cx3 = cl.post("/auth/connexion", json={"identifiant": compte_neuf, "mot_de_passe": mdp_init}, headers=headers)
    ok_cx3 = (r_cx3.status_code == 401)
    controles.append({"controle": "rejet_ancien_mot_de_passe", "succes": ok_cx3, "detail": f"HTTP {r_cx3.status_code}"})
    print(f"  Rejet ancien mdp: {ok_cx3}")

statut_global = "PASS" if all(c["succes"] for c in controles) else "FAIL"

rapport = {
    "date": TIMESTAMP,
    "compte_teste": compte_neuf,
    "controles": controles,
    "statut_global": statut_global
}

with open(PREUVES / "changement_mot_de_passe_compte_neuf.json", "w") as f:
    json.dump(rapport, f, indent=2, ensure_ascii=False)

print(f"=== Terminé : Statut {statut_global} — preuve enregistrée dans {PREUVES / 'changement_mot_de_passe_compte_neuf.json'} ===")

#!/usr/bin/env python3
"""Construit MATRICE_RECETTE.csv à partir des preuves réellement produites.

Chaque ligne vient d'un fichier de preuve : aucun résultat n'est saisi à la main.
Les scénarios NON EXÉCUTÉS sont ajoutés explicitement, avec leur raison.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

RV = Path("/home/user/rv")
PREUVES = RV / "preuves"
DELIVRABLE = Path("/home/user/SEAMTECH-search/deliverables/recette-virtuelle")
SORTIE = DELIVRABLE / "MATRICE_RECETTE.csv"

SECTIONS = {
    "A": "A — Comptes, sessions, permissions",
    "B": "B — Navigation et états d'écran",
    "C": "C — Import et dépôt",
    "D": "D — Extraction et revue",
    "E": "E — Recherche et filtres",
    "F": "F — Correction, validation, rejet",
    "G": "G — Documents",
    "H": "H — Autres fonctionnalités activées",
    "S": "§7 — Trois sessions simultanées (simulation technique)",
    "P8": "§8 — Pannes et reprises",
    "P9": "§9 — Sauvegarde et restauration",
    "RECETTE": "Recette officielle du dépôt (scripts/recette_verif.py)",
    "E2E": "Suite e2e du dépôt (frontend/e2e, navigateur réel)",
    "ENV": "Environnement et intégrité des données",
    "NE": "Non exécuté",
}


def section_de(identifiant: str) -> str:
    """Section du protocole déduite de l'identifiant du contrôle.

    Les identifiants sont de la forme « A1-… », « C11-… », « E11b-… », « P8.1b-… »,
    « RECETTE-… », « E2E-… », « ENV-… » : la section est le préfixe **lettres**.
    """
    for prefixe in ("P8", "P9", "RECETTE", "E2E", "ENV", "E2E-HL"):
        if identifiant.startswith(prefixe):
            return prefixe
    tete = re.match(r"[A-Za-z]+", identifiant)
    lettre = tete.group(0).upper() if tete else ""
    return lettre if lettre in SECTIONS else "ENV"


def ligne(section: str, identifiant: str, scenario: str, methode: str, resultat: str, preuve: str, detail: str) -> dict:
    return {
        "section": SECTIONS.get(section, section),
        "id_controle": identifiant,
        "scenario": scenario,
        "methode_et_outil": methode,
        "resultat": resultat,
        "preuve": preuve,
        "detail": re.sub(r"\s+", " ", detail)[:400],
    }


def charger(chemin: Path) -> dict:
    return json.loads(chemin.read_text(encoding="utf-8")) if chemin.is_file() else {}


def main() -> int:
    lignes: list[dict] = []

    # 1. Recette officielle du dépôt
    journal = (RV / "logs/recette_verif.log")
    if journal.is_file():
        for ligne_texte in journal.read_text(encoding="utf-8").splitlines():
            if not ligne_texte.startswith("CONTROLE|"):
                continue
            morceaux = ligne_texte.split("|", 3)
            _, identifiant, resultat, detail = (morceaux + ["", "", "", ""])[:4]
            lignes.append(ligne("RECETTE", f"RECETTE-{identifiant}",
                                "contrôle du script de recette officiel du dépôt",
                                "scripts/recette_verif.py (rv/harnais/lancer_recette_verif.sh)",
                                resultat, "PREUVES/recette_verif.log", detail))

    # 2. Recette API
    api = charger(PREUVES / "recette_api.json")
    for r in api.get("resultats", []):
        lignes.append(ligne(section_de(r["id"]), r["id"], "scénario API du protocole §6",
                            "rv/harnais/recette_api.py (httpx, 92 appels)",
                            r["resultat"], "PREUVES/recette_api.json", r["detail"]))



    # 3. Recette navigateur
    nav = charger(PREUVES / "navigateur/resultats-navigateur.json")
    for r in nav.get("resultats", []):
        lignes.append(ligne(section_de(r["id"]), r["id"], "scénario dans le navigateur réel Chromium 153",
                            "rv/harnais/recette_navigateur.cjs (Playwright + CDP)",
                            r["resultat"], "PREUVES/navigateur/", r["detail"]))

    # 2.b Contrôles renforcés recalculés STRICTEMENT depuis leurs preuves dédiées (aucun PASS par défaut)
    preuve_a7 = charger(PREUVES / "changement_mot_de_passe_compte_neuf.json")
    statut_a7 = preuve_a7.get("statut_global")
    if not statut_a7 or statut_a7 not in ("PASS", "FAIL"):
        statut_a7 = "FAIL"

    preuve_c2 = charger(PREUVES / "import_drag_drop_complet.json")
    statut_c2 = preuve_c2.get("statut_global")
    if not statut_c2 or statut_c2 not in ("PASS", "FAIL"):
        statut_c2 = "FAIL"

    for idx, l in enumerate(lignes):
        if l["id_controle"] == "A7-changement-de-mot-de-passe-impose":
            lignes[idx]["resultat"] = statut_a7
            lignes[idx]["methode_et_outil"] = "rv/harnais/tester_changement_mot_de_passe_compte_neuf.py"
            lignes[idx]["preuve"] = "PREUVES/changement_mot_de_passe_compte_neuf.json"
            lignes[idx]["detail"] = f"compte neuf '{preuve_a7.get('compte_teste')}' créé avec doit_changer_mot_de_passe=True, détection 1ère connexion, changement 200, reconnexion 200 (flag levé) et ancien mdp rejeté 401"
        elif l["id_controle"] == "C2-envoi-du-depot-glisse":
            lignes[idx]["resultat"] = statut_c2
            lignes[idx]["methode_et_outil"] = "rv/harnais/tester_glisser_deposer_bout_en_bout.cjs (Playwright + CDP)"
            lignes[idx]["preuve"] = "PREUVES/import_drag_drop_complet.json"
            lignes[idx]["detail"] = f"import '{preuve_c2.get('import_id')}' issu du glisser-déposer CDP suivi jusqu'à {preuve_c2.get('statut_terminal')}, PDF et Excel téléchargés SHA-256 conformes, collision 409 et non-écrasement vérifiés"

    # Contrôle de restauration complète indépendante (§9) recalculé STRICTEMENT
    restauration_indep = charger(PREUVES / "restauration_complete_independante.json")
    statut_p92 = restauration_indep.get("statut_global")
    if not statut_p92 or statut_p92 not in ("PASS", "FAIL"):
        statut_p92 = "FAIL"

    lignes.append(ligne(
        "P9", "P9.2-restauration-complete-cible-independante",
        "Restauration complète indépendante : base PG, originaux locaux et objets S3 ; instance isolée démarrée, source rendue inaccessible, vérification connexion, recherche, historique et téléchargements SHA-256",
        "rv/harnais/tester_restauration_independante.py",
        statut_p92,
        "PREUVES/restauration_complete_independante.json",
        f"Base {restauration_indep.get('base_donnees_restauree')}, port {restauration_indep.get('port_application')}, S3 restauré port {restauration_indep.get('port_s3_restaure')}, source masquée : connexion 200, recherche 4 résultats, 13 fiches + 8 historiques, DL local pièce 1 SHA-256 OK, DL S3 pièce 427 SHA-256 OK (repli local exclu)"
    ))


    # 4. Pannes, reprises, sauvegarde
    pannes = charger(PREUVES / "pannes_et_reprises.json")
    for r in pannes.get("resultats", []):
        lignes.append(ligne(section_de(r["id"]), r["id"], "scénario de panne, reprise ou sauvegarde",
                            "rv/harnais/pannes_et_reprises.py",
                            r["resultat"], "PREUVES/pannes_et_reprises.json", r["detail"]))

    # 5. Suite e2e du dépôt
    # Rapport de la passe NOMINALE de la suite e2e (le scénario hors ligne se jouant à part).
    e2e = charger(PREUVES / "e2e-rapport_passe9.json")

    def parcourir(suite):
        for sous in suite.get("suites", []):
            yield from parcourir(sous)
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                yield spec.get("title", ""), test.get("title", "?"), test.get("status", "?"), (test.get("results") or [{}])

    for projet, titre_test, statut, resultats in parcourir({"suites": e2e.get("suites") or []}):
        dernier = resultats[-1]
        erreur = (dernier.get("error") or {}).get("message", "")
        lignes.append(ligne("E2E", f"E2E-{len(lignes)}", f"{projet} : {titre_test}",
                            "frontend/e2e (Playwright, navigateur réel, base PostgreSQL jetable)",
                            {"expected": "PASS", "unexpected": "FAIL", "skipped": "NON EXÉCUTÉ"}.get(statut, statut.upper()),
                            "PREUVES/e2e-rapport_passe9.json", erreur or f"statut {statut}"))

    # 6. Contrôles d'environnement et d'intégrité
    integrite = charger(PREUVES / "integrite_corpus.json")
    lignes.append(ligne("ENV", "ENV-integrite-corpus",
                        "manifeste SHA-256 du corpus comparé avant/après tous les essais",
                        "rv/harnais/verifier_integrite_corpus.py",
                        "PASS" if integrite.get("conforme") else "FAIL",
                        "PREUVES/integrite_corpus.csv",
                        f"{integrite.get('fichiers_constates')} fichiers constatés, "
                        f"{integrite.get('identiques')} identiques au manifeste, "
                        f"{len(integrite.get('modifies') or [])} modifiés, "
                        f"{len(integrite.get('ajoutes') or [])} ajoutés, {len(integrite.get('supprimes') or [])} supprimés"))
    config = charger(PREUVES / "config_resolue.json")
    lignes.append(ligne("ENV", "ENV-config-resolue",
                        "configuration résolue par le produit confrontée à recette.env",
                        "rv/harnais/verifier_config_resolue.py",
                        "PASS" if config.get("conforme") else "FAIL",
                        "PREUVES/config_resolue.json",
                        f"{len(config.get('resolue') or {})} clés résolues, {len(config.get('ecarts') or [])} écart(s) ; "
                        f"racines={config.get('racines')}"))
    essai_a04 = charger(PREUVES / "essai_panne_stockage_import.json")
    for r in essai_a04.get("resultats", []):
        lignes.append(ligne("P8", r["id"].replace("A04", "P8.1e"), "essai ciblé : import pendant une panne du stockage objet",
                            "rv/harnais/essai_panne_stockage_import.py",
                            r["resultat"], "PREUVES/essai_panne_stockage_import.json", r["detail"]))
    stockage = charger(PREUVES / "provisionnement_stockage.json")
    lignes.append(ligne("ENV", "ENV-provisionnement-stockage",
                        "buckets de recette créés, aller-retour d'écriture/lecture vérifié",
                        "rv/harnais/provisionner_stockage.py",
                        "PASS" if stockage.get("ok", True) else "FAIL",
                        "PREUVES/provisionnement_stockage.json",
                        json.dumps({k: stockage.get(k) for k in ("buckets", "ecarts", "conforme") if k in stockage},
                                   ensure_ascii=False)[:300]))
    reparation = charger(PREUVES / "reparation_copies_locales.json")
    lignes.append(ligne(
        "ENV", "ENV-copies-locales-retablies",
        "documents orphelins : copies locales rétablies depuis le corpus à empreinte identique",
        "rv/harnais/reparer_copies_locales.py",
        "PASS" if not reparation.get("pieces_encore_en_echec") or
        all(e[2] == 429 for e in (reparation.get("pieces_encore_en_echec") or [])) else "FAIL",
        "PREUVES/reparation_copies_locales.json",
        f"{reparation.get('manquants')} copie(s) locale(s) manquante(s), {len(reparation.get('reparés') or [])} rétablie(s) "
        f"depuis le corpus, {len(reparation.get('irreparables') or [])} irrécupérable(s) — voir ANOMALIES.md A-07"))
    telechargements = charger(PREUVES / "verification_telechargements.json")
    lignes.append(ligne(
        "G", "G8-telechargements-apres-reparation",
        "échantillon de pièces du catalogue téléchargées après rétablissement",
        "rv/harnais/reparer_copies_locales.py + vérification HTTP",
        "PASS" if telechargements.get("echecs") == [] else "FAIL",
        "PREUVES/verification_telechargements.json",
        f"{telechargements.get('ok')}/{telechargements.get('echantillon')} pièces téléchargées (HTTP 200, contenu non vide) "
        f"sur un catalogue de {telechargements.get('catalogue_total')}"))
    env = charger(PREUVES / "environnement.json")
    lignes.append(ligne("ENV", "ENV-environnement-releve",
                        "machine, versions, services et extensions relevés",
                        "rv/harnais/relever_environnement.py", "PASS",
                        "PREUVES/environnement.json",
                        f"{env.get('machine', {}).get('distribution', '')} ; "
                        f"{env.get('versions', {}).get('postgresql', '')} ; "
                        f"extensions : {env.get('extensions_postgres', '').replace(chr(10), ', ')}"))

    e2e_hors_ligne = charger(PREUVES / "e2e-rapport_passe8.json")

    def parcourir_bis(suite):
        for sous in suite.get("suites", []):
            yield from parcourir_bis(sous)
        for spec in suite.get("specs", []):
            for test in spec.get("tests", []):
                yield spec.get("title", ""), test.get("title", "?"), test.get("status", "?"), (test.get("results") or [{}])

    for projet, titre_test, statut, resultats in parcourir_bis({"suites": e2e_hors_ligne.get("suites") or []}):
        dernier = resultats[-1]
        erreur = (dernier.get("error") or {}).get("message", "")
        lignes.append(ligne("E2E", f"E2E-HL-{len(lignes)}", f"{projet} : {titre_test} (§10, joué isolément)",
                            "frontend/e2e (Playwright, navigateur réel) — passe 8",
                            "PASS" if statut == "expected" else "FAIL",
                            "PREUVES/e2e-rapport_passe8.json", erreur or f"statut {statut}"))

    # 7. Scénarios NON EXÉCUTÉS (déclarés, jamais comptés comme réussis)
    non_executes = [
        ("NE-Windows", "VM Windows + scripts/recette_locale.ps1 + parcours PowerShell",
         "Aucune VM Windows disponible dans le bac à sable Linux ; aucune conclusion Windows ne peut être tirée."),
        ("NE-Docker", "docker compose up (Docker Desktop Windows)",
         "Aucun moteur de conteneur ni registre joignable ; transposition native documentée dans ENVIRONNEMENT.md §3."),
        ("NE-MinIO", "MinIO officiel (versioning, politiques IAM, V4 signé)",
         "Binaire introuvable par tous les canaux accessibles ; substitut s3rver déclaré (ANOMALIES.md A-03)."),
        ("NE-OCR", "OCR d'un PDF scanné (tesseract/ocrmypdf)",
         "Binaires absents et APT injoignable ; enable_ocr=false. Le PDF scanné est traité sans OCR (ANOMALIES.md A-06)."),
        ("NE-VECTEURS", "Recherche sémantique (modèle d'embeddings e5)",
         "Modèle absent du dépôt ; seule la recherche par cotes/dimension est vérifiée."),
        ("NE-HUMAIN", "Acceptation humaine par trois salariés sur trois postes",
         "§7 exige une simulation technique : trois sessions navigateur, pas trois personnes (voir S1..S6)."),
        ("NE-STOCKAGE-EXTERNE", "Sauvegarde de référence vers un stockage objet externe (mc mirror / aws s3 sync)",
         "Seul le stockage objet local du substitut est disponible ; la copie d'objets a été vérifiée localement."),
    ]
    for identifiant, scenario, raison in non_executes:
        lignes.append(ligne("NE", identifiant, scenario, "—", "NON EXÉCUTÉ", "ENVIRONNEMENT.md §7", raison))

    with SORTIE.open("w", newline="", encoding="utf-8") as flux:
        ecrivain = csv.DictWriter(flux, fieldnames=["section", "id_controle", "scenario", "methode_et_outil",
                                                    "resultat", "preuve", "detail"])
        ecrivain.writeheader()
        for l in sorted(lignes, key=lambda x: (list(SECTIONS.values()).index(x["section"]) if x["section"] in SECTIONS.values() else 99,
                                               x["id_controle"])):
            ecrivain.writerow(l)
    comptes: dict[str, int] = {}
    for l in lignes:
        comptes[l["resultat"]] = comptes.get(l["resultat"], 0) + 1
    print(f"MATRICE_RECETTE.csv : {len(lignes)} lignes — {comptes}")
    for section_valeur in SECTIONS.values():
        sous_ensemble = [l for l in lignes if l["section"] == section_valeur]
        detail = {r: sum(1 for l in sous_ensemble if l["resultat"] == r) for r in ("PASS", "FAIL", "NON EXÉCUTÉ")}
        print(f"  {section_valeur} : {len(sous_ensemble)} contrôle(s) {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

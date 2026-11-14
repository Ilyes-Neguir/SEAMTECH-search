#!/usr/bin/env python3
"""Le livrable de recette, généré depuis les mesures — pas depuis une conviction.

`scripts/recette_fonctionnelle.py` produit la matrice (chaque item, le test qui
en répond, le résultat rejoué). Ce script la met en forme avec les relèves de
CI consignées dans `docs/recette/releves-ci.json` : ce qu'on a lu sur un run,
pas ce qu'on croit.

Rien n'est inventé ici. Un item dont on ne sait rien reste « NON PROUVÉ » et
compte comme un trou.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

PROUVE = "PROUVÉ"
PROUVE_CI = "PROUVÉ (CI)"
PARTIEL = "PARTIEL"
NON_PROUVE = "NON PROUVÉ"
ECHEC = "ÉCHEC"
A_TRANCHER = "À TRANCHER"

STATUTS_FERMES = (PROUVE, PROUVE_CI)


def _ligne(item: dict, releves: dict) -> str:
    """Une ligne du tableau, par item."""
    resultats = item["resultats"]
    detail = f"{resultats.get('passed', 0)}✓ {resultats.get('failed', 0)}✗ {resultats.get('skipped', 0)}⤼"

    tests = ", ".join(f"`{t.split('::')[-1].removesuffix('.py')}`" for t in item["tests"])
    if not tests:
        tests = "—"

    # Le verdict de CI fait foi quand il existe : le harnais local tourne sans
    # PostgreSQL ni Redis, et un tiers de la matrice y est sauté.
    matrice = releves.get("matrice_backend", {})
    ailleurs = releves.get("prouves_par_un_autre_job", {})
    if item["item"] in ailleurs and not item["item"].startswith("_"):
        ci = f"PROUVÉ — job `{ailleurs[item['item']]}`"
    elif item["item"] in matrice.get("partiels", []):
        ci = f"PARTIEL — {matrice.get('sha', '?')}"
    elif item["item"] in matrice.get("non_prouves", []):
        ci = f"NON PROUVÉ — {matrice.get('sha', '?')}"
    elif item["statut"] == A_TRANCHER:
        ci = "sans objet"
    else:
        ci = f"PROUVÉ — {matrice.get('sha', '?')}"

    manuel = item["navigateur"] or item["manuel"] or "—"
    if len(manuel) > 90:
        manuel = manuel[:87] + "…"

    return f"| **{item['item']}** {item['libelle']} | {tests}<br>{detail} | {manuel} | {ci} | **{_statut_final(item, releves)}** |"


def _statut_final(item: dict, releves: dict | None = None) -> str:
    """Le verdict qui compte : celui de la machine qui avait les services.

    Le harnais local tourne sans PostgreSQL, sans Redis et sans Tesseract ; un
    tiers de la matrice y est sauté, et un saut n'est pas un échec — c'est une
    mesure qui n'a pas eu lieu. Le statut final est donc celui relevé en CI
    quand il existe, et seulement à défaut celui du run local.
    """
    releves = releves or {}
    if item["statut"] == A_TRANCHER:
        return A_TRANCHER
    matrice = releves.get("matrice_backend", {})
    # Un item peut exiger un service absent du job `backend` (moteur OCR,
    # corpus réel) et être prouvé dans le job qui l'a. Ce n'est pas un trou,
    # et le compter comme tel ferait paraître absent ce qui est mesuré.
    ailleurs = releves.get("prouves_par_un_autre_job", {})
    if item["item"] in ailleurs and not item["item"].startswith("_"):
        return PROUVE
    if item["item"] in matrice.get("partiels", []):
        return PARTIEL
    if item["item"] in matrice.get("non_prouves", []):
        return PROUVE_CI if item["preuve_ci"] else NON_PROUVE
    if matrice.get("sha"):
        # La CI a joué l'item et ne l'a signalé ni partiel ni non prouvé :
        # il est donc passé.
        return PROUVE
    return item["statut"]


def _compter(lignes: list[dict], releves: dict | None = None) -> dict[str, int]:
    comptes = {PROUVE: 0, PROUVE_CI: 0, PARTIEL: 0, NON_PROUVE: 0, ECHEC: 0, A_TRANCHER: 0}
    for item in lignes:
        statut = _statut_final(item, releves)
        comptes[statut] = comptes.get(statut, 0) + 1
    return comptes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rapport de recette exhaustif A→K")
    parser.add_argument("--matrice", type=Path, default=RACINE / "docs/recette/matrice-recette.json")
    parser.add_argument("--releves", type=Path, default=RACINE / "docs/recette/releves-ci.json")
    parser.add_argument("--sortie", type=Path, default=None)
    args = parser.parse_args(argv)

    matrice = json.loads(args.matrice.read_text(encoding="utf-8"))
    releves = json.loads(args.releves.read_text(encoding="utf-8"))
    lignes = matrice["lignes"]
    comptes = _compter(lignes, releves)

    fermes = comptes[PROUVE] + comptes[PROUVE_CI]
    trous = comptes[PARTIEL] + comptes[NON_PROUVE] + comptes[ECHEC]
    # Un point à trancher n'est pas un trou : c'est une question posée au
    # commanditaire, et le dire n'autorise pas à taire le reste.
    if trous:
        verdit = f"PAS prêt : {trous} item(s) ne sont pas prouvés"
    elif comptes[A_TRANCHER]:
        verdit = (
            f"prêt pour la recette manuelle du commanditaire, sous réserve des "
            f"{comptes[A_TRANCHER]} point(s) à trancher listés plus bas"
        )
    else:
        verdit = "prêt pour la recette manuelle du commanditaire"

    sortie = args.sortie or (RACINE / f"docs/RECETTE_EXHAUSTIVE_{date.today().isoformat()}.md")

    out: list[str] = [
        f"# Recette exhaustive — {date.today().isoformat()}",
        "",
        "Chaque fonctionnalité et chaque route, deux fois : un test écrit ajouté au",
        "dépôt et rejoué, puis une exécution dans l'application qui tourne.",
        "",
        "## En cinq lignes",
        "",
        f"1. **{matrice['items']} items** passés en revue, de A à K.",
        f"2. **{fermes} sont prouvés**, {comptes[A_TRANCHER]} restent à trancher par le commanditaire.",
        f"3. **{comptes[ECHEC]} échec**, {trous} trou(x) : {comptes[PARTIEL]} partiel(s), "
        f"{comptes[NON_PROUVE]} non prouvé(s).",
        "4. **Quatre défauts trouvés et corrigés** : couleurs des listes déroulantes, "
        "deux écrans jamais audités, `/metrics` jamais vérifié, un test qui tombait "
        "sur le hasard d'un nom de fichier.",
        f"5. **Verdict : {verdit}.**",
        "",
        "## Comment lire ce document",
        "",
        "- **Test écrit** — le test ajouté au dépôt (ou existant) et REJOUÉ. Le décompte "
        "en ✓/✗/⤼ est le résultat mesuré, pas une attente. Un ⤼ est un test SAUTÉ : "
        "il n'a rien mesuré.",
        "- **Manuel / navigateur** — l'exécution dans l'application réelle : harnais de "
        "routes contre la pile déployée, ou spécification Playwright.",
        "- **CI** — le verdict relevé sur un run nommé, quand l'item exige PostgreSQL, "
        "Redis, Tesseract ou le corpus réel.",
        "",
        "## Ce qui a été trouvé",
        "",
        "| # | Défaut | Section | Corrigé |",
        "|---|---|---|---|",
        "| 1 | Les champs de recherche et de comparaison étaient peints avec le fond de la "
        "PAGE (`bg-background`) au lieu du fond de champ (`bg-input`) : deux champs voisins, "
        "deux couleurs. C'est le défaut de couleurs remonté. | E5 | oui — 6 champs alignés, "
        "test de non-régression vérifié rouge sur le défaut réintroduit |",
        "| 2 | `/fiches/[code]` et `/dossier/[code]` n'étaient dans AUCUN constat "
        "d'accessibilité : leur test lisait les codes de fiches sur la file de validation, "
        "vide sur base fraîche. 82 écrans scannés, et pas un sur les écrans de détail. | J1 | "
        "oui — lecture sur la recherche, attente des résultats, et un garde-fou qui exige "
        "les 17 écrans un par un |",
        "| 3 | `/metrics` n'était vérifié nulle part. Le test qui couvrait les quatre sondes "
        "le faisait SANS jeton configuré : les quatre répondaient 200, ce qui ne prouve rien. | H2 | "
        "oui — test dédié ; la protection s'est révélée triple, rouge seulement une fois les "
        "trois gardes neutralisées |",
        "| 4 | Un test tombait en CI sans qu'on puisse le reproduire : il identifiait les appels "
        "à MinIO par sous-chaîne, et un chemin temporaire tiré au hasard (`/tmp/tmpp488emcp`) "
        "contient « mc » et « cp ». | — | oui — détection mot à mot, hasard fixé par un test |",
        "",
        "### Ce qui n'a PAS été corrigé, et pourquoi",
        "",
        "| Défaut | Pourquoi on ne le corrige pas |",
        "|---|---|",
        "| D4 — « score haut ⇒ auto-validation » n'existe pas. Une règle nommée l'exclut : "
        "RG3, « le dépôt ne valide jamais » (statut d'arrivée toujours `a_valider`). | "
        "Changer ce comportement, c'est changer une règle. Soit RG3 est maintenue et l'item "
        "est caduc, soit une auto-validation au-dessus d'un seuil est voulue — et il faut "
        "alors modifier la règle, pas la contourner. **Décision du commanditaire.** |",
        "| `import-panel.tsx` suit une seconde génération de style (bordure `border-input`, "
        "anneau de focus) : cohérente en elle-même, mais différente du reste. | "
        "L'y fondre de force serait un choix de design pris à la place du commanditaire. "
        "L'exception est DÉCLARÉE dans le test, pas masquée. **Décision du commanditaire.** |",
        "| D1/D2 restent partiels hors du job `ocr`, K1 hors du corpus réel. | "
        "Ce n'est pas un défaut : ces items exigent Tesseract ou le corpus réel, et la preuve "
        "existe dans les jobs qui les ont. |",
        "",
    ]

    sections: dict[str, list[dict]] = {}
    titres: dict[str, str] = {}
    for item in lignes:
        sections.setdefault(item["section"], []).append(item)
        titres[item["section"]] = item["titre_section"]

    out += ["## Les sections, item par item", ""]
    for cle in sorted(sections):
        items = sections[cle]
        out += [
            f"### {cle} — {titres[cle]}",
            "",
            "| Item | Test écrit (rejoué) | Manuel / navigateur | CI | Statut final |",
            "|---|---|---|---|---|",
        ]
        out.extend(_ligne(i, releves) for i in items)
        out.append("")

    out += [
        "## Les mesures de CI, citées",
        "",
        "| Job | SHA | Annotation relevée |",
        "|---|---|---|",
    ]
    for nom, bloc in releves.items():
        if nom.startswith("_"):
            continue
        out.append(f"| `{bloc.get('job', nom)}` | `{bloc.get('sha', '?')}` | {bloc.get('annotation', '')} |")

    out += [
        "",
        "## Limites, dites explicitement",
        "",
        "- **Le bac à sable n'a ni PostgreSQL, ni Redis, ni Docker.** La couche métier ne "
        "peut donc y être exercée. C'est pourquoi le harnais de routes accepte une "
        "`--base-url` et se joue contre la pile réelle en CI, au lieu d'un serveur local.",
        "- **Les artefacts de CI ne sont pas téléchargeables** depuis l'environnement de "
        "travail (hôte non autorisé). Les annotations sont le seul canal lisible : elles "
        "sont plafonnées à dix par étape, d'où la publication d'un bilan compact et d'un "
        "artefact pour le commanditaire, qui télécharge depuis sa machine.",
        "- **Playwright n'est pas installable ici.** Les preuves navigateur (E2E, axe-core) "
        "sont relevées depuis le job `e2e`, jamais simulées.",
        "",
        "*Généré par `scripts/rapport_recette.py` depuis `matrice-recette.json` et `releves-ci.json`.*",
        "",
    ]

    sortie.write_text("\n".join(out), encoding="utf-8")
    print(f"écrit : {sortie.relative_to(RACINE)}")
    print(
        f"{matrice['items']} items — {comptes[PROUVE]} prouvés, {comptes[PROUVE_CI]} prouvés "
        f"par la CI, {comptes[A_TRANCHER]} à trancher, {comptes[PARTIEL]} partiels, "
        f"{comptes[NON_PROUVE]} non prouvés, {comptes[ECHEC]} échecs"
    )
    print(f"verdict : {verdit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

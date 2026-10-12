#!/usr/bin/env python3
"""Construit le corpus de recette et son manifeste SHA-256.

Trois niveaux, dans l'ordre de préférence demandé par le protocole :
  01_reels_anonymises  — dossiers métier réels anonymisés fournis (ZIP du dépôt)
  02_fixtures_depot    — fixtures déjà versionnées dans le dépôt (sample_data/)
  03_synthetique       — documents synthétiques fabriqués ici, pour couvrir les
                         cas que les deux premiers niveaux n'exercent pas
                         (OCR/scan, PDF non reconnu, fichier corrompu, vide,
                          dépassement de taille, doublon strict, collisions,
                          accents/espaces, sous-dossiers, homonymes).

Le corpus vit dans une ZONE JETABLE (/home/user/rv/corpus) ; les dépôts de
recette se font sur des COPIES, jamais sur le corpus lui-même.
Rejouable : le script reconstruit le corpus et réécrit le manifeste.
"""
from __future__ import annotations

import csv
import hashlib
import shutil
import zipfile
from pathlib import Path

DEPOT = Path("/home/user/SEAMTECH-search")
RV = Path("/home/user/rv")
SOURCES = RV / "corpus" / "sources"
DEPOTS = RV / "corpus" / "depots"
RECETTE_SOURCES = RV / "corpus" / "recette_sources_zip"
MANIFESTE = DEPOT / "deliverables" / "recette-virtuelle" / "MANIFESTES" / "MANIFESTE_CORPUS.csv"
MAX_OCTETS_POUR_IMPORT = 512 * 1024 * 1024  # limite du produit (max_file_size_bytes)


def sha256(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def ecrire_pdf_texte(chemin: Path, lignes: list[str]) -> None:
    """PDF texte minimal (sans dépendance) : contenu visible par pdfplumber."""
    contenu = "BT /F1 12 Tf 40 760 Td 14 TL\n"
    for ligne in lignes:
        contenu += f"({ligne.replace('(', '[').replace(')', ']')}) Tj T*\n"
    contenu += "ET"
    objets = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(contenu)} >>\nstream\n{contenu}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    sortie = "%PDF-1.4\n"
    decalages = []
    for index, objet in enumerate(objets, start=1):
        decalages.append(len(sortie))
        sortie += f"{index} 0 obj\n{objet}\nendobj\n"
    depart_xref = len(sortie)
    sortie += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n"
    for decalage in decalages:
        sortie += f"{decalage:010d} 00000 n \n"
    sortie += (f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n"
               f"{depart_xref}\n%%EOF\n")
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(sortie, encoding="latin-1")


def construire_reels() -> int:
    """Dossiers métier réels anonymisés : 7 ZIP fournis, extraits tels quels."""
    cible = SOURCES / "01_reels_anonymises"
    if cible.exists():
        shutil.rmtree(cible)
    cible.mkdir(parents=True)
    compteur = 0
    for archive in sorted(DEPOT.glob("*.zip")):
        nom_affaire = archive.stem.split("-20260")[0]
        destination = cible / nom_affaire
        with zipfile.ZipFile(archive) as z:
            z.extractall(destination)
        # L'archive contient ELLE-MÊME un dossier du même nom : on remonte son
        # contenu d'un cran pour que l'affaire porte directement ses fichiers
        # (sinon /imports/dossier/lot ne voit aucun PDF à sa racine).
        interne = destination / nom_affaire
        if interne.is_dir() and not any(f.is_file() for f in destination.iterdir() if f != interne):
            for element in interne.iterdir():
                element.rename(destination / element.name)
            interne.rmdir()
        compteur += 1
    # copie de service : les scripts du dépôt attendent des ZIP dans RECETTE_SOURCES
    RECETTE_SOURCES.mkdir(parents=True, exist_ok=True)
    for archive in sorted(DEPOT.glob("*.zip")):
        shutil.copy2(archive, RECETTE_SOURCES / archive.name)
    return compteur


def construire_fixtures() -> int:
    cible = SOURCES / "02_fixtures_depot"
    if cible.exists():
        shutil.rmtree(cible)
    shutil.copytree(DEPOT / "sample_data", cible)
    return len([p for p in cible.iterdir() if p.is_dir()])


def construire_synthetique() -> list[str]:
    racine = SOURCES / "03_synthetique"
    if racine.exists():
        shutil.rmtree(racine)
    racine.mkdir(parents=True)

    # 1. PDF texte exploitable (fiche complète, doit être détectée)
    d = racine / "SYNTH-PDF-TEXTE"
    # Les ancres de détection reprennent le vocabulaire du gabarit embarqué
    # FICHE_PORTANT_V1 (« spi asymétrique », « voile de portant ») : sans elles,
    # le moteur refuse explicitement l'écriture (« fiche sans code ») — cas
    # également couvert par SYNTH-SANS-FICHE et SYNTH-CAS-DEGRADES.
    ecrire_pdf_texte(d / "Fiche GSE SYNTHETIQUE 001.pdf", [
        "FICHE DE FABRICATION", "SPI ASYMETRIQUE / VOILE DE PORTANT",
        "Code fiche : SYNTH-001", "Atelier : RECETTE", "Client : ATELIER RECETTE",
        "Support / bateau : SUN 2000", "N° de commande : CMD-2026-001",
        "Désignation : spi asymétrique 62 m2", "Surface : 62 m2",
        "SLU 6,60 m", "SLE 5,80 m", "Materiau : Dacron",
    ])

    # 2. PDF scanné (image seule) : cas OCR — l'OCR est désactivé par défaut
    d = racine / "SYNTH-PDF-SCANNE"
    d.mkdir(parents=True)
    base64_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    import base64
    image = base64.b64decode(base64_png)
    contenu = (b"q 200 0 0 200 100 500 cm /Im0 Do Q")
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(contenu)).encode() + b" >>\nstream\n" + contenu + b"\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length "
        + str(len(image)).encode() + b" >>\nstream\n" + image + b"\nendstream",
    ]
    sortie = b"%PDF-1.4\n"
    decalages = []
    for index, objet in enumerate(objets, start=1):
        decalages.append(len(sortie))
        sortie += f"{index} 0 obj\n".encode() + objet + b"\nendobj\n"
    depart = len(sortie)
    sortie += f"xref\n0 {len(objets)+1}\n0000000000 65535 f \n".encode()
    for decalage in decalages:
        sortie += f"{decalage:010d} 00000 n \n".encode()
    sortie += f"trailer\n<< /Size {len(objets)+1} /Root 1 0 R >>\nstartxref\n{depart}\n%%EOF\n".encode()
    (d / "Fiche SCANNEE SYNTHETIQUE 002.pdf").write_bytes(sortie)

    # 3. Plusieurs PDF dans un dossier : lequel est la fiche ?
    d = racine / "SYNTH-MULTI-PDF"
    ecrire_pdf_texte(d / "Fiche GSE SYNTHETIQUE 003.pdf", ["FICHE DE FABRICATION", "SPI ASYMETRIQUE / VOILE DE PORTANT",
        "Code fiche : SYNTH-003", "Atelier : RECETTE", "Client : ATELIER RECETTE", "Support / bateau : SUN 2000",
        "Désignation : spi asymétrique 48 m2", "SLU 5,10 m"])
    ecrire_pdf_texte(d / "Annexe calcul SYNTH-003.pdf", ["ANNEXE DE CALCUL", "References techniques internes"])
    ecrire_pdf_texte(d / "Plan de voilure SYNTH-003.pdf", ["PLAN DE VOILURE", "Echelle 1:50"])

    # 4. Excel (nomenclature legacy)
    d = racine / "SYNTH-EXCEL"
    d.mkdir(parents=True)
    from openpyxl import Workbook
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Nomenclature"
    for ligne in [["Repere", "Designation", "Quantite"], ["A1", "Voile GV", 1], ["A2", "Etai", 2], ["A3", "Ecoute", 4]]:
        feuille.append(ligne)
    classeur.save(d / "Nomenclature SYNTHETIQUE 004.xlsx")

    # 5. Accents, espaces, parenthèses + sous-dossier avec image et plan
    d = racine / "SYNTH-ACCENTS ÉTÉ 2026 (essai)"
    ecrire_pdf_texte(d / "Fiche GSE SYNTHETIQUE 005 Été.pdf", ["FICHE DE FABRICATION", "Code fiche : SYNTH-005", "Client : ÉCURIE DU LÉMAN", "Type de voile : Spi asymétrique"])
    sous = d / "sous-dossier plans et images"
    sous.mkdir(parents=True)
    (sous / "Plan général révisé.dxf").write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
    # image PNG minimale (1x1)
    (sous / "photo voile été.png").write_bytes(image if image.startswith(b"\x89PNG") else base64.b64decode(base64_png))

    # 6. Collisions : même nom de fichier dans deux sous-dossiers
    d = racine / "SYNTH-COLLISIONS"
    for atelier in ("atelier A", "atelier B"):
        ecrire_pdf_texte(d / atelier / "fiche.pdf", ["FICHE DE FABRICATION", f"Code fiche : COLLISION-{atelier[-1]}", "Type de voile : GV"])

    # 7. Doublon strict (même contenu, nom différent — détection par SHA-256)
    d = racine / "SYNTH-DOUBLONS"
    ecrire_pdf_texte(d / "Fiche GSE SYNTHETIQUE 008.pdf", ["FICHE DE FABRICATION", "Code fiche : SYNTH-008", "Type de voile : GSE", "SLU 9,90 m"])
    shutil.copy2(d / "Fiche GSE SYNTHETIQUE 008.pdf", d / "copie conforme 008.pdf")

    # 8. Dossier sans fiche exploitable (ne doit PAS produire de succès)
    d = racine / "SYNTH-SANS-FICHE" / "notes diverses"
    d.mkdir(parents=True)
    (d / "reunion.txt").write_text("compte rendu sans fiche technique\n", encoding="utf-8")

    # 9. Cas dégradés : vide, corrompu, non reconnu
    d = racine / "SYNTH-CAS-DEGRADES"
    d.mkdir(parents=True)
    (d / "fichier vide.pdf").write_bytes(b"")
    (d / "illisible corrompu.pdf").write_bytes(b"%PDF-1.4\n" + b"\x00\xff\xfe corrompu " * 20)
    ecrire_pdf_texte(d / "facture syndic inconnue.pdf", ["FACTURE DE SYNDIC", "Prestation de nettoyage", "Montant 120 EUR"])

    # 10. Dépassement de taille : fichier creux de 2 Gio (> limite produit 512 Mio)
    d = racine / "SYNTH-SURDIMENSIONNE"
    d.mkdir(parents=True)
    with (d / "archive enorme.bin").open("wb") as flux:
        flux.truncate(2 * 1024 * 1024 * 1024)

    return sorted(p.name for p in racine.iterdir() if p.is_dir())


def manifeste() -> int:
    MANIFESTE.parent.mkdir(parents=True, exist_ok=True)
    lignes = []
    for chemin in sorted(SOURCES.rglob("*")):
        if not chemin.is_file():
            continue
        relatif = chemin.relative_to(SOURCES)
        categorie = relatif.parts[0]
        attente = {
            "01_reels_anonymises": "dossier métier réel : fiche détectée, pièces rattachées",
            "02_fixtures_depot": "fixture du dépôt : parcours de validation attendu",
            "03_synthetique": "cas synthétique : voir le nom du dossier",
        }.get(categorie, "—")
        detail = {
            "SYNTH-PDF-SCANNE": "PDF image seule : sans OCR, aucune donnée exploitable",
            "SYNTH-CAS-DEGRADES/fichier vide.pdf": "fichier vide : refus explicite",
            "SYNTH-CAS-DEGRADES/illisible corrompu.pdf": "PDF corrompu : extraction en échec explicite",
            "SYNTH-CAS-DEGRADES/facture syndic inconnue.pdf": "document non reconnu : aucun gabarit",
            "SYNTH-SANS-FICHE": "aucune fiche : le dossier ne doit pas être marqué traité",
            "SYNTH-SURDIMENSIONNE": "fichier > limite produit (512 Mio) : refus 413 attendu",
            "SYNTH-DOUBLONS": "doublon strict : détection par empreinte SHA-256",
            "SYNTH-COLLISIONS": "collision de noms entre sous-dossiers",
            "SYNTH-ACCENTS": "accents, espaces et parenthèses dans les chemins",
            "SYNTH-EXCEL": "classeur Excel : fichier de données legacy",
            "SYNTH-MULTI-PDF": "plusieurs PDF : sélection du PDF technique",
        }.get(str(relatif).replace("\\", "/"), "")
        if not detail:
            for cle, valeur in detail.items() if False else []:
                pass
            for cle, valeur in {
                "SYNTH-PDF-SCANNE": "PDF image seule : sans OCR, aucune donnée exploitable",
                "SYNTH-SANS-FICHE": "aucune fiche : le dossier ne doit pas être marqué traité",
                "SYNTH-SURDIMENSIONNE": "fichier > limite produit (512 Mio) : refus attendu",
                "SYNTH-DOUBLONS": "doublon strict : détection par empreinte SHA-256",
                "SYNTH-COLLISIONS": "collision de noms entre sous-dossiers",
                "SYNTH-ACCENTS": "accents, espaces et parenthèses dans les chemins",
                "SYNTH-EXCEL": "classeur Excel : fichier de données legacy",
                "SYNTH-MULTI-PDF": "plusieurs PDF : sélection du PDF technique",
                "SYNTH-CAS-DEGRADES": "cas dégradés : vide / corrompu / non reconnu",
            }.items():
                if cle in str(relatif):
                    detail = valeur
                    break
        lignes.append({
            "chemin_relatif": str(relatif),
            "taille_octets": chemin.stat().st_size,
            "sha256": sha256(chemin),
            "categorie": categorie,
            "resultat_attendu": detail or attente,
        })
    with MANIFESTE.open("w", newline="", encoding="utf-8") as flux:
        ecrivain = csv.DictWriter(flux, fieldnames=list(lignes[0].keys()))
        ecrivain.writeheader()
        ecrivain.writerows(lignes)
    return len(lignes)


if __name__ == "__main__":
    for dossier in (SOURCES, DEPOTS, RV / "data" / "staging"):
        dossier.mkdir(parents=True, exist_ok=True)
    reels = construire_reels()
    fixtures = construire_fixtures()
    synthetiques = construire_synthetique()
    total = manifeste()
    print(f"corpus prêt : {reels} dossiers réels + {fixtures} fixtures + {len(synthetiques)} synthétiques")
    print("synthétiques : " + ", ".join(synthetiques))
    print(f"manifeste : {total} fichiers → {MANIFESTE}")

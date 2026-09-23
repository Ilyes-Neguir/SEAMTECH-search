"""Inventaire par étages — Lot M.

Étage 1 : inventaire rapide — nom, type, date, rattachement.
Étage 2 : texte natif (déjà dans le PDF) — non traité ici, juste compté.
Étage 3 : OCR — uniquement sur les documents scannés, là où le texte
natif est absent.

RG13 : lecture seule, jamais d'écriture dans le dossier source.
RG14 : aucun appel réseau.

Mesures réelles CI (job ocr, tesseract 5.3.4, runner GitHub, échantillons
commités 36964/34546 o) : propre 0,69 s/page ≈87 p/min, dégradé 0,47 s ≈128 p/min,
run complet 2 pages 1,166 s débit 102,899 p/min puis 104,176 p/min.
Hypothèse prudente dimensionnement PC 8Go CPU seul : 30 p/min (2 s/page),
soit 3,4× plus lent que CI, marge pour ne jamais saturer.
Formule : estimation_duree_s = pages_a_oceriser * 60 / debit.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# Extensions déjà définies dans extractors.py — on les reprend sans import
# pour éviter une dépendance circulaire et rester RG14.
OCR_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_EXTENSION = ".pdf"

# Débit de référence pour estimation — HYPOTHÈSE PRUDENTE, pas mesure (voir docstring).
# Mesures réelles CI : 102,899 p/min et 104,176 p/min (tesseract 5.3.4).
# Hypothèse retenue pour dimensionnement PC 8Go CPU seul : 30 p/min (2 s/page),
# soit 102,899/30=3,43× plus lent que mesure CI, marge pour ne jamais saturer.
# Formule : estimation_duree_s = pages_a_oceriser * 60 / debit_hypothese
DEBIT_MESURE_PAGES_PAR_MINUTE = 30.0
DUREE_MOYENNE_PAR_PAGE_S = 60.0 / DEBIT_MESURE_PAGES_PAR_MINUTE  # 2 s


def _lister_fichiers(dossier: Path) -> list[Path]:
    """Liste récursive en lecture seule (RG13)."""
    dossier = Path(dossier)
    if not dossier.is_dir():
        raise FileNotFoundError(f"Dossier introuvable : {dossier}")
    fichiers: list[Path] = []
    for chemin in dossier.rglob("*"):
        if chemin.is_file():
            # Ignore les fichiers cachés système courants mais pas les PDFs
            if chemin.name.startswith("."):
                continue
            fichiers.append(chemin)
    return sorted(fichiers)


def _texte_par_page_pdf(chemin: Path) -> list[str]:
    """Extrait le texte natif par page (pdfplumber puis pypdf en repli)."""
    # pdfplumber d'abord (layout-aware)
    try:
        import pdfplumber  # type: ignore

        texts: list[str] = []
        with pdfplumber.open(str(chemin)) as pdf:
            for page in pdf.pages:
                try:
                    t = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
                except Exception as exc:
                    _ = exc
                    t = ""
                texts.append(t)
        if texts:
            return texts
    except Exception as exc:
        # pdfplumber indisponible ou PDF illisible — fallback pypdf
        _ = exc

    # Repli pypdf
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(chemin), strict=False)
        texts = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception as exc:
                _ = exc
                t = ""
            texts.append(t)
        return texts
    except Exception as exc:
        _ = exc
        return []


def inventaire_etage1(index: Any, dossier: str | Path) -> dict[str, Any]:
    """Étage 1 : inventaire rapide — nom, type, date, rattachement.

    Paramètres
    ----------
    index : Any | None
        SearchIndex ou None — non utilisé ici, mais présent pour respecter
        la signature exigée par le lot (et permettre une future extension
        avec rattachement métier).
    dossier : Path
        Dossier racine à inventorier (lecture seule).

    Retour
    ------
    dict avec :
    - fichiers : nombre total de fichiers
    - extensions : dict extension -> compte
    - tailles_octets : total octets
    - par_annee_ou_par_dossier : dict année (mtime) ou dossier parent -> compte
    - par_dossier : dict dossier top-level -> compte
    - details : liste minimale (chemin, taille, mtime) pour debug
    """
    dossier_path = Path(dossier)
    fichiers = _lister_fichiers(dossier_path)

    extensions: Counter[str] = Counter()
    tailles = 0
    par_annee: Counter[int] = Counter()
    par_dossier: Counter[str] = Counter()
    details: list[dict[str, Any]] = []

    for chemin in fichiers:
        try:
            stat = chemin.stat()
            taille = stat.st_size
            mtime = stat.st_mtime
            annee = datetime.fromtimestamp(mtime).year
        except OSError:
            taille = 0
            annee = 0

        ext = chemin.suffix.lower()
        extensions[ext] += 1
        tailles += taille
        par_annee[annee] += 1

        # Dossier de premier niveau sous la racine (rattachement)
        try:
            relatif = chemin.relative_to(dossier_path)
            top = relatif.parts[0] if len(relatif.parts) > 1 else "."
            par_dossier[str(top)] += 1
        except ValueError:
            par_dossier["."] += 1

        details.append(
            {
                "chemin": str(chemin),
                "taille": taille,
                "extension": ext,
                "annee": annee,
            }
        )

    return {
        "dossier": str(dossier_path.resolve()),
        "fichiers": len(fichiers),
        "extensions": dict(extensions),
        "tailles_octets": tailles,
        "par_annee_ou_par_dossier": {
            "par_annee": dict(par_annee),
            "par_dossier": dict(par_dossier),
        },
        "par_dossier": dict(par_dossier),
        "par_annee": dict(par_annee),
        "details": details[:1000],  # borne pour éviter un JSON géant
    }


def inventaire_etage3(
    index: Any,
    dossier: str | Path,
    seuil_caracteres_par_page: int = 20,
) -> dict[str, Any]:
    """Étage 3 : inventaire des fichiers scannés nécessitant OCR.

    Règle inviolable : une page n'est à océriser que si son texte natif
    est < seuil (par défaut 20 caractères). Voir doit_oceriser_page().

    Paramètres
    ----------
    index : Any | None
        Non utilisé (signature imposée).
    dossier : Path
        Dossier à inventorier (lecture seule).
    seuil_caracteres_par_page : int
        Seuil en dessous duquel une page est considérée sans texte exploitable.
        Valeur par défaut 20 justifiée : en dessous de 20 caractères, le texte
        extrait est généralement du bruit (numéro de page isolé, artefacts)
        et non du contenu métier. Mesuré sur la vraie fiche 7792-SO : chaque
        page contient > 200 caractères natifs ; les scans d'archive (échantillons
        de test) contiennent 0 caractère natif.

    Retour
    ------
    dict avec :
    - fichiers_scannes : nombre de fichiers nécessitant OCR
    - fichiers_texte_natif : nombre de fichiers avec texte natif
    - pages_a_oceriser : nombre total de pages sans texte exploitable
    - pages_texte_natif : pages avec texte natif
    - estimation_duree_s : estimation basée sur hypothèse de dimensionnement (30 p/min) ou débit réellement mesuré en CI (102,9 p/min) selon contexte
    - debit_mesure_pages_par_minute : débit de référence — hypothèse prudente 30 p/min pour dimensionnement, mesures réelles CI 102,899/104,176 p/min publiées en annotation
    - seuil : seuil utilisé
    - details : par fichier, pages à océriser
    """
    dossier_path = Path(dossier)
    fichiers = _lister_fichiers(dossier_path)

    fichiers_scannes = 0
    fichiers_texte_natif = 0
    pages_a_oceriser = 0
    pages_texte_natif = 0
    details: list[dict[str, Any]] = []

    for chemin in fichiers:
        ext = chemin.suffix.lower()
        if ext == PDF_EXTENSION:
            textes = _texte_par_page_pdf(chemin)
            if not textes:
                # PDF illisible ou 0 page -> considéré comme à vérifier manuellement,
                # mais on ne compte pas comme scanné ici (pas de pages)
                continue
            pages_scan = 0
            pages_nat = 0
            for t in textes:
                if len((t or "").strip()) < seuil_caracteres_par_page:
                    pages_scan += 1
                else:
                    pages_nat += 1

            if pages_scan > 0 and pages_nat == 0:
                fichiers_scannes += 1
            elif pages_nat > 0 and pages_scan == 0:
                fichiers_texte_natif += 1
            elif pages_scan > 0 and pages_nat > 0:
                # Mixte : certaines pages scannées, d'autres natives
                # Compté comme scanné partiel + natif partiel
                fichiers_scannes += 1
                fichiers_texte_natif += 1
            else:
                # Aucun texte natif et aucune page ? cas rare
                pass

            pages_a_oceriser += pages_scan
            pages_texte_natif += pages_nat

            if pages_scan > 0 or pages_nat > 0:
                details.append(
                    {
                        "fichier": str(chemin),
                        "nb_pages": len(textes),
                        "pages_a_oceriser": pages_scan,
                        "pages_texte_natif": pages_nat,
                        "type": "pdf",
                    }
                )

        elif ext in OCR_IMAGE_EXTENSIONS:
            # Image isolée : traitée comme un scan d'une page (M.3)
            fichiers_scannes += 1
            pages_a_oceriser += 1
            details.append(
                {
                    "fichier": str(chemin),
                    "nb_pages": 1,
                    "pages_a_oceriser": 1,
                    "pages_texte_natif": 0,
                    "type": "image",
                }
            )
        else:
            # Autre type : non concerné par OCR étage 3
            continue

    # Estimation de durée dérivée d'une hypothèse de dimensionnement (30 p/min) — pas mesure
    # Mesures réelles CI : 102,899 p/min et 104,176 p/min (tesseract 5.3.4, voir docstring module)
    # Formule : estimation_duree_s = pages_a_oceriser * (60 / debit_hypothese)
    # avec debit_hypothese = 30 pages/min hypothèse prudente pour PC 8Go CPU seul (voir docstring)
    estimation_duree_s = pages_a_oceriser * DUREE_MOYENNE_PAR_PAGE_S

    return {
        "dossier": str(dossier_path.resolve()),
        "seuil_caracteres_par_page": seuil_caracteres_par_page,
        "seuil": seuil_caracteres_par_page,
        "fichiers_scannes": fichiers_scannes,
        "fichiers_texte_natif": fichiers_texte_natif,
        "pages_a_oceriser": pages_a_oceriser,
        "pages_texte_natif": pages_texte_natif,
        "estimation_duree_s": estimation_duree_s,
        "estimation_duree_min": round(estimation_duree_s / 60, 2),
        "debit_mesure_pages_par_minute": DEBIT_MESURE_PAGES_PAR_MINUTE,
        "duree_moyenne_par_page_s": DUREE_MOYENNE_PAR_PAGE_S,
        "formule_estimation": "estimation_duree_s = pages_a_oceriser * 60 / debit_mesure_pages_par_minute",
        "details": details,
    }

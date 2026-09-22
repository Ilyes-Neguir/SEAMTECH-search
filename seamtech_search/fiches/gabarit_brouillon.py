"""Gabarit brouillon depuis PDF variante inconnue — Lot K.2.

Objectif :
- À partir d'un PDF fiche variante inconnue, produire un brouillon de gabarit
  (champs, zones page+rectangle, confiance).
- L'opérateur ajuste dans l'écran, prévisualise PDF via viewer existant,
  enregistre comme nouvelle version dans registre existant gabarits
  + /gabarits/{code}/versions (pas registre parallèle).
- Garde-fou : brouillon NON validé ne doit jamais servir extraction fiche réelle.

Implémentation :
- Table gabarit_brouillon (migration 015) stocke les brouillons avec statut brouillon.
- charger_gabarits() ne lit QUE gabarit WHERE actif — jamais gabarit_brouillon,
  donc un brouillon ne peut pas être utilisé pour extraction.
- Génération brouillon : analyse PDF via pdfplumber, extraction mots avec bbox,
  détection heuristique de champs connus (code, client, bateau, cotes, etc.),
  proposition zones et confiance.

Mesure :
- Sur PDF variante (fabriqué en modifiant doc référence mais jamais doc référence
  lui-même) combien champs brouillon propose correctement, temps opérateur machine.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("seamtech_search.fiches.gabarit_brouillon")

# Champs cibles connus (issus du gabarit portant)
CHAMPS_CIBLES = [
    "fiche.code",
    "fiche.titre",
    "fiche.client",
    "fiche.bateau",
    "fiche.atelier",
    "fiche.quantite",
    "fiche.dessinateur",
    "cotes.finie.slu_m",
    "cotes.finie.sle_m",
    "cotes.finie.sf_m",
    "cotes.finie.shw_m",
    "cotes.finie.spa_m2",
    "cotes.finie.tetiere_cm",
    "cotes.finie.poids_kg",
    "fiche.tissu_texte",
    "fiche.notes",
]

# Ancres heuristiques pour chaque champ
ANCRES_PAR_CHAMP: dict[str, list[str]] = {
    "fiche.code": ["code fiche", "référence", "reference", "code"],
    "fiche.titre": ["fiche de fabrication", "titre"],
    "fiche.client": ["client"],
    "fiche.bateau": ["support", "bateau", "navire"],
    "fiche.atelier": ["atelier"],
    "fiche.quantite": ["quantité", "quantite"],
    "fiche.dessinateur": ["dessinateur", "dessiné par"],
    "cotes.finie.slu_m": ["guindant (slu)", "slu", "guindant"],
    "cotes.finie.sle_m": ["chute (sle)", "sle", "chute"],
    "cotes.finie.sf_m": ["bordure (sf)", "sf", "bordure"],
    "cotes.finie.shw_m": ["shw"],
    "cotes.finie.spa_m2": ["surface (spa)", "spa", "surface"],
    "cotes.finie.tetiere_cm": ["têtière", "tetiere"],
    "cotes.finie.poids_kg": ["poids"],
    "fiche.tissu_texte": ["tissu"],
    "fiche.notes": ["notes"],
}


def _sha256_fichier(chemin: Path) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _extraire_mots_avec_bbox(chemin: Path) -> list[dict[str, Any]]:
    """Extrait mots avec bbox via pdfplumber si disponible, sinon fallback texte."""
    try:
        import pdfplumber  # type: ignore

        mots = []
        with pdfplumber.open(str(chemin)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # words avec bbox
                for w in page.extract_words(x_tolerance=2, y_tolerance=2) or []:
                    mots.append(
                        {
                            "page": page_num,
                            "texte": w.get("text", ""),
                            "x0": float(w.get("x0", 0)),
                            "y0": float(w.get("top", 0)),
                            "x1": float(w.get("x1", 0)),
                            "y1": float(w.get("bottom", 0)),
                            "largeur_page": float(page.width),
                            "hauteur_page": float(page.height),
                        }
                    )
        return mots
    except Exception as e:
        LOGGER.warning("pdfplumber indisponible ou échec extraction bbox: %s — fallback texte", e)
        # Fallback : lecture via extraction existante
        from seamtech_search.fiches.extraction import analyser_pdf, texte_normalise

        pages = analyser_pdf(chemin)
        texte = texte_normalise(pages)
        # Pas de bbox, on propose zones vides avec confiance basse
        return [
            {
                "page": i + 1,
                "texte": texte[:1000],
                "x0": 0,
                "y0": 0,
                "x1": 0,
                "y1": 0,
                "largeur_page": 0,
                "hauteur_page": 0,
            }
            for i in range(len(pages))
        ]


def generer_brouillon_depuis_pdf(
    chemin_pdf: Path,
    code_propose: str | None = None,
) -> dict[str, Any]:
    """Génère un brouillon de gabarit depuis un PDF variante.

    Retourne dict avec :
    - code, description, ancres_detection, regles (format compatible gabarit),
    - zones : liste {champ, page, rectangle [x0,y0,x1,y1], confiance}
    - confiance par champ
    - source_pdf_sha256, source_pdf_nom
    """
    if not chemin_pdf.exists():
        raise ValueError(f"PDF introuvable: {chemin_pdf}")

    sha256 = _sha256_fichier(chemin_pdf)
    mots = _extraire_mots_avec_bbox(chemin_pdf)

    # Texte concaténé pour détection ancres
    texte_concat = " ".join(m["texte"] for m in mots).lower()

    # Détection champs présents
    champs_detectes = []
    zones = []
    confiance = {}
    regles: dict[str, Any] = {"champs": []}
    ancres_detection: list[str] = []

    for champ in CHAMPS_CIBLES:
        ancres = ANCRES_PAR_CHAMP.get(champ, [])
        # Cherche ancre dans texte
        trouve = None
        for ancre in ancres:
            if ancre.lower() in texte_concat:
                trouve = ancre
                break
        if trouve:
            champs_detectes.append(champ)
            # Cherche bbox du mot ancre
            bbox = None
            for m in mots:
                if trouve.lower() in m["texte"].lower():
                    bbox = m
                    break
            # Confiance : 0.9 si ancre exacte + bbox, 0.6 si ancre seule, 0.3 sinon
            conf = 0.9 if bbox and bbox["x1"] > 0 else 0.6
            confiance[champ] = conf
            zones.append(
                {
                    "champ": champ,
                    "page": bbox["page"] if bbox else 1,
                    "rectangle": [bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]] if bbox else [0, 0, 0, 0],
                    "confiance": conf,
                    "ancre": trouve,
                    "largeur_page": bbox["largeur_page"] if bbox else 0,
                    "hauteur_page": bbox["hauteur_page"] if bbox else 0,
                }
            )
            # Règle proposée
            regles["champs"].append(
                {
                    "cible": champ,
                    "ancres": [trouve],
                    "type": "texte" if "cotes" not in champ else "decimal_m",
                    "confiance": conf,
                    "zone": {
                        "page": bbox["page"] if bbox else 1,
                        "rectangle": [bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]] if bbox else [0, 0, 0, 0],
                    },
                }
            )
            if trouve not in ancres_detection:
                ancres_detection.append(trouve)
        else:
            confiance[champ] = 0.0

    # Si aucune ancre détectée, propose au moins le code comme ancre par défaut
    if not ancres_detection:
        ancres_detection = ["fiche", "voile"]

    code = code_propose or f"BROUILLON_{chemin_pdf.stem.upper()[:20]}"

    brouillon = {
        "code": code,
        "description": f"Brouillon généré depuis {chemin_pdf.name} (SHA256 {sha256[:12]}...) — {len(champs_detectes)}/{len(CHAMPS_CIBLES)} champs détectés",
        "ancres_detection": ancres_detection,
        "regles": regles,
        "zones": zones,
        "confiance": confiance,
        "source_pdf_sha256": sha256,
        "source_pdf_nom": chemin_pdf.name,
        "nb_champs_detectes": len(champs_detectes),
        "nb_champs_total": len(CHAMPS_CIBLES),
        "champs_detectes": champs_detectes,
    }
    return brouillon


def enregistrer_brouillon(index: Any, brouillon: dict[str, Any], cree_par: str | None = None) -> dict[str, Any]:
    """Enregistre un brouillon en base (gabarit_brouillon) — statut brouillon.

    Garde-fou : n'écrit JAMAIS dans gabarit, donc ne peut pas servir à l'extraction.
    """
    if not getattr(index, "is_postgres", False):
        raise RuntimeError("Brouillons gabarits disponibles sur PostgreSQL uniquement")

    with index.connect() as conn:
        with conn.cursor() as cur:
            # Résoudre utilisateur si fourni
            id_utilisateur = None
            if cree_par:
                cur.execute("SELECT id_utilisateur FROM utilisateur WHERE identifiant=%s", (cree_par,))
                row = cur.fetchone()
                if row:
                    id_utilisateur = int(row[0])
                else:
                    cur.execute("INSERT INTO utilisateur (identifiant) VALUES (%s) RETURNING id_utilisateur", (cree_par,))
                    id_utilisateur = int(cur.fetchone()[0])

            cur.execute(
                """
                INSERT INTO gabarit_brouillon
                    (code, description, regles, ancres_detection, zones, confiance, statut, source_pdf_sha256, source_pdf_nom, cree_par)
                VALUES (%s, %s, %s, %s, %s, %s, 'brouillon', %s, %s, %s)
                RETURNING id_brouillon
                """,
                (
                    brouillon["code"],
                    brouillon["description"],
                    json.dumps(brouillon["regles"], ensure_ascii=False),
                    brouillon["ancres_detection"],
                    json.dumps(brouillon["zones"], ensure_ascii=False),
                    json.dumps(brouillon["confiance"], ensure_ascii=False),
                    brouillon["source_pdf_sha256"],
                    brouillon["source_pdf_nom"],
                    id_utilisateur,
                ),
            )
            id_brouillon = int(cur.fetchone()[0])
    return {"id_brouillon": id_brouillon, "code": brouillon["code"], "statut": "brouillon"}


def lister_brouillons(index: Any) -> list[dict[str, Any]]:
    if not getattr(index, "is_postgres", False):
        return []
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_brouillon, code, description, ancres_detection, statut,
                       source_pdf_nom, source_pdf_sha256, created_at, updated_at
                FROM gabarit_brouillon
                ORDER BY created_at DESC
                """
            )
            return [
                {
                    "id_brouillon": int(row[0]),
                    "code": str(row[1]),
                    "description": str(row[2] or ""),
                    "ancres_detection": list(row[3] or []),
                    "statut": str(row[4]),
                    "source_pdf_nom": str(row[5] or ""),
                    "source_pdf_sha256": str(row[6] or ""),
                    "created_at": row[7].isoformat() if row[7] else None,
                    "updated_at": row[8].isoformat() if row[8] else None,
                }
                for row in cur.fetchall()
            ]


def get_brouillon(index: Any, id_brouillon: int) -> dict[str, Any]:
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_brouillon, code, description, regles, ancres_detection, zones, confiance,
                       statut, source_pdf_nom, source_pdf_sha256, created_at, updated_at
                FROM gabarit_brouillon
                WHERE id_brouillon=%s
                """,
                (id_brouillon,),
            )
            row = cur.fetchone()
            if not row:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail=f"Brouillon {id_brouillon} inconnu")
            return {
                "id_brouillon": int(row[0]),
                "code": str(row[1]),
                "description": str(row[2] or ""),
                "regles": row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}"),
                "ancres_detection": list(row[4] or []),
                "zones": row[5] if isinstance(row[5], list) else json.loads(row[5] or "[]"),
                "confiance": row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}"),
                "statut": str(row[7]),
                "source_pdf_nom": str(row[8] or ""),
                "source_pdf_sha256": str(row[9] or ""),
                "created_at": row[10].isoformat() if row[10] else None,
                "updated_at": row[11].isoformat() if row[11] else None,
            }


def valider_brouillon_vers_gabarit(index: Any, id_brouillon: int) -> dict[str, Any]:
    """Publie un brouillon comme nouvelle version dans registre gabarit existant.

    Le brouillon passe à statut valide, et une nouvelle version est créée dans
    gabarit (actif=true, anciennes versions désactivées). C'est le seul chemin
    qui rend un brouillon utilisable pour extraction.
    """
    from seamtech_search.fiches.routes import publier_nouvelle_version

    brouillon = get_brouillon(index, id_brouillon)
    if brouillon["statut"] != "brouillon":
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail=f"Brouillon {id_brouillon} déjà {brouillon['statut']}")

    # Publie comme nouvelle version dans gabarit
    resultat = publier_nouvelle_version(
        index,
        brouillon["code"],
        brouillon["description"],
        brouillon["ancres_detection"],
        brouillon["regles"],
    )

    # Marque brouillon comme valide
    with index.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE gabarit_brouillon SET statut='valide', updated_at=now() WHERE id_brouillon=%s",
                (id_brouillon,),
            )

    return {"id_brouillon": id_brouillon, "gabarit": resultat, "statut": "valide"}

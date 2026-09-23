"""CLI OCR par étages — Lot M.

Entry point installé : ocr-nuit (homogène avec recherche-log, dedup-scan, comptes)

Usage :
  python3 -m seamtech_search.ocr.cli inventaire --dossier … [--json]
  python3 -m seamtech_search.ocr.cli nuit --dossier … --limite N --budget-minutes B [--json]
  python3 -m seamtech_search.ocr.cli rapport --depuis <date> [--json]

Variable d'environnement :
  SEAMTECH_OCR_TRAVAIL_DIR : répertoire de travail (hors archive) où sont
  stockés verrou, état reprenable et rapports. Défaut : data/ocr_travail.

RG13 : AUCUNE écriture dans le dossier source, jamais.
RG14 : AUCUN appel réseau.

Codes de sortie :
  0 : succès
  1 : erreur
  2 : verrou actif (second lancement refusé proprement)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .etat import EtatOCR, VerrouOCR, empreinte_sha256, get_travail_dir, verifier_budget
from .inventaire import inventaire_etage1, inventaire_etage3
from .pipeline import SEUIL_DEFAUT, ocriser_fichier


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ocr-nuit",
        description="Pipeline OCR par étages — Lot M (préparation Lot G).",
    )
    parser.add_argument(
        "--travail-dir",
        default=None,
        help="Répertoire de travail (hors archive) pour verrou, état et rapports. "
        "Défaut : $SEAMTECH_OCR_TRAVAIL_DIR ou data/ocr_travail.",
    )

    sub = parser.add_subparsers(dest="commande", required=True)

    # inventaire
    inv = sub.add_parser("inventaire", help="Inventaire étage 1 et étage 3 (obligatoire avant traitement).")
    inv.add_argument("--dossier", required=True, help="Dossier à inventorier (lecture seule).")
    inv.add_argument("--json", action="store_true", help="Sortie JSON")
    inv.add_argument(
        "--seuil",
        type=int,
        default=SEUIL_DEFAUT,
        help=f"Seuil caractères par page pour considérer une page sans texte (défaut {SEUIL_DEFAUT}).",
    )
    inv.add_argument("--database-url", default=None, help="URL PostgreSQL (optionnel, pour futur rattachement)")

    # nuit
    nuit = sub.add_parser("nuit", help="Exécution nocturne du pipeline OCR (avec verrou, reprise, budget).")
    nuit.add_argument("--dossier", required=True, help="Dossier à traiter (lecture seule).")
    nuit.add_argument("--limite", type=int, default=None, help="Nombre max de fichiers à traiter (pour tests).")
    nuit.add_argument(
        "--budget-minutes",
        type=float,
        default=None,
        help="Budget de temps en minutes (arrêt propre à l'heure dite).",
    )
    nuit.add_argument("--json", action="store_true", help="Sortie JSON")
    nuit.add_argument(
        "--seuil",
        type=int,
        default=SEUIL_DEFAUT,
        help=f"Seuil caractères par page (défaut {SEUIL_DEFAUT}).",
    )
    nuit.add_argument("--langue", default="fra", help="Langue tesseract (défaut fra).")
    nuit.add_argument("--tesseract-command", default="tesseract", help="Binaire tesseract (défaut tesseract).")
    nuit.add_argument(
        "--resolution",
        type=int,
        default=300,
        help="Résolution DPI pour rendu PDF via pdftoppm (défaut 300).",
    )
    nuit.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Pages à traiter (ex: 0,1,2 ou 0-5) — pour tests, défaut toutes.",
    )
    nuit.add_argument("--database-url", default=None, help="URL PostgreSQL pour staging (optionnel).")

    # rapport
    rap = sub.add_parser("rapport", help="Compte rendu des runs précédents.")
    rap.add_argument(
        "--depuis",
        type=str,
        default=None,
        help="Date depuis laquelle lister les rapports (YYYY-MM-DD).",
    )
    rap.add_argument("--json", action="store_true", help="Sortie JSON")

    return parser.parse_args(argv)


def _parse_pages(pages_str: str | None) -> list[int] | None:
    if not pages_str:
        return None
    pages: list[int] = []
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            try:
                debut, fin = part.split("-", 1)
                for i in range(int(debut), int(fin) + 1):
                    pages.append(i)
            except ValueError:
                continue
        else:
            try:
                pages.append(int(part))
            except ValueError:
                continue
    return sorted(set(pages)) if pages else None


def _cmd_inventaire(args: argparse.Namespace) -> int:
    dossier = Path(args.dossier)
    if not dossier.is_dir():
        print(f"Erreur : dossier introuvable : {dossier}", file=sys.stderr)
        return 1

    try:
        etage1 = inventaire_etage1(None, dossier)
        etage3 = inventaire_etage3(None, dossier, seuil_caracteres_par_page=args.seuil)
    except Exception as exc:
        print(f"Erreur inventaire : {exc}", file=sys.stderr)
        return 1

    rapport = {
        "dossier": str(dossier.resolve()),
        "seuil": args.seuil,
        "etage1": etage1,
        "etage3": etage3,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        print("=== Inventaire OCR par étages ===")
        print(f"Dossier : {rapport['dossier']}")
        print(f"Seuil : {args.seuil} caractères/page")
        print("")
        print("--- Étage 1 : inventaire rapide ---")
        print(f"Fichiers : {etage1['fichiers']}")
        print(f"Tailles : {etage1['tailles_octets']} octets")
        print(f"Extensions : {etage1['extensions']}")
        print(f"Par année : {etage1.get('par_annee', {})}")
        print(f"Par dossier : {etage1.get('par_dossier', {})}")
        print("")
        print("--- Étage 3 : fichiers scannés (nécessitant OCR) ---")
        print(f"Fichiers scannés : {etage3['fichiers_scannes']}")
        print(f"Fichiers texte natif : {etage3['fichiers_texte_natif']}")
        print(f"Pages à océriser : {etage3['pages_a_oceriser']}")
        print(f"Pages texte natif : {etage3['pages_texte_natif']}")
        print(f"Débit hypothèse (dimensionnement) : {etage3['debit_mesure_pages_par_minute']} pages/min — mesures réelles CI 102,899/104,176 p/min tesseract 5.3.4")
        print(f"Estimation durée : {etage3['estimation_duree_s']:.1f}s ({etage3['estimation_duree_min']} min) avec hypothèse 30 p/min")
        print(f"Formule : {etage3['formule_estimation']}")
        print(f"Mesures réelles CI : 102,899 p/min et 104,176 p/min (voir docs/OCR_ETAGES.md)")
        print("")
        print("Détails (premiers 20) :")
        for det in etage3["details"][:20]:
            print(f"  {det['fichier']} : {det['pages_a_oceriser']} page(s) à océriser / {det['nb_pages']} total")

    return 0


def _lister_fichiers_ocr(dossier: Path) -> list[Path]:
    """Liste les fichiers éligibles OCR (PDF + images)."""
    from .inventaire import OCR_IMAGE_EXTENSIONS

    eligibles: list[Path] = []
    for chemin in sorted(dossier.rglob("*")):
        if not chemin.is_file():
            continue
        if chemin.name.startswith("."):
            continue
        ext = chemin.suffix.lower()
        if ext == ".pdf" or ext in OCR_IMAGE_EXTENSIONS:
            eligibles.append(chemin)
    return eligibles


def _cmd_nuit(args: argparse.Namespace) -> int:
    dossier = Path(args.dossier)
    if not dossier.is_dir():
        print(f"Erreur : dossier introuvable : {dossier}", file=sys.stderr)
        return 1

    travail_dir = get_travail_dir(args.travail_dir)
    verrou = VerrouOCR(travail_dir)

    # Tente d'acquérir le verrou
    if not verrou.acquire():
        print(
            f"Un run OCR est déjà en cours (verrou {verrou.lock_path}). "
            "Second lancement refusé proprement — pas de double travail.",
            file=sys.stderr,
        )
        # Code de sortie distinct (2) comme exigé
        return 2

    etat = EtatOCR(travail_dir)
    debut_run = time.time()
    pages_spec = _parse_pages(args.pages)

    fichiers = _lister_fichiers_ocr(dossier)
    if args.limite is not None:
        fichiers = fichiers[: args.limite]

    # Stats du run
    stats = {
        "dossier": str(dossier.resolve()),
        "travail_dir": str(travail_dir),
        "debut": datetime.now(timezone.utc).isoformat(),
        "fichiers_examines": 0,
        "fichiers_traites": 0,
        "fichiers_ignores_deja_traites": 0,
        "pages_ocerisees": 0,
        "pages_ignorees_texte_natif": 0,
        "echecs": 0,
        "duree_totale_s": 0.0,
        "taille_texte_produit": 0,
        "moteur": "",
        "version_moteur": "",
        "seuil": args.seuil,
        "langue": args.langue,
        "budget_minutes": args.budget_minutes,
        "limite": args.limite,
        "fichiers": [],
    }

    moteur_version: str | None = None

    try:
        for chemin in fichiers:
            # Vérifie budget
            depasse, ecoule = verifier_budget(debut_run, args.budget_minutes)
            if depasse:
                print(
                    f"Budget de {args.budget_minutes} min dépassé ({ecoule:.1f}s écoulés) — arrêt propre.",
                    file=sys.stderr,
                )
                stats["arret_motif"] = f"budget {args.budget_minutes} min dépassé"
                break

            # Empreinte SHA-256 (idempotence, jamais mtime seul)
            try:
                emp = empreinte_sha256(chemin)
            except OSError as exc:
                print(f"Impossible de lire {chemin} : {exc}", file=sys.stderr)
                stats["echecs"] += 1
                stats["fichiers"].append(
                    {
                        "fichier": str(chemin),
                        "empreinte": "",
                        "statut": "echec_lecture",
                        "motif": str(exc),
                    }
                )
                continue

            stats["fichiers_examines"] += 1

            if etat.est_deja_traite(emp):
                stats["fichiers_ignores_deja_traites"] += 1
                stats["fichiers"].append(
                    {
                        "fichier": str(chemin),
                        "empreinte": emp,
                        "statut": "deja_traite",
                    }
                )
                continue

            # OCR
            try:
                res = ocriser_fichier(
                    chemin,
                    seuil=args.seuil,
                    langue=args.langue,
                    timeout_par_page_s=30,
                    tesseract_command=args.tesseract_command,
                    resolution_dpi=args.resolution,
                    pages=pages_spec,
                )
            except Exception as exc:
                print(f"Erreur OCR {chemin} : {exc}", file=sys.stderr)
                stats["echecs"] += 1
                stats["fichiers"].append(
                    {
                        "fichier": str(chemin),
                        "empreinte": emp,
                        "statut": "echec_ocr",
                        "motif": str(exc),
                    }
                )
                continue

            # Mise à jour stats
            stats["fichiers_traites"] += 1
            stats["pages_ocerisees"] += res["nb_pages_ocerisees"]
            stats["pages_ignorees_texte_natif"] += res["nb_pages_ignorees_texte_natif"]
            stats["echecs"] += res["nb_echecs"]
            stats["taille_texte_produit"] += res["taille_texte_ocr"]
            if res["version_moteur"] and not moteur_version:
                moteur_version = res["version_moteur"]
                stats["moteur"] = res["moteur"]
                stats["version_moteur"] = res["version_moteur"]

            stats["fichiers"].append(
                {
                    "fichier": str(chemin),
                    "empreinte": emp,
                    "statut": "traite",
                    "nb_pages": res["nb_pages"],
                    "nb_pages_ocerisees": res["nb_pages_ocerisees"],
                    "nb_pages_ignorees": res["nb_pages_ignorees_texte_natif"],
                    "nb_echecs": res["nb_echecs"],
                    "duree_s": res["duree_s"],
                    "taille_texte": res["taille_texte_ocr"],
                }
            )

            # Marque comme traité (idempotence par empreinte)
            etat.marquer_traite(
                emp,
                str(chemin),
                res["nb_pages"],
                res["nb_pages_ocerisees"],
                res["duree_s"],
            )

            # Sauvegarde intermédiaire de l'état déjà faite dans marquer_traite

            # Optionnel : stockage en base si URL fournie
            if args.database_url:
                try:
                    from pathlib import Path as _Path

                    from seamtech_search.indexer import SearchIndex

                    index = SearchIndex(database_path=_Path(":memory:"), database_url=args.database_url)
                    index.initialize()
                    index.run_migrations()
                    # Stocke chaque page
                    with index.connect() as conn:
                        with conn.cursor() as cur:
                            for page_res in res["pages"]:
                                if page_res["page_ocerisee"]:
                                    cur.execute(
                                        """
                                        INSERT INTO ocr_etage3
                                        (fichier_source, empreinte_sha256, page, texte_ocr, confiance,
                                         moteur, version_moteur, duree_s, page_ocerisee, motif)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                        """,
                                        (
                                            str(chemin),
                                            emp,
                                            page_res["page"],
                                            page_res["texte_ocr"],
                                            page_res["confiance"],
                                            page_res["moteur"],
                                            page_res["version_moteur"],
                                            page_res["duree_s"],
                                            page_res["page_ocerisee"],
                                            page_res["motif"],
                                        ),
                                    )
                        conn.commit()
                    index.close()
                except Exception as exc:
                    print(f"Stockage base échoué pour {chemin} : {exc}", file=sys.stderr)
                    # Non bloquant : le staging est optionnel, le run continue

    except KeyboardInterrupt:
        print("Interruption (Ctrl-C) — arrêt propre, état sauvegardé.", file=sys.stderr)
        stats["arret_motif"] = "interruption Ctrl-C"
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Erreur inattendue : {exc}", file=sys.stderr)
        stats["arret_motif"] = f"erreur: {exc}"
        # On continue vers la sauvegarde du rapport et libération du verrou
    finally:
        # Finalise stats
        stats["fin"] = datetime.now(timezone.utc).isoformat()
        stats["duree_totale_s"] = time.time() - debut_run
        if stats["duree_totale_s"] > 0:
            stats["debit_pages_par_minute"] = (
                stats["pages_ocerisees"] / (stats["duree_totale_s"] / 60)
                if stats["pages_ocerisees"] > 0
                else 0.0
            )
        else:
            stats["debit_pages_par_minute"] = 0.0

        # Écrit rapport JSON + texte
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rapport_json_path = travail_dir / "rapports" / f"ocr_rapport_{timestamp}.json"
        rapport_txt_path = travail_dir / "rapports" / f"ocr_rapport_{timestamp}.txt"

        try:
            rapport_json_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"Impossible d'écrire rapport JSON {rapport_json_path} : {exc}", file=sys.stderr)

        try:
            # Rapport texte lisible
            lignes = []
            lignes.append("=== Compte rendu OCR nocturne ===")
            lignes.append(f"Dossier source : {stats['dossier']}")
            lignes.append(f"Travail dir : {stats['travail_dir']}")
            lignes.append(f"Début : {stats['debut']}")
            lignes.append(f"Fin : {stats['fin']}")
            lignes.append(f"Durée totale : {stats['duree_totale_s']:.1f}s")
            lignes.append(f"Fichiers examinés : {stats['fichiers_examines']}")
            lignes.append(f"Fichiers traités : {stats['fichiers_traites']}")
            lignes.append(f"Fichiers déjà traités (idempotence) : {stats['fichiers_ignores_deja_traites']}")
            lignes.append(f"Pages océrisées : {stats['pages_ocerisees']}")
            lignes.append(f"Pages ignorées (texte natif présent) : {stats['pages_ignorees_texte_natif']}")
            lignes.append(f"Échecs : {stats['echecs']}")
            lignes.append(f"Taille texte produit : {stats['taille_texte_produit']} caractères")
            lignes.append(f"Débit mesuré : {stats['debit_pages_par_minute']:.1f} pages/min")
            lignes.append(f"Moteur : {stats['moteur']} {stats['version_moteur']}")
            lignes.append(f"Seuil : {stats['seuil']} caractères/page")
            lignes.append(f"Langue : {stats['langue']}")
            lignes.append(f"Budget : {stats['budget_minutes']} min")
            if "arret_motif" in stats:
                lignes.append(f"Arrêt : {stats['arret_motif']}")
            lignes.append("")
            lignes.append("Fichiers :")
            for f in stats["fichiers"]:
                lignes.append(f"  - {f['fichier']} : {f.get('statut','')} "
                              f"({f.get('nb_pages_ocerisees',0)} océrisées, "
                              f"{f.get('nb_pages_ignorees',0)} ignorées, "
                              f"{f.get('nb_echecs',0)} échecs)")

            rapport_txt_path.write_text("\n".join(lignes), encoding="utf-8")
        except OSError as exc:
            print(f"Impossible d'écrire rapport texte {rapport_txt_path} : {exc}", file=sys.stderr)

        # Sauvegarde dernière exécution dans état
        try:
            etat.set_derniere_execution(stats)
        except (OSError, ValueError, RuntimeError) as exc:
            # état non critique, on log et continue
            print(f"Avertissement état: {exc}", file=sys.stderr)
            _ = exc

        # Libère verrou
        verrou.release()

    # Sortie
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print("=== Run OCR terminé ===")
        print(f"Fichiers examinés : {stats['fichiers_examines']}")
        print(f"Fichiers traités : {stats['fichiers_traites']}")
        print(f"Déjà traités : {stats['fichiers_ignores_deja_traites']}")
        print(f"Pages océrisées : {stats['pages_ocerisees']}")
        print(f"Pages ignorées (texte natif) : {stats['pages_ignorees_texte_natif']}")
        print(f"Échecs : {stats['echecs']}")
        print(f"Durée : {stats['duree_totale_s']:.1f}s")
        print(f"Débit : {stats['debit_pages_par_minute']:.1f} pages/min")
        print(f"Texte produit : {stats['taille_texte_produit']} caractères")
        print(f"Rapports : {rapport_json_path} et {rapport_txt_path}")

    return 0


def _cmd_rapport(args: argparse.Namespace) -> int:
    travail_dir = get_travail_dir(args.travail_dir)
    rapports_dir = travail_dir / "rapports"

    if not rapports_dir.is_dir():
        print(f"Aucun rapport trouvé dans {rapports_dir}", file=sys.stderr)
        return 0

    fichiers = sorted(rapports_dir.glob("ocr_rapport_*.json"), reverse=True)

    # Filtre par date si demandé
    if args.depuis:
        try:
            depuis_dt = datetime.fromisoformat(args.depuis)
            if depuis_dt.tzinfo is None:
                depuis_dt = depuis_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                depuis_dt = datetime.strptime(args.depuis, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                print(f"Format date invalide --depuis : {args.depuis} (attendu YYYY-MM-DD ou ISO)", file=sys.stderr)
                return 1

        filtres = []
        for f in fichiers:
            try:
                # Essaie de lire le JSON pour avoir la date de début
                data = json.loads(f.read_text(encoding="utf-8"))
                debut_str = data.get("debut", "")
                debut_dt = datetime.fromisoformat(debut_str)
                if debut_dt >= depuis_dt:
                    filtres.append(f)
            except Exception:
                # Si illisible, garde si mtime >= depuis
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                    if mtime >= depuis_dt:
                        filtres.append(f)
                except OSError:
                    continue
        fichiers = filtres

    if not fichiers:
        print(f"Aucun rapport depuis {args.depuis} dans {rapports_dir}")
        return 0

    if args.json:
        rapports = []
        for f in fichiers:
            try:
                rapports.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        print(json.dumps(rapports, ensure_ascii=False, indent=2))
    else:
        print(f"=== Rapports OCR (depuis {args.depuis or 'début'}) ===")
        print(f"Travail dir : {travail_dir}")
        print(f"{len(fichiers)} rapport(s) trouvé(s) :")
        for f in fichiers[:20]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                print(
                    f"  {f.name} : {data.get('debut','?')} — "
                    f"{data.get('fichiers_traites',0)} fichiers, "
                    f"{data.get('pages_ocerisees',0)} pages océrisées, "
                    f"{data.get('duree_totale_s',0):.1f}s, "
                    f"{data.get('debit_pages_par_minute',0):.1f} p/min"
                )
            except Exception:
                print(f"  {f.name} : (illisible)")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.commande == "inventaire":
        return _cmd_inventaire(args)
    elif args.commande == "nuit":
        return _cmd_nuit(args)
    elif args.commande == "rapport":
        return _cmd_rapport(args)
    else:
        print(f"Commande inconnue : {args.commande}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

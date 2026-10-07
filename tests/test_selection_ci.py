"""Garde-fou de la SÉLECTION des tests en CI (correctif R-14, audit du 25/09/2026).

Le problème corrigé, en une phrase : la CI excluait les tests par **sous-chaîne
de leur nom** (``pytest -k "not postgres and not s3"``), si bien que 33 tests
qui n'exigent aucun service — chaos S3 sur doubles, grammaire SQL PostgreSQL
vérifiée hors serveur, routes qui répondent 503 *sans* PostgreSQL… — n'étaient
exécutés **nulle part**, ni dans la suite SQLite, ni dans la mesure de
couverture. Un test qui ne s'exécute jamais ne prouve rien ; pire, il donne
l'illusion d'une protection.

La règle désormais :

1. **un test qui a besoin d'un service le DÉCLARE par un marqueur**
   (``postgres``, ``s3``, ``sauvegarde``, ``perf``) ;
2. **la CI sélectionne par marqueur** (``-m``), jamais par sous-chaîne pour une
   catégorie ;
3. tous les jobs dédiés sélectionnent eux aussi **par marqueurs ``-m``** ;
4. aucune sélection ``-k`` ni sélection par chemin/nodeid n'est autorisée dans
   les commandes pytest du workflow.

Ce fichier ne lance aucun service et n'ouvre aucune connexion réseau (RG14) :
il lit le workflow, analyse les fichiers de tests en AST, et interroge pytest
en mode ``--collect-only`` (collecte seule, aucun test exécuté).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
CI = RACINE / ".github" / "workflows" / "ci.yml"
DOSSIER_TESTS = RACINE / "tests"

#: Les SEULS tests qui exigent un stockage objet VIVANT (MinIO/S3 réel).
#: Tout ce qui est ici doit porter ``@pytest.mark.s3`` ; rien d'autre ne doit le
#: porter. Un test sur doubles (boto3 remplacé par un ``MagicMock``) n'est PAS
#: un test S3 : le marquer reviendrait à le cacher de la CI, ce qui est
#: exactement le défaut que ce fichier corrige.
INVENTAIRE_S3_REEL = {
    # job `integration` : compose complet (PostgreSQL + Redis + MinIO), appelle
    # ensure_bucket_exists / upload_file / get_presigned_url / delete_file.
    "tests/test_integration_docker.py::test_docker_compose_infra",
    "tests/test_integration_docker.py::test_api_with_real_backends",
    # job `integration` : deux CONTENEURS séparés (web + worker) sur la même
    # pile, MinIO vivant pour l'envoi des documents et le téléchargement du
    # rapport généré. C'est la preuve que le worker distinct partage réellement
    # les volumes (fichiers téléversés, brouillons, quarantaine, modèles).
    "tests/test_compose_partage_worker.py::test_web_et_worker_partagent_volumes_et_fichiers",
    # job `integration` : identités RESTREINTES — l'identité applicative
    # écrit/lit SON bucket, et se voit refuser un autre bucket, toute opération
    # d'administration et l'accès anonyme ; `web`/`worker` reçoivent bien cette
    # identité (jamais MINIO_ROOT_*) ; identifiants absents ⇒ échec clair de la
    # composition ; aucun secret dans les journaux. Correctif du défaut bloquant
    # de la revue du 2026-10-07.
    "tests/test_credentials_s3_restreintes.py::test_identite_applicative_autorisee_sur_son_bucket_et_refusee_ailleurs",
    "tests/test_credentials_s3_restreintes.py::test_conteneurs_web_et_worker_recoivent_l_identite_applicative_et_pas_le_root",
    "tests/test_credentials_s3_restreintes.py::test_identifiants_applicatifs_manquants_echec_clair_de_la_composition",
    "tests/test_credentials_s3_restreintes.py::test_aucun_secret_dans_les_journaux_des_services",
    # job `sauvegarde` : aller-retour hors-site contre un VRAI bucket.
    "tests/test_sauvegarde_restauration.py::test_aller_retour_via_client_s3_reel",
    # épreuve de conformité d'un endpoint réel (SEAMTECH_TEST_S3_URL).
    "tests/test_storage.py::test_live_minio_s3_integration",
    # job `recette-corpus-reel` (Lot 2) : recette complète du corpus réel —
    # MinIO vivant pour l'envoi des documents, les URL présignées (302, 900 s)
    # et la sauvegarde hors-site. Sélection par marqueur recette_corpus,
    # garde d'exécution SEAMTECH_RECETTE_CORPUS.
    "tests/test_recette_corpus_reel.py::test_01_zip_intacts_avant_recette",
    "tests/test_recette_corpus_reel.py::test_02_corpus_extrait_complet",
    "tests/test_recette_corpus_reel.py::test_03_services_et_migrations_018_base_neuve",
    "tests/test_recette_corpus_reel.py::test_04a_import_flux_reel_ref001_via_upload",
    "tests/test_recette_corpus_reel.py::test_04b_import_flux_reel[REF-002]",
    "tests/test_recette_corpus_reel.py::test_04b_import_flux_reel[REF-003]",
    "tests/test_recette_corpus_reel.py::test_04b_import_flux_reel[REF-004]",
    "tests/test_recette_corpus_reel.py::test_04b_import_flux_reel[REF-005]",
    "tests/test_recette_corpus_reel.py::test_04b_import_flux_reel[REF-006]",
    "tests/test_recette_corpus_reel.py::test_04b_import_flux_reel[REF-007]",
    "tests/test_recette_corpus_reel.py::test_04c_depot_fiche_lot_c[REF-001]",
    "tests/test_recette_corpus_reel.py::test_04c_depot_fiche_lot_c[REF-002]",
    "tests/test_recette_corpus_reel.py::test_04c_depot_fiche_lot_c[REF-003]",
    "tests/test_recette_corpus_reel.py::test_04c_depot_fiche_lot_c[REF-004]",
    "tests/test_recette_corpus_reel.py::test_04c_depot_fiche_lot_c[REF-005]",
    "tests/test_recette_corpus_reel.py::test_04c_depot_fiche_lot_c[REF-006]",
    "tests/test_recette_corpus_reel.py::test_04c_depot_fiche_lot_c[REF-007]",
    "tests/test_recette_corpus_reel.py::test_05_fiches_proposees_a_valider",
    "tests/test_recette_corpus_reel.py::test_06_ocr_par_etages",
    "tests/test_recette_corpus_reel.py::test_07_recherches_par_mots_cles",
    "tests/test_recette_corpus_reel.py::test_08_ouverture_pdf_telechargeable_sans_redirection_interne",
    "tests/test_recette_corpus_reel.py::test_09_rapport_pdf_telecharge",
    "tests/test_recette_corpus_reel.py::test_10_sauvegarde_restauration_base_neuve",
    "tests/test_recette_corpus_reel.py::test_11_zip_inchanges_apres_recette",
}

#: Variables d'environnement qui, LUES par un test, désignent un endpoint réel.
VARIABLES_ENDPOINT_REEL = ("SEAMTECH_TEST_S3_URL", "SEAMTECH_S3_ENDPOINT_URL")

#: Catégories qui ne doivent JAMAIS être exclues par sous-chaîne de nom.
CATEGORIES = ("postgres", "s3", "perf", "sauvegarde")

#: Commandes de sélection attendues dans le workflow (contrat explicite : si
#: quelqu'un les rétrograde vers `-k`, ce test devient rouge).
COMMANDE_SUITE_SANS_SERVICE = 'pytest -q -rf -m "not postgres and not s3 and not perf and not redis_queue"'
COMMANDE_COUVERTURE = 'pytest -m "not s3 and not perf"'
COMMANDE_POSTGRES = 'pytest -m "postgres and not perf and not sauvegarde and not redis_queue"'
#: File durable (Lot « imports durables ») : Redis réel + worker réel. Marquée
#: à part pour que sa CI ne puisse pas être verte par simple SKIP.
COMMANDE_FILE_DURABLE = 'pytest -m "redis_queue" -v -rf'
COMMANDE_PERF = "pytest -q -m perf -rf"


# ---------------------------------------------------------------------------
# Outils : collecte pytest (aucun test exécuté) et analyse AST
# ---------------------------------------------------------------------------


def _collecter(*arguments: str) -> set[str]:
    """Identifiants des tests collectés (``--collect-only``), normalisés."""
    processus = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly", *arguments],
        capture_output=True,
        text=True,
        cwd=str(RACINE),
        timeout=300,
    )
    assert processus.returncode == 0, f"collecte impossible ({arguments}) :\n{processus.stdout}\n{processus.stderr}"
    return {ligne.strip() for ligne in processus.stdout.splitlines() if "::" in ligne and not ligne.startswith(" ")}


def _commandes_pytest_du_workflow() -> list[str]:
    """Toutes les lignes du workflow qui lancent pytest (commentaires exclus)."""
    lignes = []
    for ligne in CI.read_text(encoding="utf-8").splitlines():
        nue = ligne.strip()
        if nue.startswith("#"):
            continue
        if re.match(r"^(python -m )?pytest\s", nue):
            lignes.append(nue)
    return lignes


def _marqueurs_par_test() -> dict[str, set[str]]:
    """{'tests/fichier.py::test_x': {'marqueur', ...}} par lecture AST.

    Lecture statique volontaire : elle voit ce qu'un relecteur voit dans le
    fichier, sans importer le module ni exécuter quoi que ce soit.
    """

    def noms_marqueurs(noeud: ast.AST) -> set[str]:
        trouves: set[str] = set()
        for sous in ast.walk(noeud):
            if isinstance(sous, ast.Attribute) and isinstance(sous.value, ast.Attribute):
                if sous.value.attr == "mark" and getattr(sous.value.value, "id", "") == "pytest":
                    trouves.add(sous.attr)
        return trouves

    resultat: dict[str, set[str]] = {}
    for fichier in sorted(DOSSIER_TESTS.glob("test_*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
        module = set()
        for noeud in arbre.body:
            if isinstance(noeud, ast.Assign) and any(
                isinstance(cible, ast.Name) and cible.id == "pytestmark" for cible in noeud.targets
            ):
                module |= noms_marqueurs(noeud.value)
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef) and noeud.name.startswith("test_"):
                identifiant = f"tests/{fichier.name}::{noeud.name}"
                propres = set()
                for decorateur in noeud.decorator_list:
                    propres |= noms_marqueurs(decorateur)
                resultat[identifiant] = module | propres
    return resultat


# ---------------------------------------------------------------------------
# 1. Les tests qui exigent un stockage objet vivant sont marqués — et eux seuls
# ---------------------------------------------------------------------------


def test_les_tests_marques_s3_sont_exactement_ceux_qui_exigent_un_service_reel() -> None:
    """``-m s3`` doit rendre EXACTEMENT l'inventaire déclaré ci-dessus.

    Deux échecs possibles, deux messages distincts : un test S3 réel oublié
    (il tournerait dans une suite sans MinIO et échouerait en CI), ou un test
    sur doubles marqué à tort (il serait caché de la CI — le défaut R-14).
    """
    collectes = _collecter("-m", "s3")
    oublies = INVENTAIRE_S3_REEL - collectes
    en_trop = collectes - INVENTAIRE_S3_REEL
    assert not oublies, f"marqueur s3 manquant sur : {sorted(oublies)}"
    assert not en_trop, (
        "tests marqués s3 mais absents de l'inventaire : "
        f"{sorted(en_trop)} — un test sur doubles NE DOIT PAS être marqué s3 "
        "(il serait exclu de la CI sans jamais être exécuté ailleurs)"
    )


def _vise_un_service_reel(fichier: Path) -> str | None:
    """Motif d'un accès à un service RÉEL dans ce fichier, sinon ``None``.

    Détection par AST, pas par ``grep`` : un fichier qui *mentionne* « docker
    compose up » dans un commentaire (les tests statiques du compose le font)
    ne vise aucun service. Ce qui compte, c'est (a) lire une variable
    d'environnement qui désigne un endpoint, ou (b) lancer réellement la
    commande ``docker``.
    """
    def est_environnement(noeud: ast.AST) -> bool:
        """Vrai pour ``os.environ`` / ``environ`` (et pas pour un dict quelconque)."""
        if isinstance(noeud, ast.Attribute):
            return noeud.attr == "environ"
        return isinstance(noeud, ast.Name) and noeud.id == "environ"

    arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Call):
            fonction = noeud.func
            nom = fonction.attr if isinstance(fonction, ast.Attribute) else getattr(fonction, "id", "")
            # (a) os.environ.get("SEAMTECH_…") / os.getenv("SEAMTECH_…")
            lecture_env = nom == "getenv" or (
                nom == "get" and isinstance(fonction, ast.Attribute) and est_environnement(fonction.value)
            )
            if lecture_env:
                for argument in noeud.args:
                    if isinstance(argument, ast.Constant) and argument.value in VARIABLES_ENDPOINT_REEL:
                        return f"lit {argument.value}"
            # (b) subprocess.run(["docker", ...]) / Popen(["docker", ...])
            if nom in {"run", "check_output", "check_call", "Popen"} and noeud.args:
                premier = noeud.args[0]
                if isinstance(premier, ast.List) and premier.elts:
                    tete = premier.elts[0]
                    if isinstance(tete, ast.Constant) and tete.value == "docker":
                        return "lance la commande docker"
        # (c) os.environ["SEAMTECH_…"] en LECTURE (une écriture ne vise rien)
        if isinstance(noeud, ast.Subscript) and isinstance(noeud.ctx, ast.Load):
            if est_environnement(noeud.value) and isinstance(noeud.slice, ast.Constant):
                if noeud.slice.value in VARIABLES_ENDPOINT_REEL:
                    return f"lit {noeud.slice.value}"
    return None


def test_tout_fichier_qui_vise_un_stockage_reel_a_un_test_dans_l_inventaire() -> None:
    """Un nouveau fichier qui parle à un vrai endpoint doit être classé ici.

    Le garde-fou est volontairement bruyant : il échoue tant que l'auteur n'a
    pas décidé, explicitement, si son test exige un service (marqueur ``s3`` +
    inventaire) ou non.
    """
    fichiers_inventaire = {identifiant.split("::")[0] for identifiant in INVENTAIRE_S3_REEL}
    for fichier in sorted(DOSSIER_TESTS.glob("test_*.py")):
        if fichier.name == Path(__file__).name:
            continue  # le garde-fou lui-même cite ces noms : c'est son objet
        motif = _vise_un_service_reel(fichier)
        if motif is None:
            continue
        chemin = f"tests/{fichier.name}"
        assert chemin in fichiers_inventaire, (
            f"{chemin} s'adresse à un service réel ({motif}) mais aucun de ses tests "
            "n'est dans INVENTAIRE_S3_REEL : marquer @pytest.mark.s3 et compléter "
            "l'inventaire, ou retirer l'accès au service réel."
        )


def test_les_tests_sur_doubles_ne_portent_pas_le_marqueur_s3() -> None:
    """Contrôle nominatif : ces tests parlent à boto3 DOUBLÉ, ils doivent tourner partout."""
    marqueurs = _marqueurs_par_test()
    sur_doubles = [
        "tests/test_chaos.py::test_chaos_s3_down_mid_import",
        "tests/test_chaos.py::test_chaos_s3_versioning_unavailable",
        "tests/test_storage.py::test_s3_storage_client_configuration",
        "tests/test_storage.py::test_s3_storage_upload_and_presigned_url",
        "tests/test_api_extra_gate.py::test_api_open_with_s3_object_key",
        "tests/test_sauvegarde_unites.py::test_retention_symetrique_locale_et_s3",
    ]
    for identifiant in sur_doubles:
        assert identifiant in marqueurs, f"{identifiant} a disparu : ne jamais supprimer un test existant"
        assert "s3" not in marqueurs[identifiant], f"{identifiant} n'a pas besoin d'un MinIO : le marqueur le cacherait"


# ---------------------------------------------------------------------------
# 2. La CI sélectionne par marqueur, pas par sous-chaîne
# ---------------------------------------------------------------------------


def test_aucune_commande_ci_n_exclut_une_categorie_par_sous_chaine() -> None:
    """Compatibilité du nodeid historique; la règle est maintenant stricte : ``-m`` seulement."""
    commandes = _commandes_pytest_du_workflow()
    assert commandes, "aucune commande pytest détectée dans le workflow"
    sans_marqueur = [commande for commande in commandes if not re.search(r"(?:^|\s)-m(?:\s|$)", commande)]
    selecteurs_interdits = [commande for commande in commandes if re.search(r"(?:^|\s)(?:-k|tests/test_[^\s]+)", commande)]
    assert not sans_marqueur, "sélection CI sans -m :\n" + "\n".join(f"  {c}" for c in sans_marqueur)
    assert not selecteurs_interdits, "sélection CI par -k/chemin au lieu de -m :\n" + "\n".join(
        f"  {c}" for c in selecteurs_interdits
    )
    assert re.search(r'"-m",\s*"sauvegarde"', CI.read_text(encoding="utf-8")), (
        "la commande collect-only du garde sauvegarde doit utiliser le même marqueur"
    )


def test_les_commandes_de_selection_attendues_sont_bien_celles_du_workflow() -> None:
    """Contrat explicite des 5 sélections : sans service, couverture, PostgreSQL, file durable, perf."""
    contenu = CI.read_text(encoding="utf-8")
    for commande in (
        COMMANDE_SUITE_SANS_SERVICE,
        COMMANDE_COUVERTURE,
        COMMANDE_POSTGRES,
        COMMANDE_FILE_DURABLE,
        COMMANDE_PERF,
    ):
        assert commande in contenu, f"commande de sélection absente du workflow : {commande}"


def test_garde_collect_only_parse_le_nombre_selectionne_par_marqueur() -> None:
    """pytest affiche ``23/1007`` sous -m; la garde doit lire 23 et non le total."""
    motif = r"(?m)^(\d+)(?:/\d+)?\s+tests?\s+collected"
    for sortie, attendu in (("23/1007 tests collected (984 deselected)", 23), ("23 tests collected", 23)):
        correspondance = re.search(motif, sortie)
        assert correspondance is not None
        assert int(correspondance.group(1)) == attendu
    assert motif in CI.read_text(encoding="utf-8")


def test_les_jobs_dedies_selectionnent_par_chemin_et_ne_sont_pas_ampute_par_le_marqueur() -> None:
    """Nodeid historique conservé; les jobs dédiés emploient maintenant -m eux aussi."""
    contenu = CI.read_text(encoding="utf-8")
    assert "pytest -m integration_docker -v" in contenu
    assert "python -m pytest -m sauvegarde -v --junitxml=sauvegarde-junit.xml" in contenu
    assert "pytest -m recette_corpus -v" in contenu
    assert "pytest -m ocr_suite -v" in contenu
    for commande in _commandes_pytest_du_workflow():
        assert re.search(r"(?:^|\s)-m(?:\s|$)", commande), f"sélection hors marqueur : {commande}"
        assert not re.search(r"(?:^|\s)(?:-k|tests/test_[^\s]+)", commande), f"sélection hors marqueur : {commande}"


# ---------------------------------------------------------------------------
# 3. Aucun test n'est silencieusement désélectionné
# ---------------------------------------------------------------------------


def test_aucun_test_nomme_s3_n_est_silencieusement_ecarte_de_la_ci() -> None:
    """Tout test dont le NOM contient « s3 » tourne quelque part en CI.

    Trois destinations légitimes : la suite sans service (marqueurs), l'étape
    PostgreSQL (marqueur ``postgres``), ou un job dédié (inventaire S3 réel).
    Une quatrième destination — nulle part — est précisément ce que R-14
    laissait passer.
    """
    tous = _collecter()
    suite_sans_service = _collecter("-m", "not postgres and not s3 and not perf")
    marqueurs = _marqueurs_par_test()

    orphelins = []
    for identifiant in sorted(tous):
        if "s3" not in identifiant.lower():
            continue
        if identifiant in suite_sans_service or identifiant in INVENTAIRE_S3_REEL:
            continue
        marques = marqueurs.get(identifiant.split("[")[0], set())
        if {"postgres", "perf"} & marques:
            continue
        orphelins.append(identifiant)
    assert not orphelins, f"tests exécutés nulle part en CI : {orphelins}"


def test_les_tests_autrefois_caches_par_le_filtre_sont_de_nouveau_executes() -> None:
    """Non-régression nominative : les 10 tests que ``-k "not s3"`` masquait.

    Mesure du 25/09/2026 (avant correctif) : ``pytest -k "s3 and not postgres
    and not perf"`` → 10 passed, 1 skipped, alors que la CI ne les exécutait
    pas. Ils doivent maintenant faire partie de la suite sans service.
    """
    suite_sans_service = _collecter("-m", "not postgres and not s3 and not perf")
    autrefois_caches = [
        "tests/test_api_extra_gate.py::test_api_imports_with_redis_and_s3_and_rate_limit",
        "tests/test_api_extra_gate.py::test_api_open_with_s3_object_key",
        "tests/test_chaos.py::test_chaos_s3_down_mid_import",
        "tests/test_chaos.py::test_chaos_s3_versioning_unavailable",
        "tests/test_sauvegarde_unites.py::test_retention_symetrique_locale_et_s3",
        "tests/test_storage.py::test_s3_storage_client_configuration",
        "tests/test_storage.py::test_s3_storage_upload_and_presigned_url",
        "tests/test_storage.py::test_s3_storage_upload_bytes",
        "tests/test_storage.py::test_s3_storage_nonexistent_file_raises",
        "tests/test_storage.py::test_import_with_s3_storage_uploads_all_files",
    ]
    manquants = [identifiant for identifiant in autrefois_caches if identifiant not in suite_sans_service]
    assert not manquants, f"de nouveau exclus de la CI : {manquants}"


def test_la_selection_par_marqueur_ne_perd_aucun_test_de_l_ancienne_selection() -> None:
    """La bascule ``-k`` → ``-m`` n'a retiré aucun test : elle en a AJOUTÉ.

    C'est la preuve mesurable de l'innocuité du correctif : l'ancienne
    sélection est strictement incluse dans la nouvelle.
    """
    ancienne = _collecter("-k", "not postgres and not s3")
    nouvelle = _collecter("-m", "not postgres and not s3 and not perf")
    perdus = ancienne - nouvelle
    assert not perdus, f"la nouvelle sélection perd des tests : {sorted(perdus)[:10]}"
    assert len(nouvelle) > len(ancienne), "la nouvelle sélection doit exécuter STRICTEMENT plus de tests"


@pytest.mark.parametrize("categorie", ["postgres", "s3", "perf", "sauvegarde"])
def test_les_marqueurs_de_categorie_sont_declares_dans_pyproject(categorie: str) -> None:
    """Un marqueur non déclaré serait une faute silencieuse (warning, pas erreur)."""
    pyproject = (RACINE / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"{categorie}:' in pyproject, f"marqueur {categorie} non déclaré dans [tool.pytest.ini_options].markers"


# ---------------------------------------------------------------------------
# Contrats de la vérification PLEINE PILE hors ligne (revue du 2026-10-07)
# ---------------------------------------------------------------------------


def test_le_job_hors_ligne_bloque_reellement_la_sortie_et_exige_le_controle_negatif() -> None:
    """« Zéro paquet rejeté » ne prouve RIEN sans contrôle négatif préalable.

    Le garde socket du diagnostic Python ne couvre ni le navigateur, ni Next.js,
    ni le worker séparé (constat n°4). Le job CI doit donc :

    1. bloquer la sortie du COMPTE APPLICATIF (règle ``--uid-owner``) ;
    2. exiger qu'une requête sortante ÉCHOUE avant l'épreuve (contrôle négatif) ;
    3. vérifier que l'application locale répond encore (contrôle positif) ;
    4. conduire le NAVIGATEUR sur les parcours réels ;
    5. relire le compteur de paquets rejetés et refuser s'il n'est pas nul.
    """
    contenu = CI.read_text(encoding="utf-8")
    assert "\n  hors-ligne-reel:\n" in contenu, "le job hors-ligne-reel doit exister"
    bloc = contenu.split("\n  hors-ligne-reel:\n", 1)[1]
    # On s'arrête au job suivant pour ne pas attribuer ses règles à celui-ci.
    bloc = bloc.split("\n  recette-corpus-reel:\n", 1)[0]

    assert "--uid-owner" in bloc, "le blocage doit viser le compte applicatif (pas toute la machine)"
    assert "-j REJECT" in bloc, "une tentative sortante doit être REJETÉE, pas seulement journalisée"
    assert "CONTRÔLE NÉGATIF" in bloc, "le contrôle négatif doit être explicite"
    assert "example.com" in bloc, "le contrôle négatif doit viser une adresse EXTERNE réelle"
    assert "hors-ligne-non-bloque" in bloc, "un blocage inopérant doit faire ÉCHOUER le job"
    assert "CONTRÔLE POSITIF" in bloc and "/health" in bloc, "la pile locale doit rester joignable"
    assert "e2e/hors-ligne.spec.ts" in bloc, "les parcours NAVIGATEUR doivent être exécutés"
    assert 'awk \'$3=="REJECT" {print $1; exit}\'' in bloc, "le compteur de paquets rejetés doit être RELU"
    assert "dependance-externe-cachee" in bloc, "un paquet rejeté non nul doit faire échouer le job"
    assert "SEAMTECH_E2E_HORS_LIGNE" in bloc, "l'épreuve navigateur doit être armée explicitement"
    # Les contrôles ci-dessus REJETTENT volontairement des paquets : sans remise à
    # zéro, le relevé final serait non nul et le job échouerait… pour la bonne
    # raison inverse de celle qu'il doit détecter.
    assert "iptables -Z SEAMTECH_HORS_LIGNE" in bloc, (
        "les compteurs doivent être remis à zéro après les contrôles, avant l'épreuve"
    )
    assert bloc.index("-Z SEAMTECH_HORS_LIGNE") < bloc.index('$3=="REJECT"'), (
        "la remise à zéro doit précéder le relevé final"
    )
    # Le semis d'une base e2e TERMINE les connexions et SUPPRIME les bases e2e_% :
    # réarmé pendant l'épreuve, il détruirait la base de la pile bloquée.
    assert 'SEAMTECH_E2E_DATABASE_URL: ""' in bloc, (
        "l'épreuve navigateur doit réutiliser la pile bloquée, pas re-semer sa base"
    )
    # dl.min.io répond « 410 Gone » : les binaires ne sont plus téléchargeables.
    assert "dl.min.io/server" not in bloc and "dl.min.io/client" not in bloc, (
        "dl.min.io répond 410 Gone : le job ne doit pas TÉLÉCHARGER MinIO depuis ce site"
    )
    assert "scripts/construire_image_minio.sh" in bloc, (
        "l'image MinIO doit être reconstruite depuis les sources archivées (même chaîne que integration/sauvegarde)"
    )
    assert bloc.index("construire_image_minio.sh") < bloc.index("docker run -d --name minio"), (
        "l'image doit être reconstruite AVANT de démarrer le conteneur MinIO"
    )
    assert "SEAMTECH_MINIO_CONTAINER" in bloc, (
        "le provisionnement doit viser le conteneur MinIO hors composition (constat E-29)"
    )
    # La pile tourne sous le compte applicatif : c'est la condition pour que le
    # blocage par uid s'applique VRAIMENT au processus applicatif, et donc pour
    # que « zéro paquet rejeté » ait un sens.
    assert "readlink -f" in bloc, "le compte applicatif doit pouvoir lire l'interpréteur Python employé par la pile"
    assert "--uid-owner \"$UID_APP\"" in bloc, "le blocage doit cibler l'uid réellement calculé du compte applicatif"


def test_le_garde_fou_de_concurrence_compte_les_scenarios_reellement_executes() -> None:
    """Le garde-fou de concurrence doit attendre AUTANT de scénarios qu'il en existe.

    Il attendait 4 tests passés au premier essai ; la revue a ajouté trois
    scénarios (décision liée à la révision revue, révision non lue = écriture
    bloquée, réponse tardive sans écrasement). Un garde resté à 4 refuserait un
    run légitime — ou, pire, laisserait passer un scénario en échec si le compte
    était baissé sans que le fichier de spec le justifie.
    """
    contenu = CI.read_text(encoding="utf-8")
    spec = RACINE / "frontend" / "e2e" / "concurrence.spec.ts"
    nombre = sum(
        1
        for ligne in spec.read_text(encoding="utf-8").splitlines()
        if ligne.strip().startswith("test(") and "test.describe" not in ligne
    )
    assert nombre >= 7, f"scénarios de concurrence attendus : au moins 7, trouvé {nombre}"
    assert f"passes != {nombre}" in contenu, (
        f"le garde-fou doit refuser quand le nombre de tests passés n'est pas {nombre}"
    )
    assert f"attendu {nombre} tests passés au premier essai" in contenu, (
        "le message du garde-fou doit annoncer le même compte que la condition"
    )

"""Les blocs ``run:`` des workflows doivent être EXÉCUTABLES, pas seulement du YAML valide.

Pourquoi ce test existe — un incident réel, pas une précaution théorique.

Lors de la fusion de ``main`` dans la branche de remédiation (21 conflits), une
résolution a ÉCRASÉ une trentaine de lignes d'une étape du job ``e2e`` : la
commande ``playwright test``, le contrôle du code de retour, l'ouverture du
hérédoc et le prologue Python du garde-fou. Le YAML restait PARFAITEMENT VALIDE
— il ne restait que du code Python là où le shell attendait une commande. Le
résultat : ``bash`` échouait en erreur de syntaxe (code de sortie 2), aucune
annotation n'était émise, et la cause était invisible sans les journaux (hôte
de blobs injoignable). Une demi-journée à chercher « pourquoi l'e2e est rouge ».

Un fichier YAML valide ne prouve donc rien sur ce qu'il exécute. Ce test
extrait chaque bloc ``run:`` de chaque workflow, le soumet à ``bash -n`` et
compile les hérédocs Python qu'il contient. Une étape cassée par une fusion
échoue ici, en une seconde, avec le nom du job et de l'étape.

Limite dite franchement : ``bash -n`` valide la SYNTAXE, pas le comportement —
une étape syntaxiquement correcte peut encore ne rien mesurer. C'est le
complément des garde-fous de comptage (``tests/test_selection_ci.py``), pas
leur remplaçant.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

RACINE = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((RACINE / ".github" / "workflows").glob("*.yml"))


def _heredocs_python(script: str) -> list[tuple[int, str]]:
    """Rend ``(numéro de ligne, code)`` pour chaque hérédoc ``python - <<'PY'``."""
    extraits: list[tuple[int, str]] = []
    lignes = script.splitlines()
    indice = 0
    while indice < len(lignes):
        ligne = lignes[indice].strip()
        if "<<" in ligne and re.search(r"\bpython3?\b", ligne):
            trouve = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", ligne)
            marqueur = trouve.group(1) if trouve else "PY"
            debut = indice + 1
            fin = debut
            while fin < len(lignes) and lignes[fin].strip() != marqueur:
                fin += 1
            extraits.append((debut + 1, "\n".join(lignes[debut:fin])))
            indice = fin + 1
            continue
        indice += 1
    return extraits


def _etapes_du_workflow(chemin: Path) -> list[tuple[str, str, str]]:
    """Rend ``(job, étape, script)`` pour chaque bloc ``run:`` du workflow."""
    document = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    etapes: list[tuple[str, str, str]] = []
    for nom_job, job in (document.get("jobs") or {}).items():
        for etape in job.get("steps") or []:
            script = etape.get("run")
            if isinstance(script, str) and script.strip():
                etapes.append((str(nom_job), str(etape.get("name") or "(sans nom)"), script))
    return etapes


def test_les_workflows_sont_du_yaml_valide() -> None:
    """Un workflow illisible n'exécute rien : le parse doit être vérifié."""
    assert WORKFLOWS, f"aucun workflow trouvé sous {RACINE / '.github' / 'workflows'}"
    for chemin in WORKFLOWS:
        document = yaml.safe_load(chemin.read_text(encoding="utf-8"))
        assert isinstance(document, dict), f"{chemin.name} : document YAML inattendu"
        assert document.get("jobs"), f"{chemin.name} : aucun job déclaré"


def test_chaque_bloc_run_est_un_script_analyse() -> None:
    """Chaque ``run:`` doit être un script shell syntaxiquement valide."""
    if shutil.which("bash") is None:  # pragma: no cover - poste sans bash
        pytest.skip("bash introuvable : vérification shell impossible sur ce poste")
    problemes: list[str] = []
    for chemin in WORKFLOWS:
        for nom_job, nom_etape, script in _etapes_du_workflow(chemin):
            with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as handle:
                handle.write(script)
            try:
                resultat = subprocess.run(["bash", "-n", handle.name], capture_output=True, text=True)
            finally:
                Path(handle.name).unlink(missing_ok=True)
            if resultat.returncode != 0:
                problemes.append(
                    f"{chemin.name} · {nom_job} / {nom_etape} :\n{resultat.stderr.strip()}"
                )
    assert not problemes, (
        "bloc(s) run: non exécutables (une étape cassée par une fusion ne lève "
        "aucune erreur YAML et n'émet aucune annotation en CI) :\n  - "
        + "\n  - ".join(probleme.replace("\n", "\n    ") for probleme in problemes)
    )


def test_chaque_heredoc_python_se_compile() -> None:
    """Le Python embarqué (garde-fous, comptages) doit se compiler."""
    problemes: list[str] = []
    for chemin in WORKFLOWS:
        for nom_job, nom_etape, script in _etapes_du_workflow(chemin):
            for numero, code in _heredocs_python(script):
                if not code.strip():
                    continue
                try:
                    compile(code, f"{chemin.name}::{nom_job}::{nom_etape}:{numero}", "exec")
                except SyntaxError as erreur:
                    problemes.append(
                        f"{chemin.name} · {nom_job} / {nom_etape} (ligne {numero}) : {erreur}"
                    )
    assert not problemes, (
        "hérédoc(s) Python invalides dans les workflows :\n  - " + "\n  - ".join(problemes)
    )


def test_le_gardefou_est_lui_meme_executable() -> None:
    """Filet ultime : ce fichier doit trouver au moins un bloc à vérifier.

    Un test qui ne parcourt rien (``glob`` vide, ``jobs`` vide, indentation
    modifiée) passerait au vert en ne prouvant rien — exactement le défaut que
    ces tests combattent.
    """
    total_etapes = sum(len(_etapes_du_workflow(chemin)) for chemin in WORKFLOWS)
    assert total_etapes >= 20, (
        f"{total_etapes} bloc(s) run: parcourus — la collecte est cassée "
        "(structure de workflow modifiée ?)"
    )

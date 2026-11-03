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
import warnings
from pathlib import Path

import pytest
import yaml

# Marqueurs de sortie du contrôle PowerShell : ils distinguent « le script a
# des erreurs de syntaxe » de « le contrôle lui-même n'a pas tourné ». Sans
# eux, une faute de frappe dans la commande de vérification ressemblerait à un
# workflow cassé, et ferait échouer la CI sur un défaut du vérificateur.
MARQUEUR_OK = "SEAMTECH_PWSH_ANALYSE_OK"
MARQUEUR_ERREUR = "SEAMTECH_PWSH_ERREUR_SYNTAXE"

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


def _etapes_du_workflow(chemin: Path) -> list[tuple[str, str, str, str]]:
    """Rend ``(job, étape, script, interpréteur)`` pour chaque bloc ``run:``.

    L'interpréteur est résolu comme GitHub le fait : ``shell:`` au niveau de
    l'étape, sinon ``defaults.run.shell`` du job, sinon celui du workflow,
    sinon ``pwsh`` sur un runner Windows et ``bash`` partout ailleurs. Le
    résoudre correctement est la raison d'être de ce test depuis l'ajout de la
    qualification Windows : soumettre du PowerShell à ``bash -n`` échoue sur
    ``$candidats = @()`` (accident vu en CI, run 41360147937) et ne dit rien de
    la validité du script.
    """
    document = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    etapes: list[tuple[str, str, str, str]] = []
    for nom_job, job in (document.get("jobs") or {}).items():
        job = job or {}
        plateforme = str(job.get("runs-on") or "")
        par_defaut = "pwsh" if "windows" in plateforme else "bash"
        for niveau in (document, job):
            defauts = (niveau.get("defaults") or {}).get("run") or {}
            if isinstance(defauts, dict) and defauts.get("shell"):
                par_defaut = str(defauts["shell"])
        for etape in job.get("steps") or []:
            script = etape.get("run")
            if not (isinstance(script, str) and script.strip()):
                continue
            interpr = str((etape or {}).get("shell") or par_defaut)
            if interpr.startswith("powershell"):
                interpr = "pwsh"
            etapes.append(
                (str(nom_job), str(etape.get("name") or "(sans nom)"), script, interpr)
            )
    return etapes


def _analyse_bash(script: str) -> tuple[str, str] | None:
    """``bash -n`` sur un script rendu temporairement sur le disque.

    Rend ``(sortie, '')`` si le script est analysable, ``('', erreur)`` sinon,
    et ``None`` si bash est absent du poste.
    """
    if shutil.which("bash") is None:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as handle:
        handle.write(script)
    try:
        resultat = subprocess.run(["bash", "-n", handle.name], capture_output=True, text=True)
    finally:
        Path(handle.name).unlink(missing_ok=True)
    if resultat.returncode == 0:
        return ("ok", "")
    return ("", resultat.stderr.strip())


def _analyse_pwsh(script: str) -> tuple[str, str] | None:
    """Parse PowerShell réel (``[Parser]::ParseFile``), pas à peu près.

    Rend ``None`` quand aucun PowerShell n'est installé sur le poste : ce n'est
    pas un script valide, c'est une vérification impossible, et l'appelant doit
    le DIRE plutôt que de le compter pour bon.
    """
    binaire = shutil.which("pwsh") or shutil.which("powershell")
    if binaire is None:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as handle:
        handle.write(script)
    # Le chemin est injecté dans une chaîne PowerShell entre apostrophes
    # doubles : un ``'`` éventuel est doublé, seul échappement valide là.
    chemin = handle.name.replace("'", "''")
    try:
        resultat = subprocess.run(
            [
                binaire,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$tokens = $null; $errors = $null; "
                "$null = [System.Management.Automation.Language.Parser]::ParseFile("
                f"'{chemin}', [ref]$tokens, [ref]$errors); "
                "if ($errors.Count -gt 0) { "
                "Write-Output '" + MARQUEUR_ERREUR + "'; "
                "$errors | ForEach-Object { $_.ToString() }; exit 1 }; "
                "Write-Output '" + MARQUEUR_OK + "'; exit 0",
            ],
            capture_output=True,
            text=True,
        )
    finally:
        Path(handle.name).unlink(missing_ok=True)
    sortie = resultat.stdout or ""
    if resultat.returncode == 0 and MARQUEUR_OK in sortie:
        return ("ok", "")
    if resultat.returncode == 1 and MARQUEUR_ERREUR in sortie:
        return ("", sortie.replace(MARQUEUR_ERREUR, "").strip() or resultat.stderr.strip())
    # Sans marqueur, on ne sait pas ce qui s'est passé : pwsh n'a pas démarré,
    # ou c'est NOTRE commande de contrôle qui est mal formée. Aucun des deux
    # n'est un verdict sur le script — le bloc part en « non vérifié » plutôt
    # que de faire échouer la CI sur un défaut du vérificateur.
    return None


def test_les_workflows_sont_du_yaml_valide() -> None:
    """Un workflow illisible n'exécute rien : le parse doit être vérifié."""
    assert WORKFLOWS, f"aucun workflow trouvé sous {RACINE / '.github' / 'workflows'}"
    for chemin in WORKFLOWS:
        document = yaml.safe_load(chemin.read_text(encoding="utf-8"))
        assert isinstance(document, dict), f"{chemin.name} : document YAML inattendu"
        assert document.get("jobs"), f"{chemin.name} : aucun job déclaré"


def test_chaque_bloc_run_est_un_script_analyse() -> None:
    """Chaque ``run:`` doit être un script syntaxiquement valide DANS SON SHELL.

    Le premier jet de ce test envoyait tout à ``bash -n``. Avec l'arrivée du
    job de qualification Windows (PowerShell), les quatre étapes ``run:`` de ce
    workflow y étaient déclarées « non exécutables » alors qu'elles étaient
    valides — la vérification mesurait du PowerShell avec la règle du shell
    bourne. C'est exactement le défaut que ce fichier combat, retourné contre
    lui : un contrôle qui se trompe d'outil produit des verdicts faux.

    Le shell de chaque étape est donc résolu (``shell:`` de l'étape, sinon
    ``defaults.run.shell``, sinon la plateforme), puis analysé par ``bash -n``
    ou par le parseur PowerShell. Quand l'outil correspondant n'est pas
    installé sur le poste, l'étape n'est PAS comptée comme bonne : elle est
    signalée (avertissement visible dans chaque rapport pytest) et le test
    échoue si aucune étape n'a pu être vérifiée du tout.
    """
    problemes: list[str] = []
    non_verifies: list[str] = []
    verifiees = 0
    for chemin in WORKFLOWS:
        for nom_job, nom_etape, script, interpr in _etapes_du_workflow(chemin):
            analyseur = _analyse_pwsh if interpr == "pwsh" else _analyse_bash
            verdict = analyseur(script)
            if verdict is None:
                non_verifies.append(f"{chemin.name} · {nom_job} / {nom_etape} ({interpr})")
                continue
            verifiees += 1
            _, erreur = verdict
            if erreur:
                problemes.append(f"{chemin.name} · {nom_job} / {nom_etape} ({interpr}) :\n{erreur}")
    if non_verifies:
        # Visible dans le rapport pytest de chaque exécution : un bloc non
        # vérifié n'est pas un bloc validé, il ne doit pas devenir invisible.
        warnings.warn(
            "bloc(s) run: non vérifiés faute d'interpréteur sur ce poste — "
            "ils le sont sur un runner GitHub (pwsh préinstallé) : "
            + ", ".join(non_verifies),
            stacklevel=1,
        )
    assert verifiees >= 20, (
        f"{verifiees} bloc(s) run: effectivement analysés — la vérification ne "
        "tourne pas (interpréteurs manquants ?) et le test ne prouverait rien"
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
        for nom_job, nom_etape, script, _interpr in _etapes_du_workflow(chemin):
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


def _workflow_factice(tmp_path: Path, scripts: list[str], shell: str = "pwsh") -> Path:
    """Écrit un workflow jetable dont chaque bloc ``run:`` est ``scripts[i]``."""
    document = {
        "name": "factice",
        "jobs": {
            "job": {
                "runs-on": "windows-latest" if shell == "pwsh" else "ubuntu-latest",
                "defaults": {"run": {"shell": shell}},
                "steps": [
                    {"name": f"étape {i}", "run": script} for i, script in enumerate(scripts)
                ],
            }
        },
    }
    chemin = tmp_path / "factice.yml"
    chemin.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return chemin


def test_le_gardefou_distingue_echec_et_non_verifie(monkeypatch, tmp_path) -> None:
    """Le verdict du garde-fou ne doit pas dépendre de l'outillage du poste.

    Ce poste n'a pas PowerShell : sans lui, les blocs ``pwsh`` du workflow
    Windows seraient soit validés à tort (comptés bons alors qu'on ne les a pas
    lus), soit bloquants (CI rouge là où rien n'est cassé). Des analyseurs
    factices prouvent les trois comportements sans dépendre d'un binaire
    installé :

      * un bloc cassé fait échouer le test ;
      * un bloc que l'outil ne peut pas lire est signalé et ne compte pas
        comme bon ;
      * si PLUS AUCUN bloc n'est analysable, le test échoue au lieu de passer
        en ne prouvant rien.
    """
    borne = 21
    bons = [f"Write-Host 'étape {i}'" for i in range(borne)]

    def analyseur(script: str):
        """Factices : un script « CASSE » échoue, un « MUET » n'est pas lisible."""
        if script == "CASSE":
            return ("", "erreur de syntaxe simulée")
        if script == "MUET":
            return None
        return ("ok", "")

    def lance(scripts: list[str]):
        monkeypatch.setattr("test_ci_executable.WORKFLOWS", [_workflow_factice(tmp_path, scripts)])
        monkeypatch.setattr("test_ci_executable._analyse_pwsh", analyseur)
        monkeypatch.setattr("test_ci_executable._analyse_bash", analyseur)
        test_chaque_bloc_run_est_un_script_analyse()

    # 1. tous les blocs sont bons : ça passe.
    lance(bons)

    # 2. un bloc cassé : échec, et le message le nomme.
    with pytest.raises(AssertionError, match="non exécutables"):
        lance(bons + ["CASSE"])

    # 3. un bloc illisible : ce n'est pas un échec, mais c'est DIT.
    with pytest.warns(UserWarning, match="non vérifiés"):
        lance(bons + ["MUET"])

    # 4. plus rien d'analysable : le test refuse de passer à vide.
    with pytest.raises(AssertionError, match="effectivement analysés"):
        lance(["MUET"] * borne)

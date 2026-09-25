"""Tests statiques du diagnostic Windows — scripts/preflight_windows.ps1.

Le script tourne sur le poste de production Windows ; la CI est Linux : ces
tests sont donc des tests de CONTENU (contrat écrit) qui prouvent que le
script reste non destructif :

- aucune opération d'écriture/suppression/déplacement/exécution système ;
- aucun téléchargement ni appel réseau ;
- aucun secret ni mot de passe manipulé ou journalisé ;
- aucun démarrage forcé de Docker ;
- aucun accès à l'archive réelle (l'emplacement prévu est affiché, jamais ouvert) ;
- présence des 14 points de contrôle exigés par docs/CHECKLIST_POSTE_WINDOWS.md.

Si le script évolue, ces tests forcent à relire le contrat de sécurité.
"""

from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
PS1_PATH = RACINE / "scripts" / "preflight_windows.ps1"
CHECKLIST_PATH = RACINE / "docs" / "CHECKLIST_POSTE_WINDOWS.md"


def _contenu() -> str:
    assert PS1_PATH.exists(), "scripts/preflight_windows.ps1 manquant"
    return PS1_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# A. Non destruction — aucune opération d'écriture système
# ---------------------------------------------------------------------------

COMMANDES_DESTRUCTIVES = (
    "Remove-Item",
    "Remove-ItemProperty",
    "Set-Content",
    "Add-Content",
    "Out-File",
    "New-Item",
    "Move-Item",
    "Copy-Item",
    "Rename-Item",
    "Clear-Content",
    "Clear-Item",
    "Set-Item",
    "Set-ItemProperty",
    "New-ItemProperty",
    "Start-Process",
    "Start-Service",
    "Stop-Service",
    "Restart-Service",
    "Restart-Computer",
    "Stop-Computer",
    "Set-ExecutionPolicy",
    "Set-Date",
    "New-Service",
    "Unregister-Event",
    "Format-Volume",
    "Clear-Disk",
    "Reset-ComputerMachinePassword",
)


def test_script_ne_contient_aucune_commande_destructive() -> None:
    texte = _contenu()
    interdites = [cmd for cmd in COMMANDES_DESTRUCTIVES if cmd in texte]
    assert not interdites, f"commandes destructives trouvées dans preflight_windows.ps1 : {interdites}"


COMMANDES_INSTALLATION = ("choco ", "winget", "scoop install", "msiexec", "Start-BitsTransfer", "Install-Module", "Install-Package")


def test_script_ne_contient_aucune_installation() -> None:
    texte = _contenu()
    interdites = [cmd for cmd in COMMANDES_INSTALLATION if cmd in texte]
    assert not interdites, f"installations trouvées : {interdites}"


COMMANDES_RESEAU = (
    "Invoke-WebRequest",
    "Invoke-RestMethod",
    "Invoke-Expression",
    "New-Object Net.WebClient",
    "System.Net.WebClient",
    "DownloadFile",
    "DownloadString",
    "DownloadData",
    "curl.exe",
    "wget.exe",
    "iwr ",
    "irm ",
    "iex ",
)


def test_script_ne_contient_aucun_telechargement_ou_appel_reseau() -> None:
    texte = _contenu()
    interdites = [cmd for cmd in COMMANDES_RESEAU if cmd in texte]
    assert not interdites, f"téléchargements/appels réseau trouvés : {interdites}"


COMMANDES_SYSTEME = ("reg.exe", "schtasks", "net user", "net localgroup", "icacls", "takeown", "bcdedit", "diskpart", "sc.exe", "wmic os get")


def test_script_ne_contient_aucune_modification_systeme() -> None:
    texte = _contenu()
    interdites = [cmd for cmd in COMMANDES_SYSTEME if cmd in texte]
    assert not interdites, f"commandes système trouvées : {interdites}"


# ---------------------------------------------------------------------------
# B. Secrets — aucun mot de passe, token ou secret
# ---------------------------------------------------------------------------


def test_aucune_variable_secret_assignee() -> None:
    import re

    texte = _contenu()
    motifs = re.findall(r"\$\w*(?:password|passwd|secret|token|pwd)\w*\s*=", texte, flags=re.IGNORECASE)
    assert not motifs, f"variables de type secret assignées : {motifs}"


def test_aucune_saisie_de_mot_de_passe() -> None:
    texte = _contenu()
    assert "Read-Host -AsSecureString" not in texte
    assert "Get-Credential" not in texte
    assert "ConvertTo-SecureString" not in texte
    assert "ConvertFrom-SecureString" not in texte


def test_fichier_env_jamais_lu() -> None:
    """.env n'est jamais ouvert — seule son existence est testée (Test-Path)."""
    texte = _contenu()
    for ligne in texte.splitlines():
        if ".env" in ligne and "Get-Content" in ligne:
            raise AssertionError(f".env lu par Get-Content (interdit) : {ligne.strip()}")
    assert "Test-Path -LiteralPath \".env\"" in texte or "Test-Path -LiteralPath '.env'" in texte or 'Test-Path -LiteralPath ".env"' in texte


def test_config_files_existence_seulement() -> None:
    """Les fichiers de config ne sont pas lus (pas de fuite de secret en log)."""
    texte = _contenu()
    for ligne in texte.splitlines():
        if "Get-Content" in ligne and any(x in ligne for x in (".env", "config.json", "docker-compose")):
            raise AssertionError(f"fichier de configuration lu (interdit) : {ligne.strip()}")


# ---------------------------------------------------------------------------
# C. Docker — jamais démarré de force
# ---------------------------------------------------------------------------


def test_docker_jamais_demarre_ni_modifie() -> None:
    texte = _contenu()
    for interdit in ("docker start", "docker stop", "docker rm", "docker pull", "docker kill", "docker compose up", "docker-compose up", "docker system prune", "Start-Service"):
        assert interdit not in texte, f"commande docker/service non autorisée : {interdit}"
    # Sous-commandes docker autorisées : version, info, ps (lecture seule).
    assert "docker version" in texte
    assert "Get-Service" in texte


# ---------------------------------------------------------------------------
# D. Archive réelle — jamais accessible
# ---------------------------------------------------------------------------


def test_emplacement_archive_affiche_jamais_consulte() -> None:
    """$EmplacementArchive ne doit alimenter AUCUN cmdlet de lecture de fichier."""
    texte = _contenu()
    interdits = (
        "Get-Content $EmplacementArchive",
        "Get-Content -LiteralPath $EmplacementArchive",
        "Get-ChildItem $EmplacementArchive",
        "Get-ChildItem -LiteralPath $EmplacementArchive",
        "Test-Path $EmplacementArchive",
        "Test-Path -LiteralPath $EmplacementArchive",
        "Get-Item $EmplacementArchive",
        "Get-Item -LiteralPath $EmplacementArchive",
        "Resolve-Path $EmplacementArchive",
    )
    for interdit in interdits:
        assert interdit not in texte, f"l'archive réelle est consultée ({interdit})"
    assert "non vérifié" in texte, "le rapport doit dire que l'emplacement n'est pas vérifié"


# ---------------------------------------------------------------------------
# E. Couverture des 14 points de contrôle de la checklist
# ---------------------------------------------------------------------------

POINTS_CONTROLE = (
    "Version Windows",
    "Architecture 64 bits",
    "RAM totale",
    "Espace disque",
    "Docker / Docker Desktop",
    "Services Docker",
    "Python",
    "Tesseract",
    "Langue française",
    "pdftoppm",
    "Port ",
    "Répertoire de travail OCR",
    "Répertoire de sauvegarde",
    "Emplacement prévu de l'archive",
)


def test_les_14_points_de_controle_sont_couverts() -> None:
    texte = _contenu()
    manquants = [point for point in POINTS_CONTROLE if point not in texte]
    assert not manquants, f"points de contrôle absents du script : {manquants}"


def test_script_declare_lecture_seule_et_code_sortie() -> None:
    texte = _contenu()
    assert "LECTURE SEULE" in texte
    assert "ne modifie RIEN" in texte
    assert "exit 0" in texte and "exit 1" in texte


# ---------------------------------------------------------------------------
# F. Checklist Windows — document exploitable
# ---------------------------------------------------------------------------

SECTIONS_ATTENDUES = (
    "prérequis minimaux",
    "commandes PowerShell",
    "résultats attendus",
    "problèmes fréquents",
    "actions correctives",
    "avant mise en service",
    "après redémarrage",
    "espace disque",
    "sauvegarde",
)


def test_checklist_presente_et_complete() -> None:
    assert CHECKLIST_PATH.exists(), "docs/CHECKLIST_POSTE_WINDOWS.md manquant"
    texte = CHECKLIST_PATH.read_text(encoding="utf-8").lower()
    manquantes = [section for section in SECTIONS_ATTENDUES if section.lower() not in texte]
    assert not manquantes, f"sections manquantes dans la checklist : {manquantes}"


def test_checklist_documente_le_diagnostic_script() -> None:
    texte = CHECKLIST_PATH.read_text(encoding="utf-8")
    assert "preflight_windows.ps1" in texte
    assert "Tee-Object" in texte  # conservation du rapport sans écriture par le script

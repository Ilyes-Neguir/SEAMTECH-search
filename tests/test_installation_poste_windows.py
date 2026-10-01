"""Tests statiques de l'installation « propre » sur un poste Windows.

Ces scripts tournent sur le poste de l'utilisateur, jamais en CI (Linux) : ce
sont donc des tests de CONTENU — un contrat écrit, dans l'esprit de
``tests/test_preflight_windows.py``. Ils prouvent que l'installation reste :

- lisible par Windows PowerShell 5.1 (BOM UTF-8, chemins avec espaces/accents) ;
- honnête : aucun secret imprimé, aucun logiciel installé à la place de
  l'utilisateur, aucune donnée supprimée ;
- complète : copie du dépôt vérifiée, services de données, dépendances,
  configuration SANS BOM (sinon ``json.load`` échoue), premier lancement réel,
  compte nominatif, icône sur le Bureau et dans le menu Démarrer ;
- sûre à l'arrêt : processus tués par identifiant, arbre de processus respecté,
  nom du processus vérifié (un PID recyclé n'est jamais tué).
"""

from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
INSTALLATEUR = RACINE / "scripts" / "installer_poste_windows.ps1"
ARRET = RACINE / "scripts" / "arreter_seamtech.ps1"
LANCEUR = RACINE / "scripts" / "start_seamtech_search.ps1"
CMD_INSTALLATEUR = RACINE / "Installer SEAMTECH Search.cmd"
ICONE = RACINE / "SEAMTECH Search.ico"
GUIDE = RACINE / "docs" / "INSTALLATION_POSTE_WINDOWS.md"
STRUCTURE = RACINE / "docs" / "STRUCTURE.md"
CHANGELOG = RACINE / "CHANGELOG.md"

#: Variables qui portent un secret dans l'installeur : elles ne doivent jamais
#: atteindre une ligne de journalisation.
VARIABLES_SECRETES = ("$Valeurs", "$Clair", "$MotDePasse", "$Conf.s3_secret_access_key")

#: Installateurs interdits : l'installeur MESURE le poste, il n'installe rien.
INSTALLATEURS_LOGICIEL = (
    "winget",
    "choco ",
    "scoop install",
    "msiexec",
    "Start-BitsTransfer",
    "Install-Module",
    "Install-Package",
)


def _contenu(chemin: Path) -> str:
    assert chemin.exists(), f"{chemin.name} manquant"
    # utf-8-sig : les scripts PowerShell sont écrits avec BOM (voir test dédié).
    return chemin.read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# A. Présence et encodage
# ---------------------------------------------------------------------------


def test_fichiers_de_l_installation_presents() -> None:
    for chemin in (INSTALLATEUR, ARRET, LANCEUR, CMD_INSTALLATEUR, ICONE, GUIDE):
        assert chemin.exists(), f"{chemin.name} manquant — installation incomplète"


def test_scripts_powershell_ont_un_bom_utf8() -> None:
    """Sans BOM, Windows PowerShell 5.1 lit un .ps1 en ANSI : tous les accents
    des messages partaient en mojibake sur le poste de l'utilisateur."""
    for chemin in (INSTALLATEUR, ARRET, LANCEUR):
        assert chemin.read_bytes().startswith(b"\xef\xbb\xbf"), f"{chemin.name} doit être UTF-8 avec BOM"


def test_fichier_cmd_sans_bom_et_ascii() -> None:
    """cmd.exe lit les .cmd en OEM : un accent y serait illisible, et un BOM
    casserait la première ligne."""
    donnees = CMD_INSTALLATEUR.read_bytes()
    assert not donnees.startswith(b"\xef\xbb\xbf"), "le .cmd ne doit pas avoir de BOM"
    assert all(octet < 128 for octet in donnees), "le .cmd doit rester ASCII (cmd.exe lit en OEM)"


def test_icone_valide() -> None:
    """Le raccourci pointe sur cette icône : elle doit être un vrai .ico."""
    donnees = ICONE.read_bytes()
    assert donnees[:4] == b"\x00\x00\x01\x00", "en-tête ICO attendu (réservé=0, type=1 icône)"
    assert donnees[4:6] == b"\x01\x00", "une seule image attendue dans l'icône"


# ---------------------------------------------------------------------------
# B. L'installeur couvre les huit étapes
# ---------------------------------------------------------------------------

ETAPES_INSTALLATEUR = (
    "copie du depot",  # 1. copie complète du dépôt
    "Get-CimInstance Win32_OperatingSystem",  # 2. mesure du poste
    "ensure_postgres.ps1",  # 3. services de données (.env, MinIO, conteneurs)
    "requirements.txt",  # 4. dépendances Python
    "bootstrap.py",  # 4. config/config.json
    "Start-Process -FilePath $LanceurCmd",  # 5. premier lancement réel
    "comptes.cli creer",  # 6. premier compte nominatif
    "CreateShortcut",  # 7. icône (Bureau + menu Démarrer)
    "Installation terminee",  # 8. récapitulatif
)


def test_installateur_couvre_les_huit_etapes() -> None:
    contenu = _contenu(INSTALLATEUR)
    manquantes = [etape for etape in ETAPES_INSTALLATEUR if etape not in contenu]
    assert not manquantes, f"étapes absentes de l'installeur : {manquantes}"


def test_installateur_verifie_la_sante_de_l_interface() -> None:
    """Un premier lancement qui « marche » doit être PROUVÉ, pas supposé."""
    contenu = _contenu(INSTALLATEUR)
    assert "/api/health" in contenu, "l'installeur doit contrôler /api/health après le lancement"


def test_installateur_est_rejouable() -> None:
    """Rejouable : .env appartient à ensure_postgres.ps1, l'installeur ne doit
    jamais l'écrire (sinon les secrets régénérés casseraient les volumes)."""
    contenu = _contenu(INSTALLATEUR)
    assert "Rejouable" in contenu, "la rejouabilité doit être documentée"
    assert "Set-Content -Path $EnvFile" not in contenu, ".env est écrit par ensure_postgres.ps1, pas ici"
    assert "Set-Content -Path (Join-Path $ProjectRoot" not in contenu, "aucun fichier du projet réécrit à l'aveugle"


def test_installateur_propose_le_compte_nominatif() -> None:
    contenu = _contenu(INSTALLATEUR)
    assert "AsSecureString" in contenu, "le mot de passe est saisi masqué"
    assert "Read-Host" in contenu, "l'installeur interroge l'utilisateur"
    assert "--mot-de-passe-definitif" in contenu, (
        "l'interface n'a aucun écran de changement forcé : forcer le changement enfermerait l'opérateur"
    )
    # Le mot de passe voyage sur l'entrée standard, jamais en argument.
    assert " | & $VenvPython -m seamtech_search.comptes.cli creer" in contenu


# ---------------------------------------------------------------------------
# C. L'icône : Bureau et menu Démarrer
# ---------------------------------------------------------------------------


def test_icone_deposee_sur_le_bureau_et_dans_le_menu_demarrer() -> None:
    contenu = _contenu(INSTALLATEUR)
    assert "IconLocation" in contenu, "le raccourci doit porter l'icône du projet"
    assert "SEAMTECH Search.ico" in contenu
    assert "SEAMTECH Search.lnk" in contenu, "raccourci Bureau / menu Démarrer"
    assert "Arreter SEAMTECH Search.lnk" in contenu, "l'arrêt doit être accessible depuis le menu Démarrer"
    assert "SpecialFolders" in contenu, "les dossiers système sont demandés à Windows"


def test_menu_demarrer_jamais_code_en_dur() -> None:
    """Sur un poste français le dossier s'appelle « Menu Démarrer\\Programmes » :
    un chemin anglais codé en dur déposerait les raccourcis dans un dossier que
    personne ne regarde."""
    contenu = _contenu(INSTALLATEUR)
    assert "Start Menu" not in contenu, "chemin anglais codé en dur interdit"
    assert "Programmes" not in contenu or "Menu Démarrer" in contenu


def test_demarrage_automatique_optionnel() -> None:
    contenu = _contenu(INSTALLATEUR)
    assert "[switch]$DemarrageAutomatique" in contenu
    assert 'SpecialFolders("Startup")' in contenu


# ---------------------------------------------------------------------------
# D. Configuration locale écrite correctement
# ---------------------------------------------------------------------------


def test_config_json_ecrite_sans_bom() -> None:
    """Python ouvre config/config.json en UTF-8 strict : un BOM ferait échouer
    `json.load` au démarrage du moteur."""
    contenu = _contenu(INSTALLATEUR)
    assert "UTF8Encoding" in contenu, "l'encodage doit être piloté explicitement"
    assert "$false" in contenu, "UTF8Encoding($false) = sans BOM"
    assert "WriteAllText" in contenu
    assert "Set-Content -Path $ConfigPath" not in contenu, "Set-Content -Encoding UTF8 de PS 5.1 écrit un BOM"


def test_config_locale_renseigne_minio_et_redis() -> None:
    """Le moteur lancé en local lit config/config.json (pas .env) : sans ces
    valeurs, les dépôts partaient en échec d'identification MinIO."""
    contenu = _contenu(INSTALLATEUR)
    for cle in ("s3_endpoint_url", "s3_access_key", "s3_secret_access_key", "redis_url", "root_paths"):
        assert cle in contenu, f"{cle} doit être renseigné dans config/config.json"


def test_dossier_a_indexer_jamais_devine() -> None:
    """Le dossier à indexer est proposé à l'utilisateur et vérifié (RG13 : on
    n'invente pas un chemin de production)."""
    contenu = _contenu(INSTALLATEUR)
    assert "Test-Path -LiteralPath $chemin" in contenu, "chaîne de dossier non vérifiée"
    assert "Dossier(s) a indexer" in contenu


def test_chemins_toujours_avec_join_path() -> None:
    for chemin in (INSTALLATEUR, ARRET, LANCEUR):
        contenu = _contenu(chemin)
        assert "Join-Path" in contenu, f"{chemin.name} doit construire ses chemins avec Join-Path"
        assert "C:\\" not in contenu, f"{chemin.name} : aucun disque codé en dur"


# ---------------------------------------------------------------------------
# E. Sécurité : rien d'installé, aucun secret visible
# ---------------------------------------------------------------------------


def test_installateur_n_installe_aucun_logiciel() -> None:
    contenu = _contenu(INSTALLATEUR)
    interdits = [outil for outil in INSTALLATEURS_LOGICIEL if outil in contenu]
    assert not interdits, f"l'installeur ne doit rien installer lui-même : {interdits}"


def test_aucun_secret_imprime() -> None:
    """Aucune variable portant un secret ne doit atteindre la console : les
    journaux d'installation circulent (copier-coller de dépannage)."""
    for chemin in (INSTALLATEUR, ARRET, LANCEUR):
        for numero, ligne in enumerate(_contenu(chemin).splitlines(), 1):
            if "Write-Host" in ligne:
                fuites = [variable for variable in VARIABLES_SECRETES if variable in ligne]
                assert not fuites, f"{chemin.name}:{numero} journalise un secret : {fuites}"


def test_installateur_ne_supprime_rien() -> None:
    contenu = _contenu(INSTALLATEUR)
    assert "Remove-Item" not in contenu, "l'installeur ne supprime aucun fichier"
    assert "down -v" not in contenu
    assert "system prune" not in contenu


# ---------------------------------------------------------------------------
# F. Arrêt propre : jamais de destruction de données
# ---------------------------------------------------------------------------


def test_arret_jamais_destructif() -> None:
    contenu = _contenu(ARRET)
    for interdit in ("down -v", "system prune", "Remove-Item -Recurse", "Remove-Item -Force $ProjectRoot"):
        # Les commentaires peuvent CITER la commande interdite, pas le code.
        for numero, ligne in enumerate(contenu.splitlines(), 1):
            if ligne.lstrip().startswith("#"):
                continue
            assert interdit not in ligne, f"arreter_seamtech.ps1:{numero} : {interdit}"


def test_arret_ne_supprime_que_ses_propres_fichiers_de_pid() -> None:
    contenu = _contenu(ARRET)
    suppressions = [ligne for ligne in contenu.splitlines() if "Remove-Item" in ligne]
    assert suppressions, "les fichiers de PID doivent être nettoyés après arrêt"
    for ligne in suppressions:
        assert ("FichierMoteur" in ligne) or ("FichierInterface" in ligne), f"suppression inattendue : {ligne.strip()}"


def test_arret_tue_l_arbre_de_processus() -> None:
    """Sans l'arbre, un node fantôme continuerait à tenir le port 3000 et le
    démarrage suivant basculerait sur 3001."""
    contenu = _contenu(ARRET)
    assert "Descendants" in contenu
    assert "Win32_Process" in contenu
    assert "ParentProcessId" in contenu


def test_arret_verifie_le_nom_du_processus() -> None:
    """Un PID réattribué par Windows ne doit jamais être tué par erreur."""
    contenu = _contenu(ARRET)
    assert "ProcessName" in contenu
    assert "PID recyclé" in contenu


def test_arret_par_identifiant_avec_repli_sur_les_ports() -> None:
    contenu = _contenu(ARRET)
    assert "backend.pid" in contenu
    assert "frontend.pid" in contenu
    assert "Get-NetTCPConnection" in contenu, "repli par port pour un lancement antérieur"
    for port in ("8000", "3000", "3001"):
        assert port in contenu


def test_arret_des_services_est_optionnel() -> None:
    contenu = _contenu(ARRET)
    assert "[switch]$AvecServices" in contenu
    assert "docker compose stop postgres minio redis" in contenu


# ---------------------------------------------------------------------------
# G. Le lanceur quotidien garde son comportement (régression)
# ---------------------------------------------------------------------------


def test_lanceur_garde_son_comportement() -> None:
    contenu = _contenu(LANCEUR)
    for element in (
        "ensure_postgres.ps1",  # services de données + .env
        '"seamtech_search", "serve"',  # démarrage du moteur
        "pnpm install --frozen-lockfile",  # construction de l'interface
        "pnpm build",
        "Start-Process $FrontendUrl",  # ouverture du navigateur
        "$env:SEAMTECH_API_URL",
    ):
        assert element in contenu, f"le lanceur a perdu : {element}"


def test_lanceur_journalise_et_enregistre_les_processus() -> None:
    contenu = _contenu(LANCEUR)
    assert "Start-Transcript" in contenu, "tout démarrage laisse une trace lisible"
    assert "data\\logs" in contenu
    assert "backend.pid" in contenu
    assert "frontend.pid" in contenu
    assert "-PassThru" in contenu, "l'identifiant de processus doit être capturé"


def test_lanceur_signale_ses_echecs() -> None:
    """Lancé sans fenêtre depuis l'icône : un échec ne doit pas disparaître."""
    contenu = _contenu(LANCEUR)
    assert "Popup" in contenu, "une bulle doit annoncer l'échec"
    assert "exit 1" in contenu


# ---------------------------------------------------------------------------
# H. Documentation
# ---------------------------------------------------------------------------


def test_guide_documente_prerequis_et_depannage() -> None:
    contenu = GUIDE.read_text(encoding="utf-8")
    for attendu in (
        "Docker Desktop",
        "pnpm",
        "Python",
        "Git for Windows",
        "Installer SEAMTECH Search.cmd",
        "Arreter SEAMTECH Search",
        "Dépannage",
        "icône",
    ):
        assert attendu in contenu, f"le guide doit couvrir : {attendu}"


def test_guide_dit_ce_que_l_installation_ne_fait_pas() -> None:
    """Honnêteté : pas d'installation de logiciels, pas de réseau, pas d'écriture
    hors projet/Bureau/menu Démarrer."""
    contenu = GUIDE.read_text(encoding="utf-8")
    assert "n'installe" in contenu
    assert "127.0.0.1" in contenu


def test_structure_et_changelog_mentionnent_l_installation() -> None:
    structure = STRUCTURE.read_text(encoding="utf-8")
    for attendu in (
        "installer_poste_windows.ps1",
        "arreter_seamtech.ps1",
        "Installer SEAMTECH Search.cmd",
        "SEAMTECH Search.ico",
    ):
        assert attendu in structure, f"docs/STRUCTURE.md doit lister : {attendu}"
    assert "installer_poste_windows.ps1" in CHANGELOG.read_text(encoding="utf-8")

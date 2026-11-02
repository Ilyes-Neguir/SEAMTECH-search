# Verdict d'une suite exécutée sur Windows.
#
# Pourquoi un script à part, appelé par les deux étapes de verdict : la
# mécanique est identique, et la recopier deux fois garantissait qu'elle
# divergerait. Surtout, elle doit être COMPLÈTE et OPPOSABLE :
#
#   * complète : GitHub limite à 10 annotations par étape et tronque les
#     messages. Une suite à 73 échecs ne se lisait donc qu'aux dix premiers —
#     le reste était invisible. L'inventaire est publié ici par paquets, un
#     identifiant de test par ligne, sans message d'erreur : 3000 caractères
#     d'identifiants suffisent à couvrir une suite entière.
#   * opposable : un échec non NOMMÉ dans ``ci/ecarts-windows.txt`` fait
#     échouer le job. Un test écarté doit être écrit quelque part, avec sa
#     raison — jamais « oublié ». C'est la différence entre un périmètre et
#     une dissimulation.
#
# Usage : ci/verdict-windows.ps1 -Journal logs\x.log -CodeSortie logs\rc-x.txt -Titre windows-sans-service
param(
    [Parameter(Mandatory = $true)][string]$Journal,
    [Parameter(Mandatory = $true)][string]$CodeSortie,
    [Parameter(Mandatory = $true)][string]$Titre,
    [int]$Tranche = 1
)

$ErrorActionPreference = 'Continue'

function Publier([string]$titre, [string]$texte) {
    if ($null -eq $texte) { $texte = '' }
    $propre = $texte -replace '%', '%25' -replace "`r", '%0D' -replace "`n", '%0A'
    if ($propre.Length -gt 3000) { $propre = $propre.Substring(0, 3000) }
    Write-Host "::notice title=$titre::$propre"
}

function PublierErreur([string]$titre, [string]$texte) {
    if ($null -eq $texte) { $texte = '' }
    $propre = $texte -replace '%', '%25' -replace "`r", '%0D' -replace "`n", '%0A'
    if ($propre.Length -gt 3000) { $propre = $propre.Substring(0, 3000) }
    Write-Host "::error title=$titre::$propre"
}

# --- 1. Le code de sortie : sans lui, l'étape a été INTERROMPUE -------------
if (-not (Test-Path $CodeSortie)) {
    # L'étape a été INTERROMPUE (vue au run 41375766036 : aucun code de sortie
    # écrit, alors que le témoin de début était publié). Un « ça meurt » sans
    # « où ça meurt » ne se répare pas : on publie donc la FIN du journal —
    # avec pytest en mode verbeux, la dernière ligne nomme le dernier test
    # démarré. C'est le seul canal qui survit à une mort de l'étape.
    $queue = ''
    if (Test-Path $Journal) {
        $queue = ((Get-Content $Journal -Tail 25) -join ' `n ')
    }
    # C'est la FIN du journal qui dit où ça meurt, et ``Publier`` tronque au
    # DÉBUT : on coupe donc la queue nous-mêmes avant de la lui confier. Run
    # 41420706781 : le message commençait par le premier test de la queue et
    # s'arrêtait avant la ligne utile.
    if ($queue.Length -gt 2800) { $queue = '…' + $queue.Substring($queue.Length - 2800) }
    PublierErreur $Titre ("interrompue avant la fin — fin du journal : $queue")
    exit 1
}
$rc = [int]((Get-Content $CodeSortie -Raw).Trim())

# --- 2. L'inventaire COMPLET des échecs ------------------------------------
$echecs = @()
if (Test-Path $Journal) {
    $echecs = Select-String -Path $Journal -Pattern '^(FAILED|ERROR) ' |
              ForEach-Object { (($_.Line -split '\s+')[1]) } |
              Where-Object { $_ -like 'tests/*' } |
              Sort-Object -Unique
}
$resume = ''
if (Test-Path $Journal) {
    $ligne_resume = Select-String -Path $Journal -Pattern '\d+ (passed|failed)' |
                    Select-Object -Last 1
    if ($ligne_resume) { $resume = $ligne_resume.Line.Trim() }
}
Publier $Titre "rc=$rc ; echecs=$($echecs.Count) ; $resume"

# --- 2 bis. Les CAUSES, et non seulement les noms ---------------------------
# 314 échecs ne s'instruisent pas un par un : ils se regroupent par CAUSE.
# Or la cause n'est presque JAMAIS dans la ligne « FAILED » — « Regex pattern
# did not match. » ne dit rien de ce qui manque — elle est dans la DERNIÈRE
# ligne « E » de la trace, que ``--tb=short`` attache à chaque test. On
# publie donc une annotation par famille : combien d'échecs, quelle cause,
# trois exemples. C'est ce qui permet de décider, famille par famille, entre
# adapter le produit et nommer l'écart.
#
# Plafond : DIX annotations par ÉTAPE. Une seule étape ne publie donc que dix
# familles ; le workflow rappelle ce script avec ``-Tranche 2``, ``3``… pour
# couvrir les suivantes. Sans ce découpage, l'inventaire prenait toute la
# place et les causes disparaissaient (run 41439011809).
$familles = @{}
$courant_id = $null
$courant_cause = ''
if (Test-Path $Journal) {
    foreach ($correspondance in (Select-String -Path $Journal -Pattern '^(FAILED|ERROR) |^E\s+')) {
        $texte = $correspondance.Line
        if ($texte -match '^(FAILED|ERROR) ') {
            if ($courant_id) {
                $cle = if ($courant_cause) { $courant_cause } else { '(aucune ligne E : cause hors trace)' }
                if (-not $familles.ContainsKey($cle)) { $familles[$cle] = New-Object System.Collections.ArrayList }
                [void]$familles[$cle].Add($courant_id)
            }
            $courant_id = ($texte -split '\s+')[1]
            $courant_cause = ''
        } elseif ($texte -match '^E\s+(.*)$') {
            # La DERNIÈRE ligne E d'un test est la plus précise : elle écrase
            # les précédentes, qui ne sont que le chemin d'appel.
            $courant_cause = $Matches[1].Trim()
        }
    }
    if ($courant_id) {
        $cle = if ($courant_cause) { $courant_cause } else { '(aucune ligne E : cause hors trace)' }
        if (-not $familles.ContainsKey($cle)) { $familles[$cle] = New-Object System.Collections.ArrayList }
        [void]$familles[$cle].Add($courant_id)
    }
}
$cles_triees = @($familles.Keys | Sort-Object { -$familles[$_].Count })
# Dix annotations par étape au total, compte rendu du code de sortie compris :
# la tranche 1 publie sept familles et garde deux places pour l'inventaire ;
# les tranches suivantes, appelées par d'autres étapes, en publient huit.
if ($Tranche -le 1) { $debut = 0; $combien = 7 } else { $debut = 7 + ($Tranche - 2) * 8; $combien = 8 }
$numero = $debut
foreach ($cle in ($cles_triees | Select-Object -Skip $debut -First $combien)) {
    $numero++
    $exemples = ($familles[$cle] | Select-Object -First 3) -join ' '
    $cause_courte = $cle
    if ($cause_courte.Length -gt 240) { $cause_courte = $cause_courte.Substring(0, 240) + '…' }
    Publier "$Titre-cause-$numero" "$($familles[$cle].Count) echecs ; CAUSE: $cause_courte ; EXEMPLES: $exemples"
}

# Plafond : dix annotations par étape. Avec 314 échecs, l'inventaire complet
# (9 paquets) laissait les CAUSES de côté — or ce sont elles qui décident. On
# publie donc les causes d'abord, et l'inventaire sur un échantillon borné :
# identifier 60 tests suffit à instruire, classer 314 échecs est le vrai besoin.
# Réservé à la première tranche : les suivantes n'ont que huit places.
if ($Tranche -le 1 -and $echecs.Count -gt 0) {
    $paquet = 0
    $limite = [Math]::Min($echecs.Count, 60)
    for ($i = 0; $i -lt $limite; $i += 30) {
        $paquet++
        $morceau = ($echecs[$i..([Math]::Min($i + 29, $limite - 1))]) -join ' '
        Publier "$Titre-inventaire-$paquet" ("$($echecs.Count) echecs ; extrait : " + $morceau)
    }
}

if ($rc -eq 0) { exit 0 }

# --- 3. Les échecs sont-ils NOMMÉS ? ---------------------------------------
$fichier_ecarts = Join-Path $PSScriptRoot 'ecarts-windows.txt'
$attendus = @()
if (Test-Path $fichier_ecarts) {
    $attendus = Get-Content $fichier_ecarts |
                Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } |
                ForEach-Object { ($_ -split '\|')[0].Trim() }
}

# Les écarts par CAUSE : « 314 échecs, une seule cause » ne se traite pas en
# listant 314 identifiants. Une ligne ``CAUSE|<message>|<raison>`` accepte tout
# échec dont le message contient ce texte. Le message est ce que le produit a
# réellement dit, pas une catégorie qu'on invente : c'est la condition pour
# qu'un écart par cause reste opposable.
$causes_nommees = @()
if (Test-Path $fichier_ecarts) {
    $causes_nommees = Get-Content $fichier_ecarts |
                      Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } |
                      Where-Object { $_.Trim().StartsWith('CAUSE|') } |
                      ForEach-Object { (($_ -split '\|')[1]).Trim() }
}

$messages = @{}
if (Test-Path $Journal) {
    Select-String -Path $Journal -Pattern '^(FAILED|ERROR) ' | ForEach-Object {
        $ligne = $_.Line
        $separateur = $ligne.IndexOf(' - ')
        if ($separateur -gt 0) {
            $identifiant = ($ligne.Substring(0, $separateur) -split '\s+')[1]
            if ($identifiant -like 'tests/*') { $messages[$identifiant] = $ligne.Substring($separateur + 3) }
        }
    }
}

# Le message de la ligne FAILED ne suffit PAS : 314 échecs de la suite sans
# service portent le même libellé creux — « AssertionError: Regex pattern did
# not match. » — alors que la VRAIE cause est dans les lignes « E » de la
# trace (« Actual message: 'Lecture confinée du bundle indisponible sur cette
# plateforme' »). D'où ``--tb=short`` sur les suites, et la lecture ci-dessous :
# on rattache à chaque test les lignes « E » de SA trace, repérée par l'en-tête
# de traceback « _____ nom_du_test _____ ».
$details = @{}
if (Test-Path $Journal) {
    $courant = ''
    foreach ($ligne in [System.IO.File]::ReadLines((Resolve-Path $Journal))) {
        if ($ligne -match '^_{3,} (.+?) _{3,}$') {
            $courant = $Matches[1].Trim()
        } elseif ($courant -and $ligne -match '^E\s+(.*)$') {
            if (-not $details.ContainsKey($courant)) { $details[$courant] = @() }
            $details[$courant] += $Matches[1]
        }
    }
}

$inattendus = @()
foreach ($echec in $echecs) {
    $connu = $false
    foreach ($motif in $attendus) {
        if ($echec -like "$motif*") { $connu = $true; break }
    }
    if (-not $connu) {
        $morceaux = @()
        if ($messages.ContainsKey($echec)) { $morceaux += $messages[$echec] }
        if ($details.ContainsKey($echec)) { $morceaux += $details[$echec] }
        $message = $morceaux -join ' `n '
        foreach ($cause in $causes_nommees) {
            if ($cause -and $message -and $message.Contains($cause)) { $connu = $true; break }
        }
    }
    if (-not $connu) { $inattendus += $echec }
}

if ($inattendus.Count -gt 0) {
    PublierErreur "$Titre-echec-non-nomme" (
        "$($inattendus.Count) echec(s) hors perimetre connu : " + ($inattendus -join ' ')
    )
    exit 1
}

Publier "$Titre-ecart-nomme" (
    "$($echecs.Count) echec(s), TOUS nommes dans ci/ecarts-windows.txt — " +
    "limite de plateforme connue, pas une regression. A revoir a chaque correctif."
)
exit 0

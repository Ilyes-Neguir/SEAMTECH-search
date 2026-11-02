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
    [Parameter(Mandatory = $true)][string]$Titre
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

if ($echecs.Count -gt 0) {
    $paquet = 0
    for ($i = 0; $i -lt $echecs.Count; $i += 25) {
        $paquet++
        $morceau = ($echecs[$i..([Math]::Min($i + 24, $echecs.Count - 1))]) -join ' '
        Publier "$Titre-inventaire-$paquet" $morceau
    }
}

# --- 2 bis. Les CAUSES, pas seulement les noms ------------------------------
# 71 identifiants ne disent pas POURQUOI. Le message qui suit « - » sur chaque
# ligne FAILED est regroupé ici en quelques familles : « 42 échecs : Capacité
# de plateforme absente : scan_ancre_descripteur ». C'est ce qui permet de
# décider, pour chaque famille, entre adapter le produit et nommer l'écart —
# au lieu de classer 71 lignes à la main.
$causes = @{}
if (Test-Path $Journal) {
    Select-String -Path $Journal -Pattern '^(FAILED|ERROR) ' | ForEach-Object {
        $ligne = $_.Line
        $separateur = $ligne.IndexOf(' - ')
        if ($separateur -gt 0) {
            $identifiant = ($ligne.Substring(0, $separateur) -split '\s+')[1]
            $message = $ligne.Substring($separateur + 3)
            if ($message.Length -gt 90) { $message = $message.Substring(0, 90) }
            if (-not $causes.ContainsKey($message)) { $causes[$message] = @() }
            if ($causes[$message].Count -lt 3) { $causes[$message] += $identifiant }
        }
    }
}
$numero = 0
foreach ($cle in ($causes.Keys | Sort-Object { -$causes[$_].Count })) {
    $numero++
    if ($numero -gt 5) { break }  # cinq familles : au-delà, l'inventaire suffit
    Publier "$Titre-cause-$numero" "EXEMPLES[$($causes[$cle] -join ' ')] CAUSE: $cle"
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

$inattendus = @()
foreach ($echec in $echecs) {
    $connu = $false
    foreach ($motif in $attendus) {
        if ($echec -like "$motif*") { $connu = $true; break }
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

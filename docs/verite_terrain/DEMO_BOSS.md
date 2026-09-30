# DÉMONSTRATION BOSS — scénario 10 minutes (SEAMTECH Search)

**Objectif** : montrer, en 10 minutes, que le parcours complet marche en local
sur la machine de l'atelier : dépôt d'un dossier réel → suivi → validation
d'une fiche au clavier → recherche par dimension « 6,60 » → recherche texte
et facettes → ouverture du PDF avec la zone surlignée → fiche retrouvée après
sauvegarde/restauration.

**Prérequis (la veille, 5 min)** :
1. Pile démarrée : double-clic sur « SEAMTECH Search.cmd » (ou
   `bash scripts/recette_locale.sh` pour la preuve complète).
2. Un compte nominatif prêt (ex. `recette`, créé par la recette locale, ou un
   compte opérateur du jour) — et son mot de passe.
3. Un dossier réel de voile à déposer (le dossier du jour, avec sa fiche PDF
   et ses annexes) — la recette locale s'est déjà jouée sur les 7 ZIP du dépôt.
4. Onglet navigateur vierge sur `http://127.0.0.1:3000`.

**Règle du jeu** : aucune saisie d'attente cachée — chaque étape indique
l'ACTION EXACTE, l'ÉCRAN ATTENDU et la phrase à dire. Chrono indicatif.

---

## 0:00-1:00 — Ouverture de session (compte nominatif)

| | |
|---|---|
| **Action** | Ouvrir `http://127.0.0.1:3000` → l'écran de connexion s'affiche. Saisir l'identifiant du compte nominatif (`recette` ou le compte du jour) et le mot de passe. |
| **Écran attendu** | Page `/login` : deux champs (identifiant, mot de passe). Après validation : l'accueil SEAMTECH Search, la navigation du haut (Nouveau, Dossiers, Recherche, Validation, Fichiers, Gabarits, Qualité). |
| **À dire** | « Chaque opérateur a son compte nominatif : chaque validation est tracée à SON nom dans l'audit. » |

> En cas de mot de passe oublié : le compte `recette` est créé par
> `scripts/recette_locale.ps1` ; le mot de passe est dans `.env`
> (`RECETTE_MOT_DE_PASSE`), jamais dans Git.

## 1:00-3:00 — Dépôt d'un dossier réel + suivi du lot

| | |
|---|---|
| **Action** | Menu **Nouveau** → saisir (ou parcourir) le chemin du dossier réel du jour, ex. `C:\SEAMTECH\2026\DAMIER 4 - 250821JA` → **Déposer**. |
| **Écran attendu** | Écran « Nouveau dossier » : le dépôt répond avec un **numéro de lot** ; la fiche extraite s'affiche (statut **« Non vérifiée »**/`a_valider`) et le **suivi du lot** se met à jour en direct : progression, dossiers traités, éventuelles échecs avec RAISSON écrite (jamais d'échec silencieux). |
| **À dire** | « La fiche est extraite tout de suite, jamais validée d'office — elle est proposée, pas décidée. Le lot suit chaque dossier : ce qui rate dit pourquoi. » |

Rappel RG13 : l'archive/dossier source est **lue, jamais modifiée**.

## 3:00-5:00 — Validation d'une fiche, 100 % au clavier

| | |
|---|---|
| **Action** | Menu **Validation**. La file est triée confiance croissante puis code. Sans souris : `?` (aide), `J`/`K` pour naviguer, `C` pour focus un champ, `V` pour valider (confirmation si anomalie), `R` puis `Entrée` pour rejeter avec motif. |
| **Écran attendu** | Écran « Validation » : la fiche est en tête de file ; ses champs (client, bateau, type de voile, cotes…) ; le **focus d'un champ surligne la zone et la page** dans le PDF à droite ; `?` ouvre l'aide des raccourcis ; `V` confirme → le statut passe **« Validée »** et le badge « Non vérifiée » disparaît. Le chrono local affiche le temps par décision (aucune mesure n'est envoyée). |
| **À dire** | « Un doigt sur le clavier : la validation va 2 à 3 fois plus vite qu'à la souris, et chaque champ est vérifié contre la zone exacte du PDF. » |

## 5:00-7:00 — Recherche par dimension « 6,60 », puis texte et facettes

| | |
|---|---|
| **Action** | Menu **Recherche** → taper `6,60` dans la barre, `Entrée`. |
| **Écran attendu** | La recherche passe en **mode dimension** : les fiches dont une cote vaut **6,60 ±0,5 %** remontent en premier (les formes `6,6`, `6.60`, `660 cm` donnent le même résultat). Si aucune fiche du dossier déposé n'a cette cote : « Aucun résultat » s'affiche — c'est la réponse HONNÊTE. |
| **Variante à préparer** | Ouvrir la fiche affichée à l'étape précédente, lire une cote à l'écran (ex. `6,45`), taper cette valeur : **la fiche revient immédiatement**. |
| **Action (suite)** | Taper un mot du dossier (le nom du bateau, ex. `DAMIER`), `Entrée` → résultats ; dans la colonne de facettes : cliquer un compteur (client / gabarit / matière) et cocher **« Validées uniquement »**. |
| **Écran attendu** | Résultats avec badge **« Non vérifiée »** sur les fiches non validées ; les facettes à compteurs se recalculent ; « Validées uniquement » exclut les non vérifiées ; l'URL du navigateur est partageable. |
| **À dire** | « On ne cherche pas un nom de fichier, on cherche la VOILE : sa dimension, son client, sa matière. Et on voit tout de suite ce qui n'est pas encore vérifié. » |

> **Note de vérification (honnêteté)** : les 7 ZIP du corpus de recette ne
> contiennent PAS de cote à 6,60 (valeurs réelles observées : 7,45 / 6,79 /
> 6,45…). La valeur `6,60` reste le cas canonique de démonstration du MODE
> dimension ; sur les dossiers du jour, utiliser la variante « cote lue sur la
> fiche » garantit un résultat visible. La recette locale (`recherche-dimension`)
> vérifie les deux : le mécanisme « 6,60 » ET une valeur réelle du corpus.

## 7:00-8:30 — Le PDF avec la zone surlignée

| | |
|---|---|
| **Action** | Depuis un résultat de recherche, cliquer la fiche → clic sur un champ extrait. |
| **Écran attendu** | La visionneuse PDF s'ouvre sur **la page exacte**, avec **la zone du champ surlignée** (c'est la même donnée que le focus clavier de l'étape de validation). Le bouton d'ouverture utilise une **URL présignée à durée limitée** (le PDF n'est jamais exposé en libre accès). |
| **À dire** | « Chaque information de la fiche pointe vers sa zone dans le document source. Zéro doute, zéro ressaisie. » |

## 8:30-10:00 — La fiche survit à une restauration, puis récapitulatif

| | |
|---|---|
| **Action** | (Préparé la veille par la recette, à rejouer devant le boss si besoin.) Lancer la sauvegarde `scripts/backup_postgres.ps1 -DatabaseUrl …` puis la restauration `scripts/restore_postgres.ps1 -BackupFile … -DatabaseUrl …` (ou constater le contrôle `restauration` PASS du rapport `scripts/recette_locale.ps1`). |
| **Écran attendu** | Rechercher la fiche validée : **elle est toujours là**, validée, avec ses zones. |
| **À dire** | « Une machine morte ne perd rien : la sauvegarde est testée par une vraie restauration, pas promise. » |
| **Récap (30 s)** | Compte nominatif → dépôt tracé → validation humaine rapide → recherche par dimension → PDF sourcé → sauvegarde vérifiée. |

---

## Si quelque chose ne va pas (règle : ne rien cacher)

- **Dépôt refusé** : la raison exacte s'affiche à l'écran — la lire à voix
  haute (c'est une preuve de contrôle, pas un raté).
- **Recherche « 6,60 » vide** : c'est attendu si le dossier du jour n'a pas
  cette cote → faire la variante (cote lue sur la fiche).
- **Pile non démarrée** : `bash scripts/recette_locale.sh` (ou la variante
  `.ps1`) fournit le rapport PASS/FAIL ligne par contrôle — montrer le rapport
  plutôt que d'improviser.

*Scénario rédigé le 2026-09-30. Vérifié contre le code (écrans
`frontend/app`, raccourcis `frontend/e2e/validation.spec.ts`, contrats
`docs/API.md`). La validation humaine REF-001..007 et les chronos opérateur
restent à réaliser par le commanditaire — voir
`docs/PROTOCOLE_VALIDATION_HUMAINE.md`.*

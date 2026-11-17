# Purge des données clients — procédure préparée, NON EXÉCUTÉE

**Statut : PRÉPARÉE, NON EXÉCUTÉE.** Aucune réécriture d'historique, aucun
`push`, aucune modification. Ce document existe pour qu'une décision soit prise
en connaissance de cause.

Ce fichier ne reproduit **volontairement aucun nom de client, aucun nom de
bateau, aucun code de chantier**. Les désigner par leur valeur reviendrait à
perpétuer l'exposition que la purge vise à supprimer ; ils sont donc désignés
par leur **chemin** et leur **nature**, ce qui suffit à les purger (les
commandes ci-dessous opèrent par motif de chemin, jamais par contenu nommé).

---

## 1. Inventaire mesuré

| Nature | Chemin / motif | Volume | Gravité |
|---|---|---|---|
| Archives clients réelles | `*.zip` à la racine — 7 fichiers | 4 880 366 o (4 739 084 o empaquetés) | **Haute** |
| Document client réel | `sample_data/CLIENT-7792-SO/` | 166 990 o | **Haute** |
| Vérité terrain **en code de production** | `seamtech_search/fiches/verite_7792.py` | 74 valeurs + nom du client + prénom du dessinateur + n° de commande | **Haute** |
| Copies JSON de cette vérité | `docs/verite_terrain/7792-SO_ffab.json`, `…-complete.json` | ~4,9 ko | Moyenne |
| Empreinte du PDF réel | `seamtech_search/dedup/detection.py` (l. 24, sha256) | 1 ligne | Faible |
| Code de commande diffusé | ~152 fichiers (tests, `docs/verite_terrain`, e2e, scripts, `deliverables/`, composants) | — | Moyenne |
| Nom du client diffusé | 31 fichiers | — | **Haute** |
| Prénom du dessinateur | 5 fichiers | — | Moyenne |
| Noms de bateaux + codes chantier | **dans les noms des 7 ZIP** | — | **Haute** |

Le module de production est le point le plus sensible : ce n'est pas une
fixture de test, c'est du code livré, importé par `seamtech_search/fiches/cli.py`.

### Double cheminement du PDF réel

L'analyse `git filter-repo` montre que le PDF réel est stocké sous **deux**
cheminements dans l'historique : à la racine sous un nom court, puis déplacé
dans `sample_data/`. Une purge doit couvrir les deux.

---

## 2. CE QU'IL NE FAUT PAS PURGER — piège identifié par l'inventaire

Une purge par mots-clés (noms de bateaux) détruirait le produit :

- **`seamtech_search/referentiel_bateaux.py`** — les noms qui y figurent sont
  des **fabricants et modèles du référentiel métier**, pas des clients. Les
  supprimer casserait la fonction première de l'outil (reconnaître un modèle
  dans une requête libre). Vérifié : ce fichier ne contient aucun nom de
  client.
- **Les fixtures synthétiques**, vérifiées génériques (PDF « untitled », texte
  d'exemple, références `CLIENT-123` / `CLIENT-456` / `CLIENT-GENOA` /
  `CLIENT-E2E-TROIS`, `tests/fixtures/**`) — elles ne proviennent d'aucun
  document réel.
- **Les ressources d'interface** (`frontend/public/**`).

Le critère qui distingue données client et référentiel métier : **le nom de
bateau seul est du domaine ; le nom de bateau assorti d'un code de chantier et
d'une date est un dossier client.**

---

## 3. Préalable OBLIGATOIRE — remplacer avant de purger

La purge casse tout ce qui dépend de ces fichiers. **Réécrire l'historique
avant d'avoir préparé les substituts laisserait le dépôt non fonctionnel** :

1. générer une fiche synthétique de référence (même structure, mêmes familles de
   champs, valeurs anonymisées) ;
2. substituer `seamtech_search/fiches/verite_7792.py` par ce jeu synthétique ;
3. régénérer les deux JSON de `docs/verite_terrain/` ;
4. revalider la suite **sur le substitut** avant toute réécriture.

Ce préalable est du travail de fond : ~50 fichiers de tests citent le code de
commande, et les attendus qui en dépendent devront être recalculés sur la
fixture synthétique.

---

## 4. Procédure exacte

Deux natures de purge sont nécessaires, et elles ne se remplacent pas :

- **suppression de fichiers** (les 8 binaires) → `--path … --invert-paths` ;
- **nettoyage de contenu** (identités diffusées dans des fichiers qui doivent
  survivre) → `--replace-text`, qui réécrit les chaînes dans tout l'historique.

```bash
# ── 0. Sauvegarde de sécurité (AVANT tout) ────────────────────────────────
git branch backup-avant-purge main
git tag backup-v1.0.0-avant-purge v1.0.0

# ── 1. Travailler sur un clone miroir FRAIS et COMPLET ────────────────────
git clone --mirror https://github.com/Ilyes-Neguir/SEAMTECH-search.git purge.git
cd purge.git

# ── 2. Fichier de substitutions, HORS dépôt (il contient les chaînes) ─────
#     une ligne par chaîne :  valeur_d_origine==>valeur_de_remplacement
#     Exemple de forme (ne pas recopier tel quel) :
#       NOMDUCLIENT==>CLIENT-ANONYME
#       CODEDECOMMANDE==>0000-XX
#     Ce fichier ne doit JAMAIS être commité.

# ── 3. Purge ──────────────────────────────────────────────────────────────
git filter-repo \
  --path-glob '*.zip' \
  --path 'sample_data/CLIENT-7792-SO/' \
  --path 'docs/verite_terrain/7792-SO_ffab.json' \
  --path 'docs/verite_terrain/7792-SO_ffab_complete.json' \
  --invert-paths \
  --replace-text /chemin/HORS/depot/substitutions.txt \
  --force

# ── 4. Contrôler AVANT de publier ─────────────────────────────────────────
git log --oneline | wc -l            # le nombre de commits doit être cohérent
git cat-file --batch-all-objects --batch-check | awk '$2=="blob"' | wc -l
# grep sur l'HISTORIQUE COMPLET, pas seulement HEAD :
git rev-list --objects --all | grep -iE "\.zip|CLIENT-7792"   # doit être VIDE

# ── 5. Publier (irréversible) ─────────────────────────────────────────────
git push --force --mirror
```

Le PDF réel étant présent sous deux cheminements, `--path
'sample_data/CLIENT-7792-SO/'` ne suffit pas : ajouter l'ancien chemin racine
dans la liste des `--path` si le contrôle de l'étape 4 le retrouve.

---

## 5. Impact mesuré

| Mesure | Valeur |
|---|---|
| Commits dont l'arbre contient les ZIP | **624** |
| Commits réécrits | la totalité de l'historique touché (796 commits analysés) |
| Volume retiré | ~4,66 Mo empaquetés, sur un `.git` de 16 Mo (**≈ 29 %**) |
| Références à réécrire | `main`, la branche de travail, le tag `v1.0.0` |
| Blobs analysés | 3 655 |

**Conséquences à accepter avant d'autoriser :**

- `push --force` sur **`main`** — action irréversible ;
- **tous les clones existants deviennent invalides** : chaque collaborateur doit
  re-cloner ou `git reset --hard origin/main` ;
- **la PR ouverte devra être recréée**, son commit de base changeant ;
- les SHAs cités dans tous les rapports et livrables passés deviennent caducs ;
- les objets purgés peuvent subsister chez GitHub jusqu'à une collecte (vider
  le cache et rouvrir la PR, voire recréer le dépôt, pour une suppression
  effective côté hébergeur).

---

## 6. Vérification après purge

Le contrôle exigé est un **`grep` sur l'historique complet**, pas sur `HEAD` :

```bash
git rev-list --objects --all | grep -iE "\.zip$|CLIENT-7792|verite_7792"  # vide attendu
git log --all --oneline -- '*.zip'                                        # vide attendu
git grep -iE "<nom_du_client>" $(git rev-list --all)                      # vide attendu
```

---

## 7. Ce qui n'a pas été fait, et pourquoi

- Aucune réécriture, aucun `push` : la consigne de session est « ne rien pousser
  vers `main` sans accord explicite et distinct », et une purge de l'historique
  de `main` est une action plus lourde qu'une fusion.
- L'inventaire distingue données client et référentiel métier, pour éviter
  qu'une purge automatique ne détruise `referentiel_bateaux.py`.
- Le préalable de l'article 3 (fixtures anonymisées) n'est pas engagé : il
  conditionne la purge, il ne doit pas être improvisé dans la même passe.

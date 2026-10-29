# Importer les archives réelles dans SEAMTECH Search

Objectif : faire entrer les vrais dossiers de l'atelier dans la base **sous forme
de fiches** (et non de simples rapports d'analyse), sans jamais modifier
l'archive d'origine (RG13).

Trois chemins possibles. Choisir selon la taille de l'archive et le poste.

| Voie | Quand | Ce qu'elle produit |
|---|---|---|
| **A — Navigateur** (`/nouveau`, glisser-déposer) | archive de quelques centaines de Mo ; aucun réglage | 1 lot + 1 fiche par sous-dossier |
| **B — Montage lecture seule + CLI** | archive volumineuse, déjà sur le serveur | 1 lot + 1 fiche par sous-dossier, sans copie |
| **C — Depuis un rapport d'analyse** (`/fichiers`) | un import a déjà été analysé | la fiche du dossier du PDF technique |

Dans tous les cas : **ne pas** utiliser « Importer les fichiers sélectionnés »
en croyant créer des fiches — cette action produit un **rapport** (référence,
matériau, dimensions, PDF de rapport). Les fiches se créent par A, B ou C.

---

## A — Par le navigateur (le plus simple)

1. Ouvrir `http://127.0.0.1:3000/nouveau`.
2. Glisser **le dossier de l'affaire** (pas les fichiers un par un).
3. Attendre la fin de l'envoi, puis la fin du lot.
4. Vérifier dans `/dossiers` : une ligne par affaire, statut `a_valider`.

Limites : l'envoi passe par le navigateur (débit du poste) et par le relais du
serveur ; pour plusieurs Go, préférer B.

---

## B — Montage lecture seule + ligne de commande (archives volumineuses)

### B.1 Monter l'archive en lecture seule

Créer `C:\SEAMTECH\SEAMTECH-search\docker-compose.override.yml` :

```yaml
services:
  web:
    volumes:
      - ./data:/app/data
      - C:\SEAMTECH\Archives:/archives:ro
  worker:
    volumes:
      - ./data:/app/data
      - C:\SEAMTECH\Archives:/archives:ro
```

Remplacer `C:\SEAMTECH\Archives` par le vrai chemin. `:ro` = lecture seule :
l'application **ne peut pas** écrire dans l'archive.

```powershell
cd C:\SEAMTECH\SEAMTECH-search
docker compose up -d web worker
docker compose exec -T web ls /archives
```

La dernière commande doit lister les dossiers de l'archive. Si elle répond
`No such file or directory`, le chemin du montage est faux (attention aux
espaces et aux accents : garder les guillemets dans le YAML n'est pas
nécessaire, mais le chemin doit être exact).

### B.2 Déposer le lot

```powershell
# tout un niveau de l'archive (récursif : chaque dossier contenant un PDF de fiche)
docker compose exec -T web python -m seamtech_search.fiches.cli lot "/archives" --database-url "$SEAMTECH_DATABASE_URL"

# ou une seule affaire
docker compose exec -T web python -m seamtech_search.fiches.cli deposer "/archives/NOM-DE-L-AFFAIRE" --database-url "$SEAMTECH_DATABASE_URL"
```

Sortie attendue, par dossier :

```
Lot #12 créé (/archives).
  statut : termine — 8 traité(s), 0 échec(s), 0 restant(s)
```

Les échecs sont listés avec leur raison **lisible** :
`aucune fiche de fabrication reconnue…`, `aucun PDF dans le dossier`,
`aucun code de fiche lisible…`. Un échec n'interrompt jamais le lot.

### B.3 Contrôler (sans jamais afficher de données client)

```powershell
docker compose exec -T postgres psql -U seamtech -d seamtech_search -c "SELECT statut, count(*), sum(nb_pieces) FROM lot_dossier GROUP BY statut;"
docker compose exec -T postgres psql -U seamtech -d seamtech_search -c "SELECT count(*) FROM fiche;"
```

Puis `/dossiers` dans le navigateur : chaque affaire doit apparaître en
`a_valider`, avec son gabarit détecté, son client et son bateau.

---

## C — Depuis un rapport déjà analysé (`/fichiers`)

Sur la fiche du rapport, bouton **« Créer la fiche dans la base »**. Le serveur
dépose le dossier qui contient le PDF technique identifié, avec la même
transaction et la même idempotence qu'un dépôt classique.

Messages possibles :

| Réponse | Signification |
|---|---|
| `Fiche X créée (N pièces jointes)` | la fiche existe, elle est dans `/dossiers` |
| `Ce dossier avait déjà produit une fiche : rien n'a été dupliqué` | rejeu, idempotence |
| `Fiche non créée : aucun code de fiche lisible…` | renommer le PDF avec sa référence puis redéposer |
| `Cet import n'a identifié aucun PDF technique` (422) | déposer le DOSSIER, pas des fichiers isolés |
| `Dossier hors des racines configurées` (403) | ajouter la racine à `SEAMTECH_ROOT_PATHS` dans `.env` |

---

## Ce que fait le serveur quand un dossier est refusé

Rien n'est supprimé, rien n'est déplacé, aucune ligne de fiche n'est écrite.
La tentative est **tracée** dans `lot_dossier` avec sa raison. Les causes
ordinaires :

| Raison | Cause | Action |
|---|---|---|
| `aucune fiche de fabrication reconnue dans ce dossier` | le dossier ne contient qu'un plan, une facture, un PDF sans libellé de fiche | vérifier que la fiche technique est bien dans ce dossier |
| `aucun PDF dans le dossier` | sous-dossier vide ou pièces seulement | déposer le dossier parent |
| `aucun code de fiche lisible` | ni le PDF ni le nom du fichier ne portent de référence | renommer le PDF avec sa référence (ex. `ZZ 001_ffab.pdf`) |
| `PDF illisible` | fichier tronqué / protégé | remplacer le PDF |
| `deja_traite (lot #N)` | le dossier a déjà produit une fiche | aucune action, c'est l'idempotence |

---

## Précautions

- **Ne jamais** lancer `docker compose down -v` : cela supprime la base et les
  documents stockés.
- L'archive d'origine reste la source de vérité : le montage est en `:ro`,
  l'application lit et n'écrit jamais dedans.
- Un dossier déposé deux fois ne crée pas de doublon (empreinte SHA-256 du PDF
  de fiche + clé d'idempotence).
- Aucun nom de client ni contenu de fiche n'est journalisé dans les sorties
  ci-dessus : uniquement des statuts, des compteurs et des raisons.

---

## Les accessoires (Lazy Bag, fourreau de gse, sac de pont…)

Les dossiers `ACCESSOIRES 20xx` contiennent les mêmes pièces que les voiles
(`.PLX` du traceur, `.dxf`, `.xin`, `.3dm`, `.mi2`, photos) et une **fiche PDF
imprimée**. Trois formulaires ont été mesurés sur les documents réels :

| Formulaire | Où est le code | Champs lus |
|---|---|---|
| `Fiche de Lazy Bag` (paysage) | ligne NUE sous le titre (`ST64`) ; « Commande » est imprimé sans valeur | type, code, client, logo, longueur du lazy/gousset |
| `Fiche de fourreau de gse` (paysage) | après « Commande » (`Commande ST96`) | type, code, client, longueur du fourreau |
| `Fiche de Sac de Pont` (A4 portrait) | après « Commande » (`Commande ST 074b`) | type, code, client (+ référence d'affaire), bateau, couleur, logo, longueur du sac |

Règles appliquées, visibles dans l'interface de validation :

- la fiche est classée `accessoire` (et non « voile ») : elle apparaît dans la
  file de validation avec son type et son code ;
- **une seule** dimension nommée est convertie en mètres (celle de la ligne
  « Longueur du … ») ; les autres restent du texte, jamais une cote rebaptisée ;
- les plans du traceur (`TAUD DE GV.pdf`, `ST40 FINI.pdf`, `ST298.pdf`) n'ont
  **aucune couche texte** : ils sont comptés comme pièces/refus explicites,
  jamais comme une fiche à moitié lue ;
- un dossier dont le seul PDF est un plan est refusé avec la raison
  `aucune fiche de fabrication reconnue` — c'est le comportement attendu ;
- **le nom du client est celui que le formulaire imprime** (les archives
  contiennent « VBO Sails » sur un formulaire de 2025 et « Voilerie VBO » sur
  celui de 2024 : les deux sont réels, aucun n'est corrigé en douce). La
  référence d'affaire entre parenthèses est conservée à part, après « | »
  (« Voilerie VBO | ST74 - SAOODXJBQ ») ;
- une seule dimension est projetée en mètres : celle de la ligne
  « Longueur du … » du formulaire, astérisque imprimé compris. Les autres
  (hauteur, ralingue, coulisseaux) restent du texte : jamais une cote
  rebaptisée.

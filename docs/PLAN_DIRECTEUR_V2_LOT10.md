# Lot 10 — Ingestion d'une arborescence complète depuis le navigateur

## Objectif

Permettre à l'opératrice de déposer **un dossier complet** (par exemple une année
entière) dans l'écran « Nouveau dossier », sans copie PowerShell et sans traiter
les affaires une par une. Le système doit ensuite :

1. parcourir récursivement tous les niveaux (année, catégorie, affaire,
   sous-dossiers) ;
2. transférer les fichiers en envois bornés et reprenables ;
3. découvrir côté serveur chaque dossier contenant au moins un PDF direct ;
4. créer **un seul lot** durable, suivi ligne par ligne, avec une raison pour
   chaque échec ;
5. reconnaître la fiche de fabrication grand-voile de type FFab sans confondre
   un simple indice de nom de fichier avec une preuve.

Méthodes ECC appliquées et nommées : `search-first`, `tdd-workflow`,
`api-design`, `contract-first`, `frontend-patterns`, `react-patterns`,
`e2e-testing`, `security-review`, `redis-patterns`, `postgres-patterns`,
`error-handling`, `verification-loop` et
`architecture-decision-records`. L'extraction reste déterministe
(regex/géométrie/traces) ; aucun appel d'IA externe n'est fait sur les PDF.

## Décision d'architecture

### Session de téléversement séparée du lot

Le navigateur ouvre d'abord une session :

```text
POST /imports/dossier/televersements
POST /imports/dossier/televersements/{token}/fichiers   (répété)
POST /imports/dossier/televersements/{token}/terminer
POST /imports/dossier/lot {"token_televersement": "…", "recursif": true}
```

La session possède un jeton opaque de 192 bits et un manifeste
`session.json` remplacé atomiquement. Les fichiers arrivent dans un dossier
temporaire de session, sont hachés en SHA-256 pendant l'écriture, fsyncés, puis
promus par `os.replace`. Rejouer un envoi dont la réponse a été perdue est donc
sûr : mêmes octets = déjà reçus ; octets différents = conflit explicite.

Toutes les mutations d'une session sont sérialisées par un verrou fichier
inter-processus. Ce même verrou couvre la découverte des dossiers, la création
du lot et son association au manifeste : une annulation concurrente attend la
fin de cette section critique puis est refusée, et deux créations simultanées
avec le même jeton produisent un seul lot. La réconciliation relit les fichiers
déjà promus si le serveur s'est arrêté entre promotion et sauvegarde du
manifeste.

### Bornes

| Borne | Valeur |
|---|---:|
| Fichiers par session | 100 000 |
| Taille totale par session | 20 Gio |
| Envoi recommandé navigateur | 100 fichiers et 32 Mio |
| Envoi maximal accepté | 200 fichiers et 512 Mio |
| Taille d'un fichier | `SEAMTECH_MAX_FILE_SIZE_BYTES` / configuration |
| Profondeur relative | 32 dossiers |
| Dossiers avec PDF direct par lot | 5 000 |

Il n'y a donc plus de plafond global de 500 fichiers : seules les requêtes
individuelles sont bornées. Le serveur arrête un flux dès qu'il dépasserait le
nombre ou la taille annoncés à la création de session.

### Sécurité des chemins

- chemins relatifs uniquement, normalisés en NFC ;
- refus des traversées, chemins absolus/Windows, composants vides/cachés,
  noms réservés, espaces/points terminaux et caractères de contrôle ;
- collisions exactes, de casse, de normalisation Unicode et
  fichier-contre-dossier refusées ;
- aucun chemin racine serveur n'est exposé au navigateur ;
- rôles opérationnels revérifiés par le backend, pas seulement par le BFF.

Les sessions vivent sous `uploads/lots-televerses`. Elles ne sont pas élaguées
par âge : tant qu'OBS-05 reste ouvert, les fiches déposées référencent les
fichiers locaux pour les aperçus et téléchargements. **Ne pas supprimer** le
dossier d'une session associée à un lot.

## Découverte et choix de fiche

Avec `recursif:true` (défaut), tout dossier non caché et non lien symbolique
contenant au moins un PDF direct devient une ligne du lot, y compris sous des
niveaux année/catégorie. `recursif:false` conserve le comportement historique
(sous-dossiers directs seulement).

Dans un dossier, les PDF directs sont classés par score de détection, puis par
l'indice de nom `ffab` uniquement à score égal, puis par nom. Le scanner choisit
le premier PDF **viable** : code, gabarit et au moins deux groupes de preuves
(identité, type, mesures, matériaux, fabrication). Un code isolé ne suffit pas.
Si aucun PDF n'est viable, le repli historique conserve un formulaire riche
comme candidat principal afin que le dépôt rende un refus explicite plutôt
qu'un faux succès.

## Calibration grand-voile FFab

Le gabarit `FICHE_GV_FFAB_V1` lit de façon générique :

- titre de fiche de fabrication grand-voile et désignation/bateau/client/
  commande/code lorsqu'ils sont corroborés par la ligne fichier ;
- grille `Cotes (tissu)` alignée par en-têtes et positions ;
- matière principale, montage et fil ;
- nombre de lattes et mesures non modélisées conservées comme mesures libres
  tracées, sans perte.

Aucune valeur réelle ni aucun nom réel n'est codé dans les tests : uniquement
des structures synthétiques et des libellés de colonnes.

Au démarrage PostgreSQL, la synchronisation des gabarits embarqués est
strictement additive : elle insère les couples `(code, version)` absents et ne
réécrit jamais une version déjà publiée.

## Parcours opératrice

1. Ouvrir **Nouveau dossier**.
2. Glisser-déposer le dossier racine complet dans la zone prévue, ou cliquer
   **Choisir un dossier**.
3. Vérifier le résumé : nombre de fichiers, taille, dossiers contenant un PDF
   direct et fichiers système/cachés ignorés.
4. Cliquer **Envoyer et créer le lot** et laisser l'onglet ouvert pendant le
   transfert navigateur.
5. Dès qu'un numéro de lot est affiché, le traitement se poursuit côté serveur ;
   suivre les dossiers traités et chaque raison d'échec dans le panneau du lot.
6. En cas d'incident pendant un envoi, seul l'envoi courant est rejoué par le
   navigateur. **Annuler le transfert** supprime la session non transformée en
   lot.

## Validation attendue avant usage réel

- tests service/API de téléversement (`tests/test_lot10_televersement_lot.py`) ;
- tests PostgreSQL de lot récursif, de création idempotente depuis jeton et de
  synchronisation additive du gabarit FFab sur une base existante
  (`tests/test_lot_ingestion.py`) ;
- calibration synthétique FFab et scanner
  (`tests/test_lot10_calibration_gv_ffab.py`) ;
- build TypeScript/Next et parcours Playwright
  (`frontend/e2e/televersement-lot.spec.ts`) ;
- suite complète, Ruff/audit du dépôt et CI verte sur le commit exact.

Après redéploiement sur le poste de validation : rejouer d'abord le petit cas
réel déjà en échec propre, contrôler uniquement des compteurs/statuts/raisons,
puis importer une année complète. OBS-04 (outils `pg_dump`/`pg_restore` absents
de l'image de sauvegarde) reste un point séparé : ne pas considérer la
sauvegarde automatisée comme prouvée tant qu'il n'est pas clos.

## Rollback

Revenir au commit précédent (`a5006f7`) et reconstruire la pile. Aucune
migration destructive n'est introduite ; le nouveau gabarit est additif. Les
fichiers déjà téléversés sous `uploads/lots-televerses` doivent être conservés
si des fiches déposées les référencent.

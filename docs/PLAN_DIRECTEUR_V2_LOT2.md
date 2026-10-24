# Plan directeur v2 — Lot 2

Date : 2026-10-24
Branche de travail : `arena/b02ed560-seamtech-search`
Base : Lot 0b prouvé `5c218c0afa36f57dc9085090770ed06bc482b89e`.

Ce lot ne part jamais de `main` et ne merge rien vers `main` sans autorisation
explicite du donneur d'ordre.

## Objectif du plan

- un référentiel canonique unique des modèles de bateaux ;
- des alias et des fusions explicites, historisés et réversibles ;
- une recherche et des suggestions qui tolèrent casse, accents, ponctuation,
  abréviations connues, préfixes et fautes, sans jamais fusionner en silence
  des suffixes métier distincts (`45`, `45.1`, `45 CC`) ;
- des synonymes de recherche normalisés et versionnés ;
- la route canonique `GET /api/search/suggestions` côté navigateur ;
- un outil de réindexation avec rapport avant/après ;
- migration réservée `026_boat_models_aliases`.

## Écart réel mesuré avant implémentation

L'audit du code au départ du lot a établi les faits suivants :

- aucune table de modèles canoniques, d'alias de bateaux, d'historique de
  fusion ni de synonymes versionnés n'existait ;
- l'identité historique était le couple exact `(bateau.nom, bateau.taille)` :
  `BAVARIA 45`, `Bavaria-45` et `Bav. 45` créaient donc des lignes distinctes
  sans identité commune ;
- `bateau.taille` est un détail extrait (souvent parenthétique), pas un
  second morceau fiable du nom du modèle ;
- les suggestions historiques faisaient un préfixe normalisé puis un repli
  pg_trgm, sans classement explicite exact/alias/préfixe/flou et sans modèle
  canonique ;
- les synonymes ne vivaient que dans la table historique `synonyme`, sans
  graine livrée pour les équivalences sûres du plan ;
- le navigateur n'avait que `/api/recherche/suggestions` ; `/api/search`
  restait la recherche de fichiers et ne devait pas être confondu avec la
  nouvelle route de suggestions fiches ;
- les fonctions SQL de rafraîchissement du texte des fiches existaient, mais
  aucun outil opérateur ne produisait le rapport avant/après demandé.

## Décisions d'identité verrouillées

1. **Le libellé source n'est jamais réécrit.** `bateau.nom` et
   `bateau.taille` restent la trace historique ; seul le nouveau lien
   nullable `bateau.boat_model_id` est posé.
2. **L'identité canonique dérive de `bateau.nom`.** Le libellé complet
   `nom + taille` est conservé comme alias lorsque `taille` existe.
3. **Normalisation conservative.** La clé minuscule plie casse, accents et
   ponctuation, sépare les frontières lettre/chiffre (`45CC` = `45 CC`,
   `29er` = `29 er`) et protège le séparateur décimal entre chiffres. Seules
   les abréviations ponctuées non ambiguës `Bav.`, `ben.`, `duf.` et `jan.`
   sont développées. Aucun suffixe n'est tronqué : `45`, `45.1` et `45 CC`
   ont trois clés différentes.
4. **Aucune fusion inférée.** Deux clés distinctes restent deux modèles tant
   qu'un administrateur n'appelle pas explicitement la fusion. La défusion
   restaure uniquement les bateaux/modèles encore rattachés à la cible
   enregistrée dans l'instantané, afin de ne pas voler une réaffectation
   manuelle ultérieure.
5. **Un modèle décimal exact gagne sur la dimension.** Dans
   `Bavaria 45.1`, le suffixe décimal est une identité de bateau, pas une
   cote de voile de 45,1.
6. **Les synonymes élargissent en OU.** Remplacer aveuglément le texte tapé
   par sa cible ferait perdre les libellés réels qui contiennent déjà le
   terme d'origine (par exemple « Spi Asymétrique » face à
   `spi → spinnaker`). Les sources textuelles votent donc pour la forme tapée
   et pour la forme canonique, la forme tapée restant principale.

## Implémentation

### 1. Migration `026_boat_models_aliases`

`VERSION_SCHEMA_METIER` passe à `026_boat_models_aliases` ; le schéma métier
compte désormais **38 tables** (44 tables PostgreSQL publiques au total avec
les 6 héritées) et la séquence reste continue `001…026`.

La migration crée :

- `boat_models` — modèle canonique, clé normalisée unique, état actif et
  pointeur de fusion auto-référencé ;
- `boat_model_aliases` — alias brut + clé unique rattachée au modèle
  d'origine, résolue vers le modèle final à la lecture ;
- `boat_model_merge_history` — actions `fusion`/`defusion`, acteur, motif et
  instantané JSONB de restauration ;
- `search_synonyms` — terme/cible normalisés, domaine, source et état actif ;
- `bateau.boat_model_id` — lien nullable vers le modèle final, avec index
  partiel ;
- des index trigrammes GIN sur les clés si `pg_trgm` est effectivement
  présent (extension relue dans `pg_extension`, jamais supposée).

Les synonymes historiques sont recopiés dans `search_synonyms`. Les graines
Lot 2 sont limitées aux équivalences sûres : `GV → Grand-voile`,
`spi → Spinnaker`, `gen`/`genoa → Génois`, `m2 → m²`, plus les formes de
libellés de types déjà présentes dans le métier (`voile de portant`,
`spinnaker asymétrique`). À la lecture, la table historique garde priorité
sur une copie Lot 2 du même terme.

Le backfill est exécuté en Python par
`synchroniser_referentiel_bateaux()` pendant la migration : la clé canonique
est une règle métier testée, pas une approximation SQL. Le nom d'affichage
d'un nouveau groupe est déterministe (plus grand nombre de fiches, forme non
entièrement majuscule, plus ancien identifiant).

### 2. Service `seamtech_search/referentiel_bateaux.py`

Le service fournit :

- `normaliser_modele_bateau()` et le libellé d'affichage nettoyé ;
- création/résolution de modèle final et d'alias, avec
  `AliasBateauEnConflit` explicite plutôt qu'un vol silencieux d'alias ;
- liaison d'une ligne `bateau`, synchronisation/backfill idempotent et
  chargement des clés déjà résolues vers le modèle final ;
- classement pur exact > alias > préfixe, recherche floue pg_trgm et
  détection d'un modèle dans une requête libre. Une correspondance
  exacte/alias supprime les fenêtres préfixes plus courtes : chercher
  `Bavaria 45` ne peut pas attirer `45.1`/`45 CC` au seul motif du préfixe
  `Bavaria` ;
- fusion/défusion transactionnelles avec instantané des bateaux, alias et
  modèles entrants ; une défusion peut cibler un `id_historique` précis, qui
  doit alors désigner la même paire source/cible ;
- liste du référentiel avec alias et compteurs réels.

### 3. Recherche, suggestions et persistance

- `rechercher_fiches()` charge les clés du référentiel (cache 30 s par base,
  invalidation explicite après mutation), détecte les modèles dans `q` et
  ajoute la source RRF `modele_bateau`. La réponse expose
  `modeles_bateaux_detectes` ; chaque résultat expose `id_modele_bateau`,
  `modele_bateau` et `modele_bateau_cle`.
- Le filtre historique `bateau=` est résolu en **exact/alias uniquement** vers
  le modèle final ; le nouveau filtre `boat_model_id=` est en liste blanche
  et validé comme entier positif.
- `suggerer()` classe désormais toutes les natures en
  exact > alias/synonyme > préfixe > flou, avec `type_correspondance`,
  `rang`, `score`, `alias` et `id_modele_bateau` lorsque pertinent. Les
  valeurs restent uniquement celles présentes en base ; la table historique
  `bateau` demeure un filet pour une ligne non encore resynchronisée.
- Le backend expose `GET /search/suggestions` et conserve
  `GET /recherche/suggestions` à réponse identique. Le frontend ajoute
  `GET /api/search/suggestions`, conserve l'ancien proxy et consomme la
  nouvelle route. L'UI affiche le type de correspondance, l'alias, les
  modèles détectés, le modèle canonique des résultats et le libellé de source
  « Modèle bateau ».
- `ecrire_fiche()` relie le bateau au modèle final dans la même transaction
  que la fiche, après la résolution historique existante et sans réécrire le
  libellé source.
- Les mutations `POST /referentiel/bateaux/{id}/alias|fusion|defusion`
  exigent un administrateur humain vérifié selon les gardes du Lot 0b
  (service seul : 401 ; opérateur : 403). La lecture du référentiel reste
  derrière le jeton de service. Conflit d'alias : 409 ; modèle inconnu : 404.

### 4. Outil de réindexation

`seamtech_search/reindex_recherche.py` (entry point `reindex-recherche`) :

- simulation par défaut, écriture seulement avec `--appliquer` ;
- mesures avant/après : fiches et vecteurs, bateaux sans modèle, modèles
  actifs/fusionnés, alias et synonymes ;
- en application : resynchronisation du référentiel, recopie des synonymes
  historiques, puis `rafraichir_texte_recherche_toutes()` ;
- sortie texte ou JSON, rapport JSON optionnel ;
- timeout de batch borné à 30 minutes par défaut
  (`--statement-timeout-ms` / `SEAMTECH_STATEMENT_TIMEOUT_MS`) au lieu du
  timeout interactif de 5 s.

Comme les autres CLI opérateur, le processus applique d'abord les migrations
en attente ; la simulation se lance donc après la montée de version du
backend, pas comme un pré-contrôle prétendument sans effet sur le schéma.

## Preuves

`tests/test_lot2_referentiel_recherche.py` prouve le parcours complet, pas un
module isolé :

- test pur de normalisation et des garde-fous CLI ;
- sur PostgreSQL réel en CI : réindexation dry-run puis appliquée des formes
  `BAVARIA 45`, `Bavaria-45`, `Bav. 45`, `Bavaria 45.1`, `Bavaria 45 CC` ;
- suggestions exact/alias/préfixe/flou et synonyme `spi` ;
- recherche par alias, par modèle décimal, par faute et par filtre canonique ;
- conflit d'alias, fusion historisée puis défusion avec restauration du lien ;
- persistance d'une fiche avec libellé source intact et `boat_model_id` posé ;
- endpoints : équivalence des deux routes de suggestions, lecture du
  référentiel, refus 401/403, création d'alias 200 et conflit 409.

Preuves locales déjà obtenues dans le bac à sable (sans PostgreSQL local) :

- `ruff check` sur `seamtech_search` et les tests touchés : OK ;
- `python -m compileall -q seamtech_search` : OK ;
- lot ciblé non-PostgreSQL (normalisation, dimensions pures, grammaire SQL,
  séquence de migrations et compteurs de schéma) : **65 passed, 23 skipped**
  (les 23 sont les épreuves PostgreSQL explicitement ignorées faute de
  service local) ;
- test pur CLI de réindexation ajouté ensuite : vert ;
- `corepack pnpm exec tsc --noEmit` : OK ;
- `corepack pnpm build` (Next.js 16.4.3) : OK, route
  `/api/search/suggestions` présente dans le manifeste.

### Preuve CI au SHA exact

À COMPLÉTER après lecture des quatre workflows GitHub du commit final du lot.
Le lot n'est pas déclaré terminé tant que CI, Qualification native,
Qualification réservés et Perf comparaison ne sont pas verts au même SHA.

## Déploiement et rollback

### Déploiement

1. Sauvegarder PostgreSQL (les quatre nouvelles tables font partie de la
   preuve métier après montée de version).
2. Déployer backend + frontend du même commit ; la migration 026 et son
   backfill s'appliquent au démarrage.
3. Lancer la réindexation en simulation, relire le rapport, puis
   `--appliquer` en fenêtre de maintenance (`docs/DEPLOYMENT.md`).
4. Accepter seulement si le rapport appliqué ne laisse aucun bateau au
   libellé non vide sans modèle et si les requêtes témoins distinguent
   toujours `45`, `45.1` et `45 CC`.

### Rollback

1. `git revert <commits-du-lot-2>` puis redéployer le commit résultant.
2. Les tables additives et `bateau.boat_model_id` peuvent rester en place :
   l'ancien code continue de lire les libellés historiques et ignore la
   couche canonique.
3. Ne pas supprimer `boat_model_merge_history` : c'est la preuve des
   décisions de fusion. Une restauration de sauvegarde n'est nécessaire que
   si le métier veut aussi effacer les décisions prises après la montée de
   version.

## Réserve de confidentialité

Le dépôt GitHub reste public sur décision explicite du donneur d'ordre,
contrairement à l'exigence du plan (repo strictement privé, documents clients
/ Loi 2004-63). Cette dérogation reste portée sous sa responsabilité ; aucun
nouveau document client ni extrait nominatif n'a été ajouté pendant ce lot.
Les exemples du test Lot 2 sont des libellés de modèles synthétiques.

## Périmètre non prétendu

Ce document clôt uniquement le **Lot 2**. Il ne prétend ni l'aperçu produit
« Extrait du document » du Lot 4a, ni les quatre rôles/approbations du Lot 1,
ni l'extraction structurée 3a/3b, ni les fonctions A/B, ni le Lot 6, ni la
préparation DEP. L'apprentissage d'alias depuis les corrections humaines
relève du Lot 3b ; ici, seuls les alias rencontrés à la synchronisation et
ceux posés explicitement par un administrateur existent.

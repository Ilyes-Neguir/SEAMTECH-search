# Plan directeur v2 — Lot 4a

Date : 2026-10-24  
Branche de travail : `arena/b02ed560-seamtech-search`  
Base : Lot 2 prouvé `e31bb2cf5a8e118f9ac6a8b94fc2a83e1b1b3403`.

Ce lot ne part jamais de `main` et ne merge rien vers `main` sans autorisation
explicite du donneur d'ordre.

## Objectif du plan

Afficher dans chaque carte de résultat de la recherche fiches un aperçu
produit **« Extrait du document »** permettant à l'opérateur de juger la
pertinence d'une fiche sans ouvrir systématiquement son dossier. Le lot est
sans migration.

## Écart réel mesuré avant implémentation

L'audit de départ a établi les faits suivants :

- le backend renvoyait déjà `extrait`, mais ce champ était seulement les
  400 premiers caractères de `fiche.champs_texte` — un agrégat de champs de
  fiche, ni centré sur la requête ni nécessairement issu du document ;
- le type TypeScript du résultat déclarait `extrait: string`, mais le
  composant de recherche ne le rendait jamais : la fonctionnalité était donc
  invisible pour l'opérateur ;
- les chunks `resume_champs` produits pour les vecteurs contiennent un
  résumé de champs généré : les présenter comme « texte du document » serait
  une fausse provenance ;
- le document source d'une fiche n'est pas toujours relié par
  `documents.id_fiche` : en production, le lien peut aussi passer par une
  pièce jointe (`fiche_piece_jointe.id_document`) ou par le chemin source de
  la fiche ;
- les lignes `documents` créées avec le statut `metadata` peuvent contenir
  des métadonnées de fiche de repli, pas le texte réellement extrait du PDF.

## Contrat livré

Chaque résultat de `GET /recherche` conserve l'ancien champ `extrait` et
gagne le champ additif suivant :

```json
{
  "extrait_document": {
    "texte": "…",
    "segments": [
      {"texte": "renfort ", "correspondance": false},
      {"texte": "kevlar", "correspondance": true}
    ],
    "source": "document | chunk | fiche",
    "libelle_source": "nom-du-document.pdf",
    "correspondance_requete": true
  }
}
```

Propriétés du contrat :

- `texte` est exactement la concaténation de `segments` et ne dépasse jamais
  **360 caractères** ;
- les correspondances sont des données structurées, jamais du HTML issu du
  document ; le frontend les affiche comme nœuds de texte React et éléments
  sémantiques `<mark>` ;
- `libelle_source` ne contient qu'un nom de fichier ou un libellé de
  provenance — aucun chemin source, aucune clé d'objet, aucun identifiant de
  stockage ;
- lorsque le texte vient seulement des champs, `source="fiche"` et l'écran
  affiche « Aperçu des champs de la fiche », pas « Extrait du document » ;
- un ancien backend qui omet entièrement `extrait_document` reste lisible par
  le nouveau frontend via l'ancien `extrait` ; un `null` explicite du backend
  Lot 4a signifie en revanche qu'il n'y a réellement aucun aperçu.

## Sélection et provenance

L'extrait est calculé uniquement pour la page affichée, jamais pour toute la
fusion RRF. Après classement par correspondance/score plein texte, la
provenance retenue est :

1. un document au statut `extracted`, rattaché par `documents.id_fiche`, par
   le chemin/`path_key` du PDF source, ou par `fiche_piece_jointe` ;
2. un chunk documentaire autre que `resume_champs` ;
3. les champs pondérés de la fiche.

Les documents `metadata` et les chunks de résumé générés sont exclus des deux
premières catégories. Le texte saisi et sa forme étendue par synonymes sont
des variantes de surlignage en OU, cohérentes avec le vote des sources de
recherche du Lot 2.

Pour un candidat d'au plus 2 000 caractères, les segments sont construits
localement en pliant casse/accents/ligatures, puis centrés sur la première
correspondance. Pour un long document, PostgreSQL `ts_headline` choisit le
passage pertinent avec la même configuration plein texte que la recherche ;
ses marqueurs internes sont convertis en segments et ne sont jamais exposés.
Aucun octet de fichier n'est relu, aucun service externe n'est appelé et le
journal de recherche reste inchangé (requête/filtres/nb de résultats, jamais
le contenu de l'extrait).

## Interface

La carte résultat affiche désormais, entre les métadonnées/cotes et les
badges de sources :

- le titre honnête « Extrait du document » ou « Aperçu des champs de la
  fiche » ;
- le nom de fichier ou la nature documentaire quand la source est réelle ;
- au plus trois lignes de texte avec les termes correspondants dans `<mark>` ;
- `data-testid="fiche-extrait"`, `data-source` et un libellé accessible par
  fiche ;
- un `data-code` exact sur la ligne résultat, pour que les parcours navigateur
  ne confondent plus deux fiches quand un extrait cite le code d'une autre.

## Commits du lot

- `6a954f9` — fonctionnalité backend/UI, tests et documentation ;
- `5df97b5` — correction du test HTTP (`create_app(config)`) après l'échec CI
  réel décrit ci-dessous ;
- `f314853` — sélection des candidats allégée (`pg_catalog`, `DISTINCT ON`) ;
- `5b78885` — construction locale des textes courts, `ts_headline` réservé
  aux longs documents, et épreuve PostgreSQL du document long.

## Preuves

`tests/test_lot4a_extrait_document.py` contient **10 tests** : 4 purs et
6 PostgreSQL/HTTP.

Preuves pures :

- segments structurés et reconstruction exacte du texte ;
- contenu hostile de type `<script>` conservé comme texte, sans marqueur
  interne ni `<mark>` sérialisé ;
- fenêtre de 360 caractères centrée sur la correspondance ;
- marqueurs imbriqués/non fermés sans crash ;
- surlignage local insensible à la casse et aux accents avec accents
  d'origine conservés.

Preuves PostgreSQL/HTTP réelles :

- requête `forcees` retrouvée dans un chunk et surlignée `forcées` ;
- document réellement extrait prioritaire et chunk `resume_champs` généré
  jamais affiché comme source ;
- document long centré sur le terme pertinent situé après plus de
  2 000 caractères ;
- liens par pièce jointe et par chemin du PDF source ;
- navigation sans document explicitement repliée sur `source="fiche"` ;
- route `GET /recherche` exposant le contrat complet.

Le parcours navigateur `frontend/e2e/recherche-extrait.spec.ts` ajoute
2 tests live : extrait pertinent avec vrai `<mark>` pour une recherche texte,
et aperçu borné dans le parcours fiche réelle 7792-SO. La porte live de CI
passe explicitement de **10 à 12 tests attendus**, toujours avec 0 flaky et
0 sauté ; la nouvelle spec n'est donc pas un fichier dormant.

Preuves locales du bac à sable (sans PostgreSQL local) :

- RED initial confirmé par l'import absent du contrat Lot 4a, puis tests purs
  verts (**4 passed**) ;
- lot de recherche pur associé : **20 passed** ;
- sélection CI + grammaire PostgreSQL : **28 passed** ; les deux requêtes SQL
  générées par le helper ont aussi été parsées par `pglast` ;
- Ruff, `compileall`, `git diff --check` : OK ;
- `tsc --noEmit` et build Next.js 16.4.3 : OK ; découverte Playwright :
  2 tests Lot 4a.

### Historique CI et correction

Le premier passage complet au SHA `6a954f9` a donné Qualification native,
Qualification réservés et Perf comparaison verts, mais le job backend
Python 3.13 de CI (`40087168997`) a échoué sur un seul test Lot 4a :
`TypeError: create_app() takes 1 positional argument but 2 were given`. Le
job e2e de ce même run était vert. Le test a été corrigé dans `5df97b5`, sans
modifier le code de production incriminé à tort.

Les passages suivants ont ensuite servi à reprouver les deux optimisations
de performance :

- `5df97b5` : CI `40089235416`, Qualification native `40089235479`,
  Qualification réservés `40089235390`, Perf comparaison `40089235399` —
  tous `success` ;
- `f314853` : CI `40090511133`, Qualification native `40090511075`,
  Qualification réservés `40090511039`, Perf comparaison `40090511063` —
  tous `success`.

### Preuve CI au SHA d'implémentation final

Au SHA `5b788858d547eb2c5bc7cfbd7470d64d7b1be8d5` :

- CI : run `40091642503`, **13/13 jobs `success`** ;
- Qualification native : run `40091642468`, `success` ;
- Qualification réservés : run `40091642480`, `success` ;
- Perf comparaison : run `40091642459`, `success`.

Annotations du job backend Python 3.12 `120865360514` :

- PostgreSQL : **289 passed, 0 skipped** (283 après Lot 2, +6 épreuves
  PostgreSQL/HTTP Lot 4a) ;
- file durable Redis : **89 passed, 0 skipped** ;
- couverture globale : **86,03 %**, plancher 85 % ;
- mesure 1 500 fiches : p50 **16,1 ms**, p95 **47,8 ms** — sous le critère
  produit p95 < 100 ms et sans annotation `perf-derive` sur ce job.

Le job e2e `120865360575` est `success`, y compris l'étape live PostgreSQL
dont le garde-fou exige désormais 12 tests au premier essai. La comparaison
de performance donne, pour HEAD et 1 500 fiches, des médianes de trois
passages p50 **18,34 ms** et p95 **49,95 ms** : le surcoût restant est celui
d'une requête d'aperçu limitée à la page, et il reste sous les deux bornes
produit/dérive. La première implémentation mesurait p95 médiane 51,09 ms ;
les deux optimisations ci-dessus ont été ajoutées précisément pour traiter
cet écart au lieu de le taire.

Le commit de consignation du présent rapport est à son tour revérifié sur les
quatre workflows à son SHA exact ; ses identifiants finaux sont rapportés
dans le compte rendu de livraison, sans être pré-écrits ici. Le lot n'est
déclaré terminé que si ce dernier passage est intégralement vert.

Premier passage du commit de consignation `d4f123e` : Qualification native
`40092767394`, Qualification réservés `40092767458` et Perf comparaison
`40092767434` sont verts ; CI `40092767448` compte 12 jobs verts sur 13. Le
job e2e y est refusé par son garde-fou anti-flaky : la suite concurrence
mesure **6 passés, 1 flaky, 0 en échec**, le scénario multi-postes ayant
dépassé 15 s à attendre `validation-app` au premier essai avant de passer au
retry. Ce passage n'est donc pas accepté comme preuve finale malgré
l'absence d'échec fonctionnel après retry ; un nouveau passage complet au
SHA exact du présent ajout est exigé.

## Déploiement et rollback

### Déploiement

1. Déployer backend et frontend du même commit. **Aucune migration, aucun
   backfill et aucune réindexation** ne sont requis : le lot lit les textes
   déjà indexés.
2. Se connecter, chercher un terme connu pour n'exister que dans un PDF
   indexé et vérifier la carte : titre « Extrait du document », nom du
   fichier, passage pertinent et terme surligné.
3. Vérifier aussi une fiche sans texte documentaire extrait : elle doit dire
   « Aperçu des champs de la fiche » et ne jamais prétendre citer le PDF.

### Rollback

1. `git revert 5b78885 f314853 5df97b5 6a954f9` (ordre inverse), puis
   redéployer le commit résultant.
2. Aucun nettoyage de schéma ou de donnée : le lot n'écrit ni table, ni
   colonne, ni index.
3. L'ancien champ `extrait` n'ayant jamais changé, les clients antérieurs ne
   demandent aucune adaptation pendant le retour arrière.

## Sécurité et confidentialité

- L'extrait ne quitte le backend que dans la réponse authentifiée de la
  recherche existante ; il est borné à 360 caractères par résultat.
- Le contenu source n'est jamais interprété comme HTML dans le navigateur.
- Aucun chemin d'archive, `object_key`, bucket ou contenu complet n'est
  exposé par le nouveau champ.
- Aucun appel réseau externe et aucun traitement IA externe ne sont ajoutés.
- Aucun nouveau document client ni extrait nominatif n'a été commité : les
  textes de test sont synthétiques, et le parcours navigateur réutilise la
  fiche d'exemple déjà présente.

Réserve inchangée : le dépôt GitHub reste public sur décision explicite du
donneur d'ordre, contrairement à l'exigence du plan (repo strictement privé,
documents clients / Loi 2004-63). Cette dérogation reste portée sous sa
responsabilité.

## Périmètre non prétendu

Ce document clôt uniquement le **Lot 4a**. Il ne prétend ni les quatre rôles
et approbations du Lot 1, ni l'extraction structurée 3a/3b, ni les fonctions
A/B, ni le Lot 6, ni la préparation DEP. Il n'ajoute pas de numéro de page à
l'extrait : les candidats actuels (`documents.content` et `chunk.contenu`) ne
portent pas de page fiable pour le passage retenu, et inventer cette
provenance serait contraire au contrat.

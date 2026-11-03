# Campagne complémentaire de qualification — état partiel

Cette campagne n'est **pas terminée**. Un résultat vert ci-dessous ne vaut que
pour le périmètre et l'environnement indiqués. Ne pas déclarer l'application
prête pour la production sur la seule base de ce document.

## Méthode

Revue appliquée avec les compétences ECC `production-audit`, `security-review`,
`verification-loop`, `e2e-testing`, `frontend-a11y`, `postgres-patterns`,
`redis-patterns` et `docker-patterns`. Les checklists sont appliquées par
l'agent dans ce contexte ; aucun sous-agent indépendant n'est disponible.
Les fichiers clients et valeurs extraites ne sont pas recopiés ici.

## Mesures

- Suite Python locale avec PostgreSQL 18.6 et Redis 7.4.7 : **1 529 réussis,
  17 ignorés, 40 désélectionnés, 3 échecs attendus**, 4 avertissements ;
  commande `pytest -m 'not s3 and not perf and not recette_corpus and not integration_docker'`.
  Les trois échecs attendus concernent les rotations PDF : ils ne sont pas
  une validation du comportement.
- Matrice dynamique backend : chaque méthode et route enregistrée a été
  sondée avec jeton absent/incorrect et, sur PostgreSQL, avec quatre rôles
  nominatifs, compte de secours et service. Les corps et objets sont
  volontairement minimaux ou inexistants : **ce n'est pas une matrice
  exhaustive des états métier, sessions expirées ou charges valides**.
- Parcours Playwright live isolés : validation 7/7 ; approbations 7/7 ;
  extractions 3/3 ; comparaison 3/3 ; confiance 3/3 ; navigation 3/3 ;
  accessoires 4/4 ; authentification 34/34 ; doublons 2/2 ; concurrence 7/7 ;
  recherche badge, dimension et extrait 2/2 chacun ; téléversement lot 3/3 ;
  pièces de validation 1/1 ; suivi 2/2 ; fichiers 5/5. Ces exécutions ont
  utilisé Chromium Linux et des bases de test recréées ; elles ne prouvent
  pas l'environnement Windows cible.
- Accessibilité : deux tests axe-core WCAG A/AA ont réussi en local sur les
  pages parcourues et les états chargés par le seed, avec contrôles jour/nuit
  limités à recherche et validation. Le test n'est pas encore une porte CI
  live dédiée. Un scan automatique ne remplace pas un lecteur d'écran réel,
  ni la vérification de chaque dialogue et état de page.
- `ruff check .`, compilation frontend et vérification TypeScript ont réussi
  localement. Une campagne complète après toute modification reste nécessaire.
- CI du checkpoint `578b08d` : trois workflows réussis, CI principale en
  échec sur le contrôle OCR navigateur (1 test flaky). La CI post-fusion de
  `main@f1733c8` présente aussi un échec sur cette étape. Une confusion OCR
  entre caractères proches est observée ; aucune correction n'est encore
  prouvée. Ne pas qualifier ce checkpoint de vert.

## Modifications et risques à confirmer

Le checkpoint introduit notamment un refus d'authentification avant validation,
un relais multipart BFF en flux borné, une lecture PDF temporaire bornée,
un traitement de pièces stagées lors d'un PDF modifié, et des réponses 404/422
à la place de deux erreurs 500. Les tests locaux correspondants ont réussi.
Ces modifications exigent encore une revue de sécurité et des tests réels
pour la gestion des flux interrompus, limites multipart et approbations de
pièces sur les variantes de contenu ; le test statique BFF ne prouve pas ces
propriétés à lui seul.

Les changements d'accessibilité non fusionnés ajoutent des noms accessibles,
renforcent des contrastes et rendent des régions défilantes accessibles au
clavier. Le parcours local axe a été relancé après ces changements.

## Portes encore ouvertes

1. Établir une matrice explicite route × rôle × état de session × état
   d'objet avec scénarios et oracles métier, backend **et** BFF.
2. Qualifier la lecture des PDF pivotés, notamment coordonnées, tableaux et
   orientation intrinsèque ; trois scénarios synthétiques restent `xfail`.
   Valider les champs sur un échantillon réel de référence, sans exposer ses
   données dans Git ou les résultats partagés.
3. Tester un transfert d'une année réelle complète dans l'environnement cible,
   et les pannes aux frontières réseau/disque/worker/PostgreSQL/Redis/MinIO.
4. Vérifier Windows, Docker Desktop, PostgreSQL 16, Chrome/Edge et Explorateur,
   migration et rollback sur copie isolée, puis sauvegarde planifiée,
   stockage hors site et restauration cohérente base + `./data`.
5. Réaliser des tests de sécurité complémentaires et la validation multipart
   en flux réelle ; éprouver l'accessibilité au lecteur d'écran et l'endurance
   de plusieurs jours.
6. Corriger l'instabilité OCR sans réduire la portée de l'assertion, relancer
   toutes les portes pertinentes et obtenir une CI verte sur le HEAD exact.

Les octets déposés par lot restent sous `uploads/lots-televerses`/`./data` ;
leur promotion MinIO n'est pas démontrée. Préserver et sauvegarder ces données.

# Rapport de synthèse — risques et blocages du déploiement

**État des preuves : 28 octobre 2026.**  
**Verdict : NON GO pour une mise en production.** L’application possède des parcours qui fonctionnent et une large suite de tests, mais les conditions de sécurité, de récupération et de qualification demandées ne sont pas toutes démontrées. « Non GO » ne signifie pas que tous les parcours sont cassés ; cela signifie que les preuves et protections nécessaires pour accepter le risque de production sont incomplètes.

## Comment lire ce rapport

- **Constaté** : un défaut, une exposition ou un échec a été observé.
- **Non prouvé** : nous ne disposons pas de la preuve demandée ; ce n’est pas automatiquement un bug démontré.
- **Correction à confirmer** : un changement existe dans la branche de travail, mais sa validation complète n’est pas acquise.

Les critères de blocage ci-dessous reflètent votre exigence : ne pas déployer avant qualification et récupération sûres. Il est impossible de prouver littéralement toutes les combinaisons mathématiques ; il faut définir et couvrir les états et scénarios supportés, puis documenter le risque résiduel.

## 1. Les blocages prioritaires

| Priorité | Élément | Statut et pourquoi cela bloque | Conséquence possible |
|---|---|---|---|
| P0 | Confidentialité des documents | **Constaté.** Le dépôt est public et des documents clients réels préexistants y sont suivis. Cela contrevient à votre règle de ne jamais placer de documents clients dans Git. | Des tiers peuvent avoir eu accès à des données sensibles. Supprimer seulement les fichiers actuels ne supprime ni l’historique ni les copies déjà faites. |
| P0 | Sauvegarde et restauration complètes | **Non prouvé en conditions finales.** Un dump PostgreSQL a été restauré en base isolée, mais pas l’ensemble planifié : base + `./data` + stockage hors site + rétention, puis restauration cohérente. | Une panne ou une erreur peut laisser une base restaurée sans les fichiers correspondants, ou des fichiers sans leur état métier. |
| P0 | Fichiers des lots stockés localement | **Constaté : OBS-05.** Les octets déposés par lot restent sous `uploads/lots-televerses`, dans `./data` ; leur promotion vers MinIO n’est pas démontrée. | Sauvegarder uniquement PostgreSQL ou MinIO ne garantit pas de retrouver les pièces des lots. |
| P0 | Mise à jour et retour arrière | **Non prouvé** sur une copie représentative de votre installation existante : migration, données conservées et rollback répété. | Une mise à jour pourrait rendre la version précédente incompatible avec la base ou laisser un état difficile à restaurer. |
| P1 | CI exacte | **Constaté : échec.** Sur le checkpoint distant testé, trois workflows réussissent, mais la CI principale échoue sur l’OCR navigateur ; la CI post-fusion sur `main` échoue aussi à cette étape. | La porte qualité exigée n’est pas atteinte : nous ne pouvons pas certifier le code livré ni écarter une régression associée. |
| P1 | Lecture des PDF pivotés | **Échec observé sur trois scénarios synthétiques** à 90°, 180° et 270° ; ils sont conservés comme échecs attendus. Un correctif envisagé n’a pas été retenu car il pourrait inverser des documents correctement orientés. | Texte, champs, coordonnées ou recherche peuvent être faux ou indisponibles pour certains PDF ; l’étendue sur les documents réels n’est pas encore mesurée. |
| P1 | Environnement réel de déploiement | **Non prouvé.** Les essais décrits ont utilisé Linux, PostgreSQL 18.6, Redis 7.4.7 et Chromium, sans Docker ; votre cible comprend Windows, Docker Desktop, PostgreSQL 16, Redis 7, Chrome/Edge et l’Explorateur Windows. | Un démarrage, une mise à jour, un glisser-déposer ou une sauvegarde peut se comporter différemment sur le poste cible. |
| P1 | Gros dossier réel | **Non prouvé.** Le parcours navigateur antérieur a transféré 122 fichiers synthétiques, mais pas une année réelle complète dans l’environnement cible. | Échec ou lenteur lors de l’envoi d’une arborescence volumineuse ; le traitement peut ne pas atteindre le volume attendu. |

## 2. Causes des échecs et limites observés

### OCR : pourquoi la CI échoue

Le contrôle exige quatre scénarios OCR réussis **au premier essai**. Sur le checkpoint distant, un scénario a réussi après réessai et a donc été classé instable ; la même étape a aussi échoué après fusion sur `main`. Les annotations montrent des confusions de caractères proches, notamment **0 et O**, dans des termes de test générés aléatoirement. Cela explique l’échec observé ; cela ne prouve pas à lui seul la fréquence d’erreur sur les documents réels.

**Pour lever le blocage :** analyser et corriger la cause, conserver une assertion qui vérifie vraiment l’OCR et la recherche, répéter les tests sans réessai salvateur, puis obtenir tous les workflows verts sur le commit exact. Ne pas simplement supprimer le test ou réduire silencieusement son exigence.

### PDF pivotés : pourquoi les tests échouent

Sur les exemples synthétiques pivotés, le texte est ressorti vertical, entrelacé ou inversé au lieu d’être lu dans le bon ordre. Une transformation générale de la rotation a été envisagée puis abandonnée : elle pourrait changer le sens de lecture de PDF dont l’orientation est volontaire. Les trois tests sont marqués `xfail`, donc **ne constituent pas des réussites**.

**Pour lever le blocage :** établir le comportement attendu pour les orientations réelles, les tableaux et les coordonnées ; ajouter des exemples représentatifs ; corriger sans dégrader les PDF déjà lisibles ; vérifier extraction, affichage et recherche ensemble.

### Confidentialité : pourquoi c’est un risque immédiat

L’inventaire du dépôt identifie des documents réels préexistants, et la visibilité du dépôt a été vérifiée comme publique. Nous ne savons pas si ces documents ont effectivement été consultés ou copiés.

**Pour lever le blocage :** traiter l’incident selon votre procédure de confidentialité : restreindre l’accès si nécessaire, inventorier précisément les données exposées sans les republier, décider de la conservation et des remplacements synthétiques, puis planifier séparément une éventuelle purge de l’historique et ses conséquences. Une réécriture d’historique est une opération sensible : elle ne doit pas être faite à la légère et ne peut pas effacer les copies déjà diffusées.

## 3. Autres risques qui empêchent la confirmation

| Domaine | État | Pourquoi / ce qu’il reste à démontrer |
|---|---|---|
| **Autorisations et sessions** | Non exhaustif | Une sonde a couvert les routes backend avec plusieurs identités et des corps minimaux ou objets inexistants. Elle ne prouve pas chaque route backend et BFF × rôle × session expirée/révoquée × état métier × charge valide. Il faut une matrice avec résultat attendu et effets en base. |
| **Écrans secondaires** | Partiellement testé | Des parcours live Comptes/authentification, Comparaison, Confiance, Extractions et Notifications ont réussi, mais pas tous les enchaînements et états possibles. Il faut couvrir les états vide, chargé, erreur, refus et transition utiles par rôle. |
| **Documents réels et exactitude des champs** | Non prouvé | Les fixtures et échantillons synthétiques ne représentent pas toutes les variantes métier : scans, chiffrement, mises en page et erreurs de parser. Une validation champ par champ sur un échantillon réel de référence reste nécessaire, sans publication de valeurs clientes. |
| **Rejeu et pièces modifiées** | Correction à confirmer | Un test a détecté la duplication d’une annexe inchangée quand le PDF change au même chemin logique ; un correctif existe et le test ciblé réussit. Il reste à qualifier les variantes d’annexes, les approbations liées aux anciens octets, les reprises et l’intégrité des liens. |
| **Multipart et pression mémoire** | Correction à confirmer | L’ancien BFF matérialisait le multipart en mémoire avant les limites backend. Un relais en flux borné existe dans le travail en cours ; le test statique ne prouve pas à lui seul son comportement réel en dépassement, interruption, concurrence et échec backend. |
| **Pannes et reprise** | Partiellement testé | Une reprise après redémarrage a réussi précédemment, mais les coupures réseau, disque plein et arrêts du worker, de PostgreSQL, Redis ou MinIO à chaque frontière pertinente ne sont pas tous éprouvés. Risques : travail bloqué, doublons, fichiers incomplets ou statut trompeur. |
| **Sauvegarde planifiée et hors site** | Non prouvé | La première sauvegarde planifiée sur l’image Docker canonique, la rétention, le stockage hors site et la restauration coordonnée de tous les composants restent à vérifier. Le dump restauré seul ne suffit pas. |
| **Sécurité offensive** | Partiellement testé | Des contrôles ciblés ont réussi, mais pas de pentest externe ni de qualification complète des charges malveillantes, limites et pannes. Aucun test ne peut garantir l’absence absolue de vulnérabilité. |
| **Accessibilité** | Partiellement testé | Deux tests axe-core ont réussi localement sur des écrans et états sélectionnés ; cela ne remplace pas un lecteur d’écran réel ni la vérification de tous les dialogues, états et parcours clavier. |
| **Endurance** | Non prouvé | Pas de test continu de plusieurs jours avec surveillance mémoire, espace disque, files et récupération. Des fuites ou accumulations lentes peuvent rester invisibles aux tests courts. |
| **Installation à domicile** | Non mise à jour | Selon le dernier état connu, l’installation à domicile est restée sur une révision antérieure ; aucun déploiement à domicile n’a été effectué durant cette campagne. Son état actuel doit être vérifié avant toute intervention. |

## 4. Ce qui fonctionne déjà — sans dépasser la preuve

- Campagne précédente : parcours principaux, sécurité ciblée, reprise après redémarrage et restauration PostgreSQL isolée réussis dans le périmètre mesuré.
- Campagne complémentaire locale : **1 529 tests Python réussis**, 17 ignorés, 40 désélectionnés et 3 échecs attendus, avec PostgreSQL 18.6 et Redis 7.4.7. Les catégories S3, performance, recette corpus et Docker étaient exclues de cette commande.
- Plusieurs suites Playwright live ont réussi séparément, notamment validation, authentification, approbations, recherche, comparaison, confiance et concurrence. Leur liste et leurs comptes sont dans `docs/CAMPAGNE_PRODUCTION_COMPLEMENTAIRE.md`.
- Le build frontend, TypeScript et Ruff ont réussi localement lors de la campagne. **Ces résultats ne remplacent pas une CI verte sur l’état final exact.**

## 5. Ordre recommandé pour lever les blocages

1. **Confidentialité :** contenir l’exposition et décider d’une remédiation contrôlée du dépôt et de son historique.
2. **Préservation et récupération :** garantir la sauvegarde de `./data` avec la base et les autres stockages ; prouver une restauration complète isolée avant toute mise à jour.
3. **Qualité des documents :** résoudre les rotations, qualifier les rejets OCR/PDF, mesurer les champs sur un échantillon réel autorisé et protéger les pièces/approbations lors des rejeux.
4. **Fiabilité et sécurité :** compléter les matrices de rôles et états, tester le multipart réel et injecter les pannes pertinentes ; corriger et retester chaque défaut.
5. **Cible et exploitation :** reproduire le plus fidèlement possible la pile prévue, qualifier migration/rollback, gros volume, planification des sauvegardes, accessibilité et endurance. Les aspects matériels propres au poste devront être vérifiés lors d’une mise en service contrôlée, pas supposés depuis Linux.
6. **Porte finale :** relancer les suites pertinentes et les parcours humains, publier un bilan de chaque réserve, et obtenir **100 % de CI verte sur le commit exact** avant toute décision de mise en production.

## 6. Traçabilité

- Détail de la campagne : `docs/CAMPAGNE_PRODUCTION_COMPLEMENTAIRE.md`.
- Bilan antérieur et observations OBS-04/OBS-05 : `docs/PLAN_DIRECTEUR_V2_LOT10.md`.
- CI du checkpoint distant : https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/40600774016.
- CI post-fusion sur `main` : https://github.com/Ilyes-Neguir/SEAMTECH-search/actions/runs/40586724101.

**Attention à l’état du code :** le checkout local est sur `main` fusionné comme base, avec des modifications de campagne non commitées ; les résultats CI concernent le checkpoint distant identifié ci-dessus, pas l’ensemble exact de ces modifications locales.

# Plan directeur v2 — Lot 1 : quatre rôles et approbations

Statut : **livré et prouvé** le 2026-10-25 sur la branche
`arena/b02ed560-seamtech-search`, au SHA
`406a6ff7663a36c1d885845d159dd0e9dcb7b947`.

## Objectif du plan

Le Lot 1 remplace les deux anciens rôles par quatre rôles canoniques appliqués
côté serveur — `admin`, `lecture`, `utilisateur`, `approbateur` — et ajoute un
circuit d'approbation instantané, entièrement local : un utilisateur demande
un téléchargement, une modification ou une suppression logique ; un
approbateur nominatif décide ; les deux parties sont notifiées dans
l'application. Aucun e-mail, aucun service externe et aucun traitement IA
externe ne sont ajoutés.

## Écart réel mesuré avant implémentation

L'audit de départ a constaté :

- seulement `administrateur` et `operateur` existaient ; une valeur inconnue
  recevait par défaut un rôle capable d'écrire ;
- le compte de secours partagé pouvait effectuer des actions métier directes
  (corrections, validations, téléchargements) sans attribution nominative ;
- le téléchargement des octets n'était pas soumis à une autorisation nominative
  bornée dans le temps pour les non-approbateurs ;
- il n'existait ni file d'approbation persistante, ni notification applicative,
  ni séparation serveur entre administration technique et décision métier ;
- un changement de rôle n'existait pas comme opération sûre avec révocation
  immédiate des sessions.

## Contrat livré

### Rôles

| Rôle | Droits effectifs |
|---|---|
| `admin` | Comptes et technique uniquement. Ne crée ni ne décide d'approbation, ne corrige/valide/rejette/rouvre pas une fiche, ne télécharge pas directement une pièce. |
| `lecture` | Recherche, consultation et aperçu. Aucune écriture, demande ou téléchargement. |
| `utilisateur` | Dépôts/imports autorisés, ses demandes, annulation de ses demandes, téléchargement uniquement sous approbation valide. |
| `approbateur` | Seul décideur. Corrige, valide, rejette, rouvre et télécharge directement. |

Le compte `secours` est un compte technique `admin` : il peut créer les
premiers comptes nominatifs, mais il ne peut ni décider, ni corriger/valider,
ni télécharger directement. Le défaut d'un rôle omis ou inconnu est désormais
`lecture`.

### Base de données

La migration `027_quatre_roles_approbations` :

- convertit `administrateur → admin`, `operateur → approbateur`, le secours SQL
  éventuel `secours → admin`, et toute valeur inconnue → `lecture` ;
- resserre la contrainte de `utilisateur.role` sur les quatre valeurs ;
- crée `approval_requests`, `in_app_notifications`, leurs index et les index
  uniques partiels qui interdisent deux demandes identiques en attente ;
- révoque toutes les sessions vivantes au moment de la conversion ;
- ne supprime aucune fiche, aucun fichier et aucun historique.

### Approbations

- Types : `telechargement`, `modification`, `suppression`.
- Statuts : `en_attente`, `approuvee`, `refusee`, `annulee`, `executee`,
  `expiree`.
- Création réservée aux comptes nominatifs `utilisateur|approbateur`, motif
  obligatoire, doublon en attente refusé par la base (409 applicatif).
- Détail réservé au demandeur ou à un approbateur ; un tiers reçoit 404 afin
  de ne pas confirmer l'existence de la demande.
- Annulation par le seul demandeur, uniquement en `en_attente`.
- Décision par `POST /approvals/{id}/decide`, approbateur nominatif uniquement.
  Un refus exige un commentaire. L'auto-approbation est interdite sauf si
  `SEAMTECH_ALLOW_SELF_APPROVAL=true` est explicitement assumé.
- Une modification approuvée est exécutée transactionnellement après contrôle
  de révision et se termine en `executee`.
- Une suppression approuvée est strictement logique : statut `supprimee`,
  aucune suppression physique, historique conservé, réouverture possible.
- Un téléchargement approuvé est lié au demandeur et à la pièce exacts pendant
  24 heures. HEAD et Range ne le consomment pas ; le premier GET réussi est
  horodaté.
- La création notifie les approbateurs actifs, la décision notifie le
  demandeur, dans la transaction métier.

### Interface

Les écrans `/approvals`, `/notifications` et `/comptes` sont reliés aux routes
serveur. La fiche propose les demandes adaptées au rôle ; le téléchargement
affiche l'état réel (direct, demande en attente, approuvé jusqu'à une date,
refusé avec motif) ; la cloche publie les notifications non lues ; l'admin
change les rôles depuis Comptes. Toutes les interdictions visibles sont
revérifiées par le backend et par les proxies Next.js.

## Défauts réels trouvés par les parcours CI

Les étapes navigateur ont été gardées strictes : succès au premier essai, sans
flaky ni test sauté. Elles ont mis au jour et permis de corriger :

1. un cache de session navigateur qui conservait l'état anonyme après
   connexion — la session globale est maintenant revalidée à chaque transition
   d'identité ;
2. un PDF source déposé comme document principal mais sans liaison directe
   `documents.id_fiche`/`fiche_piece_jointe.id_document` — la résolution d'une
   pièce reconnaît aussi le chemin source, les pièces jointes anciennes par
   chemin et la clé d'idempotence du lot, et l'affichage ne duplique plus ce
   document ;
3. un nom accessible du lien de téléchargement approuvé différent de son
   libellé visible — `PieceDownload` utilise maintenant le libellé visible à
   défaut d'`ariaLabel` explicite ;
4. une fixture de dépôt placée hors racines d'archive autorisées — le parcours
   dépose désormais sous la racine configurée, comme en production ;
5. un témoin PDF.js choisi seulement parce qu'il était catalogué — le parcours
   de course ne retient que des PDF dont l'aperçu sert réellement `%PDF`.

## Commits du lot

- `6ad0572` — contrats de test rôles/approbations ;
- `476afe2` — backend complet et migration 027 ;
- `446d22e` — canonisation des parcours backend existants ;
- `e99dd09` — interface et proxies par rôle ;
- `622be8f` — parcours navigateur canoniques et garde-fous CI ;
- `eb6fdd4` — session navigateur et garde d'attribution ;
- `0de688f` — liaisons PDF source existantes ;
- `0335b78` — causes d'échec navigateur lisibles en CI ;
- `32627d0` — nom accessible du téléchargement approuvé ;
- `9c731e7` — fixture de dépôt dans les racines autorisées ;
- `406a6ff` — témoins PDF.js réellement servis.

## Preuves au SHA exact

Workflows GitHub Actions terminés avec succès pour
`406a6ff7663a36c1d885845d159dd0e9dcb7b947` :

- CI `40220228482` : **13/13 jobs verts** (backend Python 3.11/3.12/3.13,
  frontend, e2e, intégration, OCR, Docker, sauvegarde, hors-ligne, sécurité
  des dépendances, recettes) ;
- Qualification native `40220228426` : succès ;
- Qualification réservés `40220228422` : succès ;
- Perf comparaison `40220228462` : succès.

Mesures publiées par la CI :

- PostgreSQL : **297 tests passés, 0 skipped** ;
- file durable Redis : **89 tests passés, 0 skipped** ;
- couverture backend : **85,75 %** pour un plancher inchangé de 85 % ;
- Lot 1 navigateur : **7 parcours verts au premier essai** ;
- parcours live canonisés : **8 verts au premier essai** ;
- authentification navigateur : **34 verts au premier essai** ;
- OCR live : **4 verts au premier essai**, doublons : **2 verts au premier
  essai** ;
- parcours machine de la fiche d'exemple déjà versionnée : 705 ms (temps
  machine uniquement ; ce n'est pas un chronométrage humain).

## Déploiement et rollback

### Déploiement

1. Sauvegarder PostgreSQL avant montée de version et conserver cette
   sauvegarde hors du poste applicatif.
2. Déployer backend et frontend du même commit ; la migration 027 s'applique
   au démarrage PostgreSQL.
3. Vérifier `SEAMTECH_ALLOW_SELF_APPROVAL=false` sauf dérogation écrite.
4. Avec `secours` ou un `admin` nominatif, contrôler les comptes convertis,
   attribuer explicitement les rôles `lecture|utilisateur|approbateur`, puis
   faire reconnecter chaque personne (les anciennes sessions sont révoquées).
5. Recetter les quatre parcours : `lecture` ne télécharge pas, `utilisateur`
   demande, `approbateur` décide puis l'utilisateur télécharge, `admin` ne
   décide pas.

### Rollback

1. Arrêter les écritures et conserver un instantané de l'état post-Lot 1.
2. Un retour exact avant Lot 1 exige la restauration de la sauvegarde
   PostgreSQL prise avant déploiement : la migration a converti les rôles, et
   `lecture|utilisateur` n'ont pas d'équivalent non ambigu dans l'ancien
   modèle.
3. Redéployer alors le commit pré-Lot 1 `0c8d659` (backend + frontend) et
   faire reconnecter tous les comptes.
4. Ne jamais effacer manuellement `approval_requests`,
   `in_app_notifications` ou l'historique pour « faire passer » un rollback :
   ce sont les preuves des décisions prises. Toute perte de demandes créées
   après la sauvegarde doit être décidée explicitement par le métier avant
   restauration. À défaut, corriger en avant sur le nouveau modèle.

## Sécurité et confidentialité

- Les décisions humaines exigent une session nominative vérifiée serveur ; un
  identifiant fourni dans le corps ne peut ni attribuer ni autoriser.
- Le jeton de service reste côté serveur ; les en-têtes d'acteur ne sont
  honorés qu'avec lui et sont rapprochés de la session Redis/PostgreSQL.
- Un changement de rôle, une désactivation ou un changement de mot de passe
  révoque les sessions du compte.
- Les téléchargements d'utilisateurs sont liés à une approbation nominative,
  à une pièce et à une échéance ; `lecture`, `admin` et `secours` ne peuvent
  pas utiliser l'approbation d'autrui.
- Aucun nouveau document client ni extrait nominatif n'a été ajouté. Les
  fixtures sont synthétiques ou réutilisent l'échantillon déjà versionné.

Réserve inchangée : le dépôt GitHub reste public sur décision explicite du
donneur d'ordre, contrairement à l'exigence du plan (dépôt strictement privé,
documents clients / Loi 2004-63). Cette dérogation reste portée sous sa
responsabilité ; elle ne doit pas être interprétée comme une autorisation
d'ajouter de nouveaux documents clients.

## Périmètre non prétendu

Ce document clôt uniquement le **Lot 1**. Restent à livrer et à prouver : 3a
(extraction structurée), 3b (liaison au référentiel et écran de correction),
4b (comparaison bloc par bloc), 5 (confiance de réemploi), 6 (cohérence UI et
recette complète) et DEP (préparation du déploiement hors ligne maximal).
Aucun merge vers `main` n'est effectué sans autorisation explicite.

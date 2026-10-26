# Plan directeur v2 — Lot 6 : cohérence UI globale et parcours « 3 clics »

Branche : `arena/b02ed560-seamtech-search`. Base : Lot 5 livré et prouvé à
`868a3c0`. Implémentation Lot 6 prouvée au SHA
`41b01d8b8f2824f1bca7196dfcc479d779eaa84a` par les quatre workflows listés
ci-dessous. `main` n'a pas été touchée. **Aucune migration**, comme prévu au
plan.

## Objectif du plan

Fermer la boucle produit : chaque écran métier atteignable sans impasse, une
navigation qui ne promet jamais un écran refusé au rôle connecté, et le
parcours métier central — chercher → demander → approuver → télécharger —
**mesuré en clics** dans le navigateur, rôle par rôle.

## Audit préalable (ce qui était vrai avant ce lot)

- La navigation et sa matrice de visibilité étaient déjà saines (Lot 1) :
  Validation/Extractions réservés à l'approbateur nominatif, Gabarits/Comptes
  à l'admin, dépôt interdit au rôle lecture, écrans voiles réservés aux
  comptes nominatifs. Elles deviennent des **gardes de non-régression**.
- Le compte de secours est l'**admin d'urgence documenté**
  (`ROLE_SECOURS = ROLE_ADMIN`, libellé « Secours admin ») : dépôt et
  administration technique, mais aucune identité nominative — tous les
  écrans métier nominatifs lui sont fermés. La première version de la
  recette supposait le contraire ; la CI a mesuré le réel (1 échec) et la
  matrice encode désormais le design documenté plutôt qu'une hypothèse.
- Vrais manques de cohérence relevés : depuis `/comparaison` et
  `/confiance`, impossible d'ouvrir la fiche métier d'une voile ; depuis le
  détail d'extraction, « Comparer… » existait mais pas « Confiance… ».

## Contrat livré

### Liens croisés — jamais de lien mort

`code_fiche_metier` est résolu côté **backend** uniquement quand
l'extraction est réellement rattachée à une fiche métier (`id_fiche` → code
de la fiche) : en-têtes A/B de `/comparaison`, fiches sources de la
justification dans `/confiance`. L'écran ne rend un lien `/fiches/{code}`
que dans ce cas ; sinon le code reste du texte. Le détail d'extraction gagne
le lien « Confiance… » (`/confiance?id=`), pendant du « Comparer… » du Lot 4b.

### Gardes statiques (`tests/test_lot6_coherence_ui.py`, suite standard)

- chaque `href` de la navigation correspond à une page Next.js existante ;
- la matrice de visibilité par rôle est figée dans `nav.tsx` (expressions
  exactes : `peutDeposer(role)`, `nominatif && estApprobateur(role)`,
  `nominatif`, `estAdmin(role)`) ;
- les trois liens croisés ci-dessus sont présents dans les composants.

### Recette navigateur comptée (`frontend/e2e/lot6-parcours-3clics.spec.ts`)

Convention de comptage explicite : seuls les **clics** comptent, depuis un
point d'entrée naturel déjà ouvert (résultats de recherche par lien profond,
liste « à décider ») ; la saisie libre (motif) n'est pas un clic.

- **Utilisateur, 3 clics** : résultat → fiche (1), « Demander le
  téléchargement » (2), « Envoyer la demande » (3) → demande en attente
  visible.
- **Approbateur, 3 clics** : ligne de la demande (1), « Approuver » (2),
  « Enregistrer la décision » (3) → statut décidé.
- **Utilisateur, 1 clic** : bouton de téléchargement → octets relus,
  signature `%PDF` vérifiée (pas une page d'erreur).
- **Matrice navigation × rôle** (secours, lecture, utilisateur, admin) :
  liens visibles/masqués conformes, puis **chaque lien promis est ouvert**
  et ne doit pas déboucher sur un refus d'accès.
- **Chaîne liens croisés** : Extractions → « Confiance… » → évaluation → une
  fiche source rattachée (7792-SO) ouvre sa fiche métier.

## Commits du lot

- `6c2829e` — contrat RED (gardes statiques + recette navigateur) ;
- `19a90bb` — `code_fiche_metier` backend, liens croisés frontend, garde CI
  `report-lot6.json` (e2e mesuré 2/3 : l'hypothèse « secours sans dépôt »
  réfutée par le réel) ;
- `41b01d8` — matrice secours = admin d'urgence documenté (design encodé,
  la garde « aucun refus sur lien promis » reste le juge).

## Preuves au SHA exact `41b01d8`

- CI `40253683828` : 13/13 jobs en succès. PostgreSQL **317/0** (inchangé —
  lot sans migration ni logique métier nouvelle côté base), Redis 89/0,
  couverture 86,08 % (plancher 85 %).
- Parcours navigateur au premier essai : **Lot 6 3/3** (3 clics par acteur
  comptés, PDF téléchargé relu en octets, navigation sans impasse sur 4
  rôles, chaîne de liens croisés 7792-SO) ; Lot 5 3/3, Lot 4b 3/3, Lot 3b
  3/3, Lot 1 7/7 ; parcours complet 7792-SO : **578 ms**.
- Qualification native `40253683608`, Qualification réservés `40253683576`,
  Perf comparaison `40253683693` : succès au même SHA.

## Déploiement et rollback

- Aucune migration, aucune donnée écrite : redéployer backend + frontend.
  Vérification : comparer deux voiles et ouvrir une fiche depuis l'en-tête ;
  ouvrir la confiance d'une voile et suivre une fiche source.
- Rollback : retour code au Lot 5 (`868a3c0`), sans effet de bord.

## Sécurité et confidentialité

- Aucun contrôle relâché : les liens ajoutés aboutissent sur des écrans déjà
  gardés (session nominative ; la fiche applique ses propres règles de
  téléchargement du Lot 1). Le secours reste sans identité nominative ; son
  pouvoir d'admin d'urgence est documenté et sa désactivation fait partie de
  la procédure DEP (§4 du runbook DEP).
- Aucun document client ajouté (dépôt public sur décision du commanditaire).

## Périmètre non prétendu

- Le chrono « ouverture → validation < 2 min » reste mesuré côté machine
  (578 ms) ; la part de lecture humaine se confirmera sur le serveur
  d'entreprise (critère de fin de DEP coché sur place).
- L'accueil `/` redirige toujours vers la recherche de fichiers historique
  (comportement documenté, inchangé) ; la recherche métier est le premier
  lien de navigation.
- Restant au plan : **DEP** — transfert hors ligne, preflight/inventaire de
  l'archive réelle, ingestion par vagues sur le serveur d'entreprise
  (`docs/PLAN_DIRECTEUR_V2_DEP.md`) ; la calibration des gabarits
  génois/GV/jade dépend de l'extraction du corpus Dropbox par le
  commanditaire ou de l'accès direct au fonds sur le serveur.

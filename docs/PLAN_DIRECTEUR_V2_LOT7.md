# Plan directeur v2 — Lot 7 : la famille « accessoires »

Branche : `arena/82958474-seamtech-search`. Base : `main` au merge commit
`7b6f46a` (PLAN DIRECTEUR V2 complet). Implémentation Lot 7 prouvée au SHA
`6113121179fadb1069cd06356eec59d64a2987cd` par les quatre workflows listés
ci-dessous. `main` n'a pas été touchée. **Migration `031_accessoires`**
(schema devient 46 tables métier).

## Objectif du plan

Les documents d'accessoires (Lazy Bag, housses, tauds, capotes, coussins…)
deviennent des **documents de première classe** avec leur propre schéma
métier — pas des voiles soustraites. Le schéma voile, les Fonctions A/B et
la liaison voile restent inchangés ; la recherche gagne une **catégorie
toujours explicite** avec facette et badge.

## Cadrage commanditaire

- Décisions figées avant code : échantillons dans Dropbox (téléversement en
  cours — le développement a démarré avant leur arrivée), détection par
  nom de dossier contenant « accessoire », Lot 7 avant le Lot 8 (thème).
- Aucun échantillon accessoire réel n'était disponible au codage : le
  gabarit `FICHE_ACCESSOIRE_V1` est **générique par libellés** (type,
  matière, couleur, dimensions, quantité, bateau + ancre « référence » pour
  le code). Les règles sont des données versionnées en base : la
  calibration sur les vrais PDFs se fera **sans toucher au moteur** dès
  que le commanditaire confirmera la fin du téléversement.
- Confidentialité : aucun document client dans Git (le dépôt reste public
  par choix du commanditaire — réserve de l'équipe maintenue) ; futurs
  échantillons mesurés en local `/tmp`, jamais committés.

## Contrat livré

### Catégorie de fiche (migration 031)

- `fiche.categorie ∈ (voile, accessoire)` + `fiche.categorie_source ∈
  (dossier, document, humain)`, défaut `voile`/`document` = comportement
  historique.
- Proposition au dépôt (`proposer_categorie`, pure et testée) : nom de
  dossier normalisé (accents/casse) contenant `accessoire` → dossier ;
  sinon famille du gabarit gagnant → document ; sinon voile.
- La persistance tranche dans la même transaction que la fiche : une
  catégorie **décidée par un humain n'est jamais écrasée** au re-dépôt ;
  l'autre famille n'est retirée que si elle ne porte **aucune correction
  humaine**.

### Projection accessoire (`accessory_extractions` + `accessory_corrections`)

- Schéma propre : `type_accessoire`, `designation_brute`, `matiere`,
  `couleur`, `dimensions_texte`, `longueur_m`/`largeur_m`/`hauteur_m`
  (NUMERIC > 0), `quantite`, `modele_bateau_brut` ; identité/provenance à
  l'image des Lots 3a/3b (champs JSONB avec valeur brute, page, zone,
  confiance ; anomalies ; score qualité ; gabarit ; empreinte SHA-256 ;
  clé d'idempotence unique).
- **Unités jamais devinées** : `1200 mm x 450 mm x 100 mm` → 1.2/0.45/0.1 m ;
  `120 x 45` reste du texte + anomalie `dimensions_sans_unite` + statut
  `partielle`. Statuts : `complete` (type + cote canonique), `partielle`,
  `a_reprendre`.

### Liaison bateau simplifiée — un modèle ou aucun

- `boat_model_id` nullable unique, contrainte de cohérence
  `accessory_extractions_liaison_coherente` en base.
- Résolution automatique par le **même** `decider_resolution` que les
  voiles (clé/alias exact, bateau de la fiche) ; jamais de fusion floue.
- `lier_accessoire` : modèle existant OU nouveau modèle nommé — pas les
  deux ; décision humaine intouchable par les ré-extractions.

### Corrections et reclassification

- `corriger_accessoire` : liste blanche de champs, validations strictes
  (quantité ≥ 1, cotes > 0, booléens refusés), historique avant/après +
  motif + auteur ; corriger les dimensions re-dérive les cotes quand des
  unités explicites existent ; corriger le libellé bateau rouvre la
  résolution (jamais d'héritage silencieux).
- `changer_categorie_fiche` : projection reconstruite depuis les traces
  persistées (`fiche_champ_extrait`), identité (document, empreinte,
  libellé bateau) reprise de l'ancienne projection ; **refus (409)** si
  des corrections humaines existent sur la projection sortante.

### Recherche et API

- Vue `v_fiche_recherche` recréée avec `categorie` ; filtre
  `v.categorie = %s`, facette « Catégorie » (comptée sans son propre
  filtre, comme les autres axes), `categorie` dans les lignes résultats,
  paramètre `?categorie=` sur `/recherche`.
- Routes `/accessoires/extractions(/resoudre|lier|corriger)` et
  `/accessoires/fiches/{id}/categorie` : lecture nominative, mutations
  approbateur, `record_audit_event`, erreurs traduites 404/409/422/503 —
  même contrat que le Lot 3b.
- Compatibilité historique : bases arrêtées avant la 031 — sonde
  d'existence comme le précédent 026 (littéral `voile`, axe absent).

### Interfaces

- `/extractions` : onglets famille (`?famille=accessoires`) — l'écran
  voiles Lot 3b est inchangé ; `accessoires-app` : file filtrable, détail
  avec provenance par champ, panneau modèle à un modèle (candidats,
  création), corrections avec motif, historique — approbateur nominatif,
  relais Next à rôles re-vérifiés.
- Recherche : facette « Catégorie » en tête (Voiles/Accessoires), badge
  « Accessoire » sur les cartes.
- Fiche : catégorie + source relisibles ; reclassification approbateur
  (`detail_fiche` expose `id_fiche`/`categorie`/`categorie_source`).

## Preuves (CI au SHA exact `6113121`)

- **CI** `40334010255` — success : backend 3.11/3.12/3.13 (PG réel,
  `tests/test_lot7_accessoires.py` : 5 tests purs + 5 PG), frontend,
  intégration, docker, OCR, sauvegarde, recettes ; e2e : **« 4 parcours
  Lot 7 verts au premier essai »** + tous les parcours des lots précédents
  toujours verts (7792-SO 918 ms).
- **Qualification reserves** `40334010160` — success ; **Qualification
  native** `40334010127` — success ; **Perf comparaison** `40334010553` —
  success.
- Seed e2e : un dossier « ACCESSOIRES E2E » (PDF reportlab synthétique)
  déposé pour de vrai → catégorie `accessoire`/`dossier` vérifiée en base
  dès le seed.

## Échecs honnêtes rencontrés (et ce qu'ils ont produit)

1. `methode_principale` NOT NULL (miroir 028) non alimentée → premier
   dépôt accessoire en échec ; la trace CI a confirmé au passage toute la
   chaîne (1.200/0.450/0.100 m, quantité 4, statut complete).
2. Test historique « avant-023 » : la recherche lisait `f.categorie`
   sans garde → compatibilité pré-031 ajoutée (précédent 026).
3. Noms réels assumés faux : `boat_models.nom_canonique` (pas
   `nom_affiche`), `accessory_corrections.id_utilisateur`/`cree_le`.
4. Annotations d'en-têtes FastAPI par tuple local : devenues des query
   params (401) → forme explicite du Lot 3b, vérifiée dans `header_params`.
5. `Zone()` exige ses 4 coordonnées → zone nullable à la reconstruction.
6. **Leçon principale** : la liste blanche `FILTRES_AUTORISES` de
   `rechercher_fiches` écartait `categorie` **en silence** — le test
   service ne prouvait que la présence, l'e2e a exigé l'exclusion. Le
   test PG assert maintenant que TOUTES les lignes filtrées sont des
   accessoires.
7. Un test OCR navigateur pré-existant (lecture 0/O par Tesseract,
   étranger au Lot 7) a flaké deux fois puis passé au premier essai au
   SHA final — consigné, non masqué.

## Limites assumées et suite

- Gabarit v1 non calibré : à l'arrivée des vrais PDFs (confirmation du
  téléversement Dropbox), mesurer 5–10 fiches en `/tmp`, ajuster ancres et
  attentes anonymisées ; aucune ligne moteur à toucher.
- L'écran accessoires v1 n'a pas la visionneuse PDF côte à côte des voiles
  (la provenance page/zone est affichée ; l'ouverture du document reste
  possible depuis la fiche).
- Prochaine étape du plan : **Lot 8** — refonte visuelle (peau uniquement,
  aucune migration) : naval/atelier clair+sombre, bascule persistée,
  ambre en accent de marque unique, navigation groupée par domaine,
  contrastes ≥ 4,5 testés dans les deux thèmes.

## Vérification ECC (skill `verification-loop`, rejouée le 2026-10-26)

À la demande du commanditaire, le Lot 7 livré a été re-vérifié selon le
workflow ECC en 6 phases (`.agents/skills/verification-loop`) :

```
VERIFICATION REPORT
==================

Build:     PASS (pnpm build, rc=0)
Types:     PASS (tsc --noEmit, 0 erreur)
Lint:      PASS (ruff sur seamtech_search/ + tests Lot 7, 0 avertissement)
Tests:     PASS — local : 1027 passed / 112 skipped (hors PG/Redis, sandbox
           sans services) ; CI au SHA 6113121 : 4 workflows success,
           couverture 85.98 % (seuil repo 85 %, cible ECC 80 %)
Security:  PASS — 0 secret, 0 console.log ; identifiants SQL uniquement en
           listes blanches (_CHAMPS_CORRIGABLES, _TABLES_FAMILLE,
           constantes) ; 6/6 routes FastAPI sous verifier_auth + acteur ;
           relais Next à rôles re-vérifiés
Diff:      33 fichiers (+3482/−66) ; zones figées intactes
           (extractions_voile, liaison_voile, comparaison, confiance :
           diff vide ; anciennes migrations SQL inchangées)

Overall:   READY
```

Note honnête : la couverture locale de `accessoires.py` est de 48 % sans
PostgreSQL (sandbox) — les chemins PG sont ceux prouvés par la CI au SHA
exact. Aucun correctif issu de cette re-vérification.

## Mesure sur échantillons réels (2026-10-26, Dropbox — structures seules)

Le téléversement contient déjà les arborescences accessoires 2024/2025/2026
(dizaines de dossiers « LAZY BAG 1P/2P ST5xx », housses, cagnards…). Un
dossier type contient : série DXF de découpe numérotée, `ST###.dxf`, `.xin`,
`.PLX` (coloris dans le nom de fichier), `ST### FINI.pdf` et une « Prises
mesures … .pdf ». Mesures faites en local (aucun document committé, aucune
donnée nominative consignée) :

- **Les deux PDF sont des images** (1 page, 0 mot texte) : le « FINI » est
  un plan coté (cartouche « Commande : ST### », « Unité : cm », options du
  type « SANS LATTES / SANS LOGO ») ; la « prise de cotes » est le formulaire
  maison « Fiche de prise de cotes — LazyBag » (champs imprimés : N° de
  commande, type de bateau, type de lazybag, bordure en mm, nuancier
  Sunbrella à cocher ; valeurs **manuscrites**, cotes en cm sur schéma).
- Conséquences honnêtes : les dépôts réels passeront par l'OCR ; les
  libellés imprimés sont de bonnes ancres, les valeurs manuscrites resteront
  souvent à confirmer par l'approbateur (l'écran de correction existe déjà).
- Pistes de calibration (à valider avec le commanditaire, hors code Lot 7) :
  ancres supplémentaires (« prise de cotes », « type de bateau », « couleurs
  sunbrella », « N° de commande »), code depuis le motif `ST###`, et question
  ouverte de l'unité documentaire (« Unité : cm ») pour les cotes nues des
  plans — aujourd'hui elles restent du texte, jamais converties.

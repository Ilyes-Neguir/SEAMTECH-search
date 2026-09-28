# Jeu de référence réel — reprise du 2026-09-28

## Périmètre et état

Ces observations portent uniquement sur les **7 ZIP présents et hachés dans le
Dépôt** le 2026-09-28 ; elles ne représentent pas une archive complète non
fournie. Les SHA-256 sont consignés dans le rapport de livraison complet.

Le corpus a été ré-extrait dans `/tmp/seamtech-reprise-20260928`, séparément du
répertoire temporaire précédent, après contrôle des noms et CRC. Les 7 ZIP
originaux correspondent aux empreintes du rapport initial et sont restés
identiques. Les manifestes détaillés et les valeurs métier restent hors Git.

## Les sept fiches candidates

Une fiche candidate réelle a été retenue dans chacun des sept ZIP. Les codes
restent anonymisés ; aucune valeur tirée des PDF n'est reproduite ici.

| Code | ZIP représenté | Gabarit du parseur SEAMTECH | Champs de fiche produits | Anomalies générées | État de validation |
|---|---|---|---:|---:|---|
| REF-001 | AQUILA | non reconnu ; filet de reprise | 0 | 1 | A_VALIDER |
| REF-002 | ATTALIA | non reconnu ; filet de reprise | 1 | 1 | A_VALIDER |
| REF-003 | BAVARIA 32 | non reconnu ; filet de reprise | 0 | 1 | A_VALIDER |
| REF-004 | BAVARIA 34 | `FICHE_GENOIS_V1` | 5 | 0 | A_VALIDER |
| REF-005 | DAMIEN 4 | non reconnu ; filet de reprise | 0 | 1 | A_VALIDER |
| REF-006 | DEHLER 39 | non reconnu ; filet de reprise | 1 | 1 | A_VALIDER |
| REF-007 | GIB SEA 284 | non reconnu ; filet de reprise | 1 | 1 | A_VALIDER |
| **Total** | **7 ZIP** | **1 gabarit reconnu / 6 reprises** | **8** | **6** | **0 fiche validée** |

Les 8 clés de champ émises par le parseur étaient : `fiche.bateau`,
`fiche.client`, `fiche.designation`, `cotes.finie.sf_m`, `cotes.finie.spa_m2`,
`libre.commande`, `libre.6_45_np_surface`, `libre.7_45_np_surface`. Elles
représentent des **propositions machine non vérifiées**, pas des valeurs de
vérité terrain. Six ont une zone localisée ; deux n'en ont pas. Les confiances
sur les 8 enregistrements vont de 0,60 à 0,99 (moyenne 0,8325). Les détails
bruts, valeurs normalisées, chemins source, zones et messages d'anomalie sont
gardés dans les manifestes privés sous `/tmp/seamtech-reprise-20260928/`.

Aucune validation humaine n'a été faite. Aucune fiche n'est déclarée valide,
aucune correction métier n'a été inventée et la calibration reste verrouillée.

## Couverture du corpus PDF

- `pdfplumber`/`pypdf` et l'inventaire applicatif ont confirmé **21 fichiers PDF
  natifs**, soit 14 contenus distincts par SHA-256 ; les 7 autres fichiers sont
  des copies identiques présentes dans les ZIP imbriqués.
- Les parseurs trouvent **53 pages physiques** et **30 pages distinctes** ; les
  14 contenus PDF distincts sont au-dessus du seuil de 20 caractères natifs sur
  chacune de leurs pages. Aucun PDF illisible ou PDF scanné n'a été relevé par
  l'inventaire applicatif.
- Le corpus contient aussi **une image raster** (un scan d'une page). Elle ne
  fait pas partie des 21 PDF ; son OCR réel a échoué, Tesseract n'étant pas
  installé.
- L'outil de détection structurelle a trouvé 7 candidats et 6 familles de
  gabarits de mise en page. Le classifieur textuel les a tous classés « plan »
  (0 indicateur automatique « technique » dans le parcours upload) ; ce
  désaccord est une réserve fonctionnelle à résoudre, pas une validation
  automatique des fiches.

## Champs à contrôler par une personne

Pour chaque REF : référence, client/chantier, bateau et taille, type de voile,
gamme, dates, quantité, dimensions et unités, matériaux/grammage/épaisseurs,
galons, jonctions, finitions, options, renforts, remarques, valeurs libres,
anomalies RG16, confiance, pages/zones et cohérence avec le PDF. Les groupes
matières, galons et jonctions n'ont pas produit de champ de fiche reconnu dans
cette extraction ; leur absence de proposition ne prouve pas qu'ils sont
absents du document.

## CSV et confidentialité

`docs/templates/SUIVI_VALIDATION_REFERENCE.csv` reste le **modèle synthétique**
du dépôt : ses tests de contrat imposent des lignes uniquement marquées
`EXEMPLE_SYNTHETIQUE`, les 15 colonnes, le BOM UTF-8 et le séparateur `;`. Il
n'est pas utilisé pour publier des valeurs clients non validées. Les codes,
chemins relatifs détaillés, empreintes de chaque PDF, valeurs brutes/normalisées
et zones sont conservés dans le répertoire de travail privé. Le suivi public
par code et statut `A_VALIDER` est ce document.

Le jeu contient 7 fiches candidates, moins que la cible de 20 ; ce chiffre
reflète les seuls ZIP fournis. Même après validation humaine, il ne couvrira pas
les gabarits absents ni l'archive complète.

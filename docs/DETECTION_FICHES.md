# Détection structurelle des fiches — lexique configurable et double vue (PR 1)

## Le problème corrigé

La Phase 0 a mesuré un angle mort : le recensement des fiches s'appuyait sur le seul classifieur du
dépôt (`seamtech_search/anchors.py`, liste `TECHNICAL_ANCHORS`). Des fiches réelles d'une variante
« génois » portant `Guindant`, `Bordure`, `Tissu`, `Surface`, `Navire`… sortaient du vocabulaire prévu
et étaient classées `plan_pdf` — donc absentes du recensement des fiches, de l'indexation technique et
des familles de gabarits. Sur 10 000 fiches à variantes multiples, ce sont des centaines de documents
que le classement actuel peut cacher.

## Le correctif : un second signal, indépendant

`seamtech_search/detection_fiches.py` décide qu'un PDF est **candidat fiche** quand il combine :

1. **assez de vocabulaire de cotes** — au moins `seuils.vocabulaire_min` termes du lexique (par défaut 2),
   comparés mots entiers, sans accents ni casse ;
2. **une structure de tableau détectable** — soit une grille réellement tracée (lignes/rects vus par
   pdfplumber, `extract_tables` non vide), soit une grille inférée des positions : au moins
   `seuils.nb_colonnes_min` (3) colonnes et `seuils.nb_lignes_min` (3) lignes de mots alignés.

Le verdict est **expliquable** : termes trouvés, comptages colonnes/lignes, présence de grille, score
pondéré (`ponderations`) et motif d'exclusion éventuel (`vocabulaire insuffisant (1/2)`,
`structure de tableau non détectée (colonnes 1, lignes 1, grille False)`,
`sans couche texte (scan probable)`). Les scans sans couche texte et les PDF sans vocabulaire restent
exclus ; les fichiers non-PDF (xlsx, txt, .XIN) ne passent jamais par la détection.

## Le lexique configurable (`config/lexique_fiches.json`)

Le vocabulaire n'est plus codé en dur. Le fichier, versionné et documenté, contient :

- `version` — à incrémenter à chaque enrichissement ;
- `vocabulaire_cotes` — les termes du lexique (accents et majuscules autorisés, la normalisation est
  appliquée à la lecture) ;
- `seuils` — `vocabulaire_min`, `nb_colonnes_min`, `nb_lignes_min` ;
- `ponderations` — poids des deux composantes du score.

**Ajout à chaud** : enrichir la liste d'un terme rencontré sur une vraie fiche prend effet au prochain
lancement, sans redéploiement. Le chemin du fichier se change avec `--lexique` (testé : un terme ajouté
fait entrer une fiche jusque-là exclue dans le recensement). Un fichier absent ou invalide arrête
l'inventaire avec une erreur claire — jamais d'analyse à côté.

Le vocabulaire de départ provient des seules fiches réellement disponibles : la fixture
`sample_data/CLIENT-123`, la reconstruction de la fiche de référence
`sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` (valeurs du §13 du plan v3.0, mise en page
approximée — documentée comme reconstruction ; à remplacer par le PDF réel dès réception) et la fiche
génois `sample_data/CLIENT-GENOA/fiche-genois.pdf` (reproduction du point mort). Les vraies fiches
apporteront du vocabulaire inconnu : enrichir le JSON, pas le code.

## La double vue de l'inventaire

`scripts/inventaire_archive.py` rapporte désormais **deux vues** pour chaque PDF natif :

- **(a) vue classifieur** — la classification actuelle du dépôt (`technique` / `plan` / scan probable /
  erreur), inchangée ;
- **(b) vue structurelle** — candidat fiche ou non, avec score et composantes.

et une section **« désaccords »** (JSON → `detection_structurelle.desaccords`, aussi en console) :

- `rates_par_le_classifieur` : vus comme fiches par la détection, classés autrement par le classifieur.
  **C'est la liste qui protège l'inventaire réel** : elle dit « voici N documents que le classement
  actuel rate », avec termes et score ;
- `techniques_sans_structure` : l'inverse — fiches du classifieur sans structure détectable (gabarits
  atypiques à examiner).

Le CSV (`inventaire_fichiers.csv`) porte les colonnes `candidat_fiche` et `score_fiche` ; la vue (a)
reste dans `categorie_pdf`.

## Familles de gabarits : empreinte en positions relatives

L'empreinte fine des familles quantifie désormais les positions **relatives à la page** (pas de 2 % de
la largeur/hauteur) quand les dimensions sont connues : un même gabarit imprimé sur deux formats de
page tombe dans la même famille. Sans dimensions, comportement historique (quantification absolue).
Chaque famille rapporte : son identifiant, le nombre de fiches, des spécimens de chemin et le nombre de
mises en page distinctes.

## Garanties (testées)

- l'archive reste rigoureusement inchangée (empreintes SHA-256, tailles, horodatages avant/après) ;
- les trois fiches connues sont détectées, y compris celle que le classifieur rate ;
- plans, scans sans couche texte et non-PDF restent exclus ;
- le lexique est lu depuis la configuration et un ajout à chaud change le verdict ;
- suite complète verte (313 passés / 9 ignorés au moment de la PR), ruff propre.

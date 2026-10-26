# PLAN DIRECTEUR V2 — Lot CAL : calibration du gabarit accessoire sur les formulaires réels

**Statut :** livré sur la branche de session, en attente de fusion.
**Périmètre décidé par la commanditaire :** `calibration` — régler le gabarit
accessoire sur les structures réelles de l'archive (Dropbox), sans brancher
d'indexation réelle (elle aura lieu plus tard, 100 % en local, sur son poste).

## 1. Méthode et confidentialité

- Recensement structurel des arborescences `ACCESSOIRES 2024/2025/2026` et
  `VOILES 2024/2025/2026` (connecteur Dropbox, lecture seule) : seuls des
  **motifs de noms, comptes et extensions** sont consignés — jamais un nom
  de client, un code de commande réel ou une cote.
- Six documents échantillonnés un par un dans `/tmp` (hors dépôt, supprimés à
  la clôture) et mesurés localement (pdfplumber/PyMuPDF) : pages, mots,
  images, libellés imprimés. **Aucun document client n'entre dans Git** ;
  les tests sont 100 % synthétiques.
- Développement TDD : contrat RED `995da08` (ImportError, v2 absente) →
  GREEN `c7f64c8`. Skills ECC appliquées : `tdd-workflow`,
  `python-testing`, `regex-vs-llm-structured-text` (règles déterministes,
  aucun appel externe — cohérent avec le 100 % local).

## 2. Ce que la mesure a établi (motifs, jamais des valeurs)

**Dossiers accessoires** — nom type : `<PRODUIT> [variante] ST###[A|B|C]`
(familles : Lazy Bag 1P/2P/multicoque, sac de pont, taud de GV, cagnard,
housse, toile). Contenu type : DXF de pièces numérotées, `ST###.dxf`,
`.xin`, 1–2 `.PLX` (grammaire de nom incohérente : références tissu, gammes
et couleurs mélangées → **la couleur n'est pas extractible du nom de
fichier**, seul le motif `ST###` y est robuste), 1–3 PDF. En 2024, les
conventions sont plus lâches (`ST##`, `ST0##X`, ~1/3 des dossiers sans code
ST) → tout appui sur le nom de fichier ne peut être qu'une suggestion.

**Six formes de PDF mesurées :**

| Forme | Nature mesurée | Conséquence extraction |
|---|---|---|
| Fiche de prise de cotes (`img….pdf`, scan) | image 1 p., 0 mot — formulaire imprimé maison : « Réservé pour le CLIENT », Type de bateau, Type de LazyBag (cases), Nombre d'ancrages, Type de grand-voile (cases, dont « FullBatten »), Bordure de la Grand-voile, Couleurs Sunbrella (nuancier à cocher), cotes en cm sur schéma ; texte explicatif contenant « coulisseau d'écoute » ; valeurs **manuscrites** ; variante mesurée **sans** « N° de commande » imprimé (la variante du Lot 7 en a un → ≥ 2 variantes) | OCR + ancres sur libellés imprimés ; cases cochées illisibles par un moteur libellé→valeur → approbateur |
| Plan de coupe (`ST### COUPE.pdf`) | image 1 p. — titre « <PRODUIT> ST### », pièces cotées **sans libellé**, multiplicateurs « X# », matière/couleur en gros caractères, cartouche de méthode | détection par le titre ; dimensions non liables (pas de libellé) → jamais inventées |
| Plan « pour devis » | image 1 p. — croquis **entièrement manuscrit** sur papier partenaire, cotes en mètres sans libellé, aucun champ imprimé | rien de structuré ; cas de test négatif |
| Commande boutique (`ST### <RÉF>.pdf`) | **texte natif**, 2 p. — impression back-office e-commerce (« Commande #… », client, total) ; le suffixe « illisible » du nom est la référence boutique | pièce jointe, pas une fiche : le scanner doit la perdre contre la vraie fiche |
| Fiche de fabrication voile (`FFAB_<type>_<modèle>_######.pdf`) | **texte natif**, 1 p. — « FICHE FAB SAILONET <TYPE> », REF VOILE, TISSU, tableau de cotes (guindant/bordure × tissu/fini), MONTAGE, LAIZES, RALINGUE, LATTES, finitions | **hors périmètre CAL** (voir §5) |
| Rapport logiciel voile (`me######.pdf`) + `me######.csv` | **texte natif**, 4 p., libellés anglais (Boat, Order Ref., Measurement Check List) ; CSV sans en-tête : liste de pièces (`pièce.dxf ; id ; réf tissu ; qté`) | **hors périmètre CAL** (voir §5) ; le CSV n'est pas une source de cotes |

## 3. Livraison : `FICHE_ACCESSOIRE_V1` version 2

- **Même code de famille, version 2** (`GABARIT_ACCESSOIRE_V2`) ; la v1
  publiée reste intacte au registre (RG11) et seule la v2 reste ACTIVE
  (`initialiser_gabarits`). **Mêmes cibles que la v1** — projection Lot 7 et
  migration 031 inchangées, **aucune migration**, extracteur `accessoire-v1`
  conservé (la projection n'a pas changé ; la version de gabarit est portée
  par chaque extraction).
- **Détection** : corpus v1 + `lazybag` (un mot, forme réelle du
  formulaire), `sac de pont`, `cagnard`, `prise de cotes`,
  `réservé pour le client`, `couleurs sunbrella`, `ancrages`,
  `bordure de la grand-voile`. Le formulaire contient « FullBatten » (ancre
  GV) et « coulisseau » au singulier (l'ancre GV est au pluriel) : il reste
  accessoire avec une marge nette ; une vraie GV marque **0** en accessoire.
- **Libellés** (du plus spécifique au générique, bornes `stop` partout) :
  `N° de commande…` → `fiche.code` ; `Type de bateau…` → `modele_bateau` ;
  `Couleurs Sunbrella` / `autres coloris` → `couleur` ; matière, dimensions
  et quantité sur leurs libellés réels.
- **Gardes anti-erreur démontrées par test :**
  - l'ancre `type` nue est **retirée** : elle lirait « Type de bateau : … »
    comme type d'accessoire. L'ancre gardée `type :` (deux-points adjacent)
    conserve le générique du Lot 7 sans cette fuite ;
  - `teinte` est **écartée** : le texte imprimé « teinté masse » se
    normalise en « teinte… » et lirait « masse » comme couleur ;
  - `nombre` nu est **écarté** : « Nombre d'ancrages » n'est pas une
    quantité ;
  - en-têtes de cases à cocher sans valeur sur leur ligne et nombres sans
    libellé (plan de coupe, croquis) → **aucun champ produit** (statut
    `a_reprendre`), jamais une valeur devinée.
- `accessoires.gabarit_accessoire()` expose le gabarit courant ;
  `gabarit_accessoire_v1()` continue de rendre la définition historique.

## 4. Preuves

- Contrat `tests/test_lot9_calibration_accessoires.py` : **13/13** (registre
  v2/v1, cibles inchangées, détection anti-vol dans les deux sens,
  extraction par PDFs synthétiques dont garde « Type de bateau », négatif
  croquis, scanner fiche vs commande boutique).
- Suite complète locale (sélection par marqueurs du dépôt) : **1056 verts**,
  17 ignorés, `ruff check .` propre — aucune régression.
- CI GitHub au HEAD exact : _à remplir après poussée._

## 5. Limites mesurées et suites proposées (hors Lot CAL)

1. **Limite moteur préexistante** (déjà en v1, non corrigée ici — impact
   tous gabarits) : un mot de la valeur égal à une ancre de la même règle
   borne la lecture (ex. « Matière : Tissu … » perd le mot « Tissu »).
   L'approbateur corrige ; à traiter, si souhaité, dans un lot moteur dédié.
2. **Code ST depuis le nom de dossier/fichier** : robuste comme *motif*,
   mais le produit exige aujourd'hui un code lu **dans** le document (pas
   de référence, pas de stockage). Les formulaires sans N° imprimé et les
   plans de coupe partiront donc en reprise complète au dépôt réel. Suite
   propre possible (décision commanditaire) : proposer le code du dossier
   comme **suggestion tracée** à confirmer par l'approbateur — jamais en
   silence. Non implémenté au Lot CAL.
3. **Documents voile texte natif** (`FFAB_…`, `me######.pdf`) : riches et
   extractibles sans OCR, mais aucun gabarit voile actuel ne les vise
   (« FICHE FAB SAILONET », libellés anglais du logiciel). Candidat naturel
   à un lot de calibration voile symétrique — les Fonctions A/B restent
   voile-only, inchangées.
4. **Vérification terrain restante** : l'OCR réel des scans (apostrophes
   typographiques, « N° », lignes manuscrites) se mesurera à l'indexation
   sur le poste de la commanditaire ; les libellés sont des données
   versionnées, ajustables en v3 sans toucher au moteur.

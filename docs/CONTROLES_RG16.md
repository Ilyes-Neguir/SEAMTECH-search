# Contrôles RG16 et échelle de confiance (Lot B)

Les contrôles RG16 sont **indépendants de la confiance** : une valeur lue avec
une confiance maximale peut rester techniquement incohérente. Ils s'exécutent
toujours (`seamtech_search/fiches/anomalies.py`) et leurs résultats arrivent
dans `fiche_anomalie` ; toute anomalie bloque la voie « passage direct ».

## 1. Contrôle de surface (portants uniquement)

Référence : triangle quelconque `½ · SLU · SLE`. Le **facteur** lu est
`SPA / (½·SLU·SLE)`.

| Élément | Valeur |
|---|---|
| Bande acceptée (TOLÉRANCE NOMMÉE) | facteur ∈ **[0,55 ; 1,30]** = [−45 % ; +30 %] |
| Formule du tableur d'origine (triangle équilatéral) | SPA = (√3/4)·SLU·SLE = 0,433·SLU·SLE → facteur **0,866** |
| Triangle quelconque | SPA = ½·SLU·SLE → facteur **1,0** |
| Fiche de référence 7792-SO (valeurs réelles) | SLU 6,60 / SLE 5,50 / SPA 15,71 → facteur **0,8655** (écart −13,4 %) → **accepté** |
| Hors bande (exemples figés en test) | SPA = 9,0 (facteur 0,496) ou SPA = 25,4 (facteur 1,40) → `surface_incoherente` (gravité forte) |

Une tolérance de ±5 ou ±10 % signalerait **la fiche de référence elle-même** :
la bande couvre volontairement les deux formules de l'atelier, avec marge pour
les ronds de chute. Test figé : `TestToleranceSurfaceFigee`
(`tests/test_anomalies_coherence.py`).

Les **interfaces** (génois, grand-voile…) ne suivent pas ce ratio : elles ne
subissent que les contrôles de plage et d'ordonnancement — documenté, testé
(`test_genois_sans_anomalie_rg16`).

## 2. Autres contrôles

- **Plages physiques** (m / m² / cm / kg) : SLU/SLE [0,5 ; 30] m, SF/SHW
  [0,2 ; 12] m, SPA [0,5 ; 300] m², têtière [0,5 ; 60] cm, poids [0,02 ; 30] kg.
- **Ordonnancement** : SLU > SF exigé pour tous ; SLU > SLE exigé pour les
  portants (`cotes_incoherentes`, gravité forte).
- **Champ manquant** : aucun jeu « finie » lu → `champ_manquant`.

## 3. Échelle de confiance (constat B de revue)

| Palier | Valeur | Signification |
|---|---|---|
| Certain | **0,99** | ancre exacte + valeur intégralement bornée (fin de ligne, changement de colonne ou cellule de tableau) + format intégralement consommé par le convertisseur du type (ou type texte à borne naturelle) |
| Lu | 0,90 | lecture correcte mais ambiguïté résiduelle (troncature par libellé stop, extraction partielle du format) |
| Décomposé | 0,85 | sous-valeurs structurées (galons, jonctions, épaisseurs, finitions, options, renforts) |
| Partiel | 0,70 / 0,50-0,60 | reconnaissances partielles, présences douteuses, valeurs non convertibles |

Le palier 0,99 est **au-dessus du seuil structurel le plus strict** (0,98 dans
`config/seuils_confiance.json`) : la voie « passage direct » est atteignable
par construction (§17.14 vise ≥ 50 % de passage direct en Phase 2). Preuve
bout-en-bout : la reconstruction 7792 passe `passage_direct` aux seuils
actuels (`TestPlafondDeConfiance::test_routage_passage_direct_aux_seuils_reels`).
La **calibration** réelle des seuils reste l'affaire de la Tâche 3 (fiches
réelles) : les valeurs actuelles sont les points de départ du §10.3.

## 4. L'échelle de confiance est ORDINALE, pas probabiliste (Tâche 1b, revue du 21/09)

Les valeurs 0,99 / 0,90 / 0,85 sont des **paliers de décision**, pas des
probabilités : elles signifient « lecture déterministe », « lecture avec
ambiguïté résiduelle », « sous-valeur de décomposition » — rien d'autre.

Conséquences impératives :
- le tableau de bord qualité (lot E) affichera des **comptes par palier**
  (`compter_par_palier()` — certain / lu / décomposé / partiel), **jamais une
  « confiance moyenne »** : moyenner des paliers n'a aucun sens et donnerait un
  chiffre faux au commanditaire ;
- `fiche.score_qualite` (colonne du Lot A) reste un indicateur brut de suivi
  par fiche, à ne JAMAIS agréger en moyenne de flotte ;
- les seuils de `config/seuils_confiance.json` comparent des paliers, ils ne
  « convertissent » pas l'échelle en probabilité de justesse.

## 5. Tolérance surface : un filtre de grosses erreurs (Tâche 1c — dette documentée)

La bande [0,55 ; 1,30] est un **filtre de grosses erreurs**, pas un contrôle de
justesse : elle attrape une surface incompatibles avec les cotes (faute de
frappe, mauvaise colonne), elle ne garantit PAS que la surface imprimée est
correcte. **Dette** : dès réception des fiches réelles, mesurer la distribution
du facteur SPA/(½·SLU·SLE) **par type de voile**, puis resserrer la bande par
famille (portant symétrique, asymétrique, ronds de chute…). Aucun resserrement
avant ces données : un filtre trop serré signalerait la fiche de référence
elle-même (facteur réel 0,8655).

## 6. Verrou de calibration (Tâche 1a)

`config/seuils_confiance.json` porte désormais `"calibre": false` +
`"fiches_reelles_utilisees": 0`. Le garde-fou
`verifier_autorisation_validation_lot()` (`seamtech_search/fiches/persistance.py`)
répond « interdit » tant que la calibration n'a pas eu lieu — la future route
`POST /validation/lot` (lot D) devra le consommer et renvoyer **409** avec son
message, ou exiger un acquittement humain explicite (paramètre prévu). Testé :
`TestVerrouCalibration`.

# Empreintes des fichiers de vérité terrain (revue du 21/09, Fait 3)

## La fiche de référence 7792-SO

| Fichier | SHA-256 | Taille | Pages | Dimensions | Caractères extraits | Statut |
|---|---|---|---|---|---|---|
| `sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` (donné au gabarit) | `43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40` | 166 990 o | 1 | 842 × 595 (paysage) | 1 619 | **DOCUMENT CLIENT RÉEL** — reçu du commanditaire le 21/09, remplacé à la place de l'ancienne reconstruction ; le gabarit `FICHE_PORTANT_V1` **v2** est réglé sur ce document |
| Ancienne reconstruction (retirée du dépôt le 21/09) | `9bbc9f50017bbcf8e08cd7955b34fb805e93c06d10ecb28dd124f1bc0737a317` | 2 483 o | 1 | — | 951 | **RECONSTRUCTION** (valeurs du §13, mise en page approximée) — conservée ici pour l'historique des empreintes uniquement |

## Le fichier `uploads/` de mon côté (la confusion expliquée)

| Fichier | SHA-256 | Taille | Pages | Identité |
|---|---|---|---|---|
| `uploads/hIumoeoP_pzFX825dwQqyJLQmzQCpPu1s0v8rKRelkc.pdf` | `848ba6a1ea0ffe9cc55fcdb977042ac892d09b3402a4fbb5b34bfcaca45e9647` | 455 226 o | **61** | **le PLAN v3.0**, pas la fiche |

Sorties brutes du contrôle (21/09, après remplacement de la fixture) :

```
$ sha256sum sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf
43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40  sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf
$ python3 -c "import pdfplumber;p=pdfplumber.open('sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf');print(len(p.pages), p.pages[0].width, p.pages[0].height)"
1 842 595
```

## Réglage du gabarit sur le document réel (21/09)

Le document réel n'a ni la mise en page ni la géométrie de l'ancienne
reconstruction : ligne de titre unique avec code rejeté à droite, **grille de
cotes tracée à colonnes alignées** (Guindant (SLU) … Poids (tissu), lignes
« Mesures Dessin » / « Mesures Finies »), blocs épaisseurs / galons /
finitions / options / renforts en zones. Le gabarit `FICHE_PORTANT_V1` passe
en **version 2 dans le registre JSONB** (aucune coordonnée en dur : ancres,
alignements de colonnes et ruptures d'espacement), les règles v1 restant en
tête pour les fixtures synthétiques (« premier lu gagne »).

**Mesures sur le document réel (21/09)** :

- `validate_extraction.py --verite docs/verite_terrain/7792-SO_ffab.json --moteur gabarit` :
  **6/6 = 100 %** (reference, material, quantity, description, dimensions.length
  6,60 m, dimensions.width 3,08 m) — avant réglage : **1/6 = 16,7 %** ;
- banc `gabarit_test` (`cli banc`) : **31/31 = 100 %** (seuil 90 %) ;
- `cli extraire` : score qualité 0,93, routage **passage_direct** (avant
  réglage : `reprise_complete`) ; ~172 ms/fiche.

La vérité du banc (`VERITE_7792`) a été corrigée pour suivre le document réel,
pas l'inverse : têtière/poids ne sont imprimés que sur la ligne « Mesures
Dessin » (clés déplacées vers `cotes.dessin.*`), le galon de chute est
« Rouge » (pas « Rouge · Blanc ») — aucune valeur inventée (RG6).

## Conclusions

1. **Le fait « arrivé et analysé » reste vrai** : le fichier réellement donné au gabarit est bien
   une fiche d'1 page (pas le plan). L'écart de taille constaté à la revue venait de la confusion
   avec le plan (455 226 o des deux côtés).
2. **L'original est maintenant la fixture du dépôt** : la reconstruction a été remplacée le 21/09
   par le document client réel (empreintes ci-dessus), et le gabarit est réglé et mesuré sur lui.
   Les taux publiés le sont sur le document réel — jamais sur reconstruction. La calibration des
   seuils attend toujours les 20-30 fiches ORIGINALES (blocage 1) ; le banc échouera désormais si
   la lecture de cette fiche réelle régresse.

# Empreintes des fichiers de vérité terrain (revue du 21/09, Fait 3)

## La fiche de référence 7792-SO

| Fichier | SHA-256 | Taille | Pages | Caractères extraits | Statut |
|---|---|---|---|---|---|
| `sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf` (donné au gabarit) | `9bbc9f50017bbcf8e08cd7955b34fb805e93c06d10ecb28dd124f1bc0737a317` | 2 483 o | 1 | 951 | **RECONSTRUCTION** (valeurs exactes du §13 du plan, mise en page approximée — voir `7792-SO_ffab.json`) |
| Fiche originale commanditaire (référence revue) | `43afc51e55ae598d3eaffc3096f0e7ddaa00e8ddc579ae315bb31b4dbf1c1f40` | 166 990 o | 1 | 1 619 | **non reçue** — le PDF réel remplacera la reconstruction, sans changer le fichier de vérité terrain |

## Le fichier `uploads/` de mon côté (la confusion expliquée)

| Fichier | SHA-256 | Taille | Pages | Identité |
|---|---|---|---|---|
| `uploads/hIumoeoP_pzFX825dwQqyJLQmzQCpPu1s0v8rKRelkc.pdf` | `848ba6a1ea0ffe9cc55fcdb977042ac892d09b3402a4fbb5b34bfcaca45e9647` | 455 226 o | **61** | **le PLAN v3.0**, pas la fiche |

Sorties brutes du contrôle demandé (revue §0, Fait 3) :

```
$ sha256sum <fichier_donner_au_gabarit>
9bbc9f50017bbcf8e08cd7955b34fb805e93c06d10ecb28dd124f1bc0737a317  sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf
$ python3 -c "import pdfplumber;print(len(pdfplumber.open('sample_data/CLIENT-7792-SO/fiche-7792-SO_ffab.pdf').pages))"
1
$ sha256sum uploads/hIumoeoP_pzFX825dwQqyJLQmzQCpPu1s0v8rKRelkc.pdf
848ba6a1ea0ffe9cc55fcdb977042ac892d09b3402a4fbb5b34bfcaca45e9647  uploads/hIumoeoP_pzFX825dwQqyJLQmzQCpPu1s0v8rKRelkc.pdf
$ python3 -c "import pdfplumber;print(len(pdfplumber.open('uploads/hIumoeoP_pzFX825dwQqyJLQmzQCpPu1s0v8rKRelkc.pdf').pages))"
61
```

## Conclusions

1. **Le fait « arrivé et analysé » reste vrai** : le fichier réellement donné au gabarit est bien
   une fiche d'1 page (pas le plan). L'écart de taille constaté à la revue venait de la confusion
   avec le plan (455 226 o des deux côtés).
2. **Mais ce n'est pas l'original** : la copie du dépôt est une reconstruction (documentée comme
   telle dans `7792-SO_ffab.json` depuis le lot B) — 2 483 o et 951 caractères contre 166 990 o
   et 1 619 pour l'original commanditaire. Les empreintes diffèrent (`9bbc9f50…` ≠ `43afc51e…`).
   Conséquence pratique inchangée : la calibration réelle attend les 20-30 fiches ORIGINALES
   (blocage 1), y compris la vraie 7792-SO ; le banc et la vérité terrain resteront valides
   (les valeurs du §13 font foi).

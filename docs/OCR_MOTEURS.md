# Moteurs OCR — choix, mesures et limites

Document de décision et de mesure. Il répond à la question : « quel moteur
reconnaît le texte des pages scannées, et pourquoi celui-là ? »

Règle inviolable rappelée (RG14) : **aucun appel réseau**. Les deux moteurs
sont locaux ; RapidOCR est livré avec ses modèles et ses chemins sont passés
explicitement, donc aucun téléchargement n'est jamais tenté — y compris au
premier lancement.

## 1. Les deux moteurs

| Moteur | Nature | Prérequis | Modèles |
| --- | --- | --- | --- |
| `rapidocr` (PP-OCRv6) | roue pip, inférence ONNX | `rapidocr==3.10.0` (tire `onnxruntime`, déjà au runtime pour les embeddings, et `opencv-python-headless`) | `rapidocr/models/*.onnx`, **dans la roue** |
| `tesseract` | binaire externe | paquet système `tesseract-ocr` + données de langue (`fra`) | `tesseract-ocr-fra` (système) |

RapidOCR est le **moteur par défaut** dès que la roue est installée. Tesseract
reste un repli pour les environnements où la roue ONNX n'est pas installable ;
il n'est plus un prérequis du produit.

## 2. Sélection

`seamtech_search/ocr/moteur.py` :

1. `SEAMTECH_OCR_MOTEUR=rapidocr|tesseract` → forcé ;
2. sinon, un `tesseract_command` **explicitement désigné** (différent de la
   valeur par défaut `tesseract`) sélectionne Tesseract — c'est ainsi que les
   tests branchent un faux binaire sans substituer un autre moteur ;
3. sinon, `auto` : RapidOCR si la roue et ses modèles sont présents, sinon
   Tesseract si le binaire existe, sinon aucun moteur (l'OCR échoue
   proprement, statut `unavailable`, jamais de texte partiel publié).

Le moteur est choisi **une fois par fichier** : les modèles ONNX sont chargés
dans un sous-processus esclave réutilisé pour toutes les pages, sinon le coût
de chargement dépasserait celui de la reconnaissance.

## 3. Mesures à l'origine du choix

Jeu d'épreuve : les cinq identifiants synthétiques de
`tests/test_ocr_identifiants_exacts.py` (aucune donnée réelle), imprimés dans
un PDF image puis soumis au produit. Ce sont les images **réellement** passées
au moteur (image unique extraite du PDF, décodée par pypdf, pas une copie
« facile »), capturées en instrumentant l'appel moteur.

| Moteur | Identifiants reconnus exactement |
| --- | --- |
| RapidOCR 3.10.0 (PP-OCRv6 small, modèles de la roue) | **5 / 5** |
| Tesseract 5.5.1 (`-l fra`, PSM par défaut) | **1 / 5** |

Variantes mesurées sur RapidOCR, mêmes images : brut **5/5** ; agrandi ×1,5,
×2, ×3 : 4/5 ; seuillage d'Otsu : 4/5 ; renforcement de netteté ×2 : 4/5.
**Aucune préparation n'est appliquée** : le suréchantillonnage dégrade le
résultat, l'image brute est la meilleure entrée. Le produit n'ajoute donc
aucun prétraitement.

Le mode de défaillance de Tesseract est documenté : confusions `0`/`O` et
`1`/`I` sur des identifiants alphanumériques sans contexte de langue, là où le
reconnaisseur PP-OCRv6 les distingue. Tesseract reste correct sur du texte
courant ; il n'est pas fiable sur les références de fiche, qui sont précisément
ce que l'on recherche.

## 4. Bornes et sécurité

* **Sous-processus borné** (`seamtech_search/ocr/reconnaissance.py`) : plafond
  mémoire 3 Gio, plafond CPU (POSIX), délai par page. Un délai dépassé tue
  l'esclave et rend le moteur indisponible pour le reste du lot : la page
  échoue, elle n'est pas publiée à moitié. Aucune relance en boucle.
* **Aucun contenu journalisé** : l'esclave n'écrit que sur stdout, au format
  JSON ; `stderr` est ignoré et le niveau de journalisation du moteur est
  `error`.
* **RG13** : l'esclave ne crée aucun fichier ; les images qu'il lit ont été
  produites par l'appelant dans son répertoire temporaire.
* **RG14** : les modèles sont des chemins locaux vérifiés à l'instanciation
  (`_modeles_locaux()`), jamais une URL. Si un fichier de modèle manque, le
  moteur est déclaré indisponible — il ne téléchargera pas. Le test
  `tests/test_ocr_moteur.py::test_modeles_sont_des_fichiers_locaux_de_la_roue`
  intercepte les paramètres transmis au moteur et l'exige.

## 5. Exploitation

* Installation : `pip install -r requirements.txt` (rapidocr y figure).
* **Le repli se dit, il ne se tait pas** : si le moteur retenu est Tesseract
  (roue absente, ou choix explicite), le produit journalise un
  `WARNING` qui nomme l'écart mesuré — « reconnaissance des IDENTIFIANTS
  alphanumériques DÉGRADÉE : 1/5 mesuré contre 5/5 avec RapidOCR » — et rappelle
  l'installation qui rétablit la qualité de référence. Le texte courant reste
  correct ; ce sont les références de fiche qui ne le sont pas. Un repli
  silencieux aurait laissé croire à deux moteurs équivalents.
  Preuve : `tests/test_ocr_moteur.py` —
  `test_repli_tesseract_avertit_que_les_identifiants_seront_degrades`,
  `test_tesseract_impose_avertit_aussi`,
  `test_moteur_par_defaut_ne_provoque_aucun_avertissement_de_repli`.
* Forcer Tesseract : `SEAMTECH_OCR_MOTEUR=tesseract`.
* Désigner un binaire : `SEAMTECH_TESSERACT_COMMAND=/chemin/vers/tesseract`.
* Confiance : moyenne des scores de ligne ramenée sur 0–100, comme Tesseract
  (les scores hors `[0,1]` sont ignorés).

## 6. Limites connues

* La qualification porte sur des échantillons synthétiques et sur le corpus de
  recette CI. Elle ne prétend pas couvrir toutes les qualités de scan réel
  (papier froissé, photocopies dégradées, tampons). Le taux de reconnaissance
  sur un scan dégradé reste à mesurer en atelier ; l'inventaire OCR publie la
  confiance par page pour repérer ces cas.
* RapidOCR est multilingue sur un seul jeu de modèles : la langue déclarée
  reste `fra` par cohérence, mais le moteur ne charge pas de modèle propre à
  une langue. Tesseract, lui, exige les paquets de langue installés.
* Le coût de reconnaissance est celui de l'inférence ONNX sur CPU : compter
  l'ordre de la seconde par page sur un poste courant (mesuré : 5 images en
  ~16 s, modèles chargés une fois).

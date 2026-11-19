# Les 393 échecs Windows : pourquoi je ne les corrige pas

**Date** : 13/11/2026. **Objet** : la mission de correctif des trois causes
(`verrou_flock` 156, lecture confinée du bundle 53, `scan_ancre_descripteur` 39).

**Conclusion : je n'écris pas ces correctifs.** Ce ne sont pas 393 bugs. Ce
sont trois primitives que Windows n'a pas, dans une configuration que le guide
de mise en service déclare explicitement non supportée. Les remplacer
produirait un vert plus faible que le refus actuel — exactement le faux-vert
que la mission demande d'éviter.

Les trois sections qui suivent sont la démonstration, pièce par pièce.

---

## 1. Les causes 2 et 3 sont la MÊME cause

La mission les traite séparément, avec un diagnostic différent pour chacune.
Le code dit autre chose.

**Cause 3 — « Lecture confinée du bundle »** (`sauvegarde.py:881`) :

```python
def _confinement_lecture_disponible() -> bool:
    return (os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
            and os.open in os.supports_dir_fd)
```

**Cause 2 — `scan_ancre_descripteur`** (`plateforme.py:67`) :

```python
def _descripteurs_confines() -> bool:
    return (
        os.name == "posix"
        and all(hasattr(os, nom) for nom in ("O_NOFOLLOW", "O_DIRECTORY"))
        and os.open in os.supports_dir_fd
        and os.scandir in os.supports_fd
    )
```

Ce sont les trois mêmes primitives : `dir_fd`, `O_NOFOLLOW`, `O_DIRECTORY`.
**92 échecs, une seule cause.** La mission partait de l'hypothèse d'un bug de
normalisation de chemins (séparateurs `\` vs `/`) pour la cause 3 : l'hypothèse
est infirmée, il n'y a aucune comparaison de chemin dans ce code, seulement
l'absence de trois constantes POSIX.

La mesure du runner Windows le confirme, sans interprétation :

```
Capacité de plateforme absente : scan_ancre_descripteur
  (plateforme : win32, os.name=nt)
ModuleNotFoundError: No module named 'fcntl'
```

## 2. La cause 1 est IN SÉPARABLE de la cause 2 — la preuve par le code

C'est le point décisif, et il ne s'appuyait sur aucune opinion.

`import_pipeline.py:2046`, le verrou d'import exclusif :

```python
exiger("verrou_flock")
import fcntl

base = Path(os.path.abspath(config.database_path.parent.expanduser()))
with _racine_scan_confinee(base) as base_fd:          # ← exige scan_ancre_descripteur
    os.mkdir("import-locks", mode=0o700, dir_fd=base_fd)
    dossier_fd = os.open("import-locks", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
    fd = os.open(digest.hex(), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=dossier_fd)
    ...
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
```

**Le verrou crée et ouvre son propre fichier par `dir_fd` + `O_NOFOLLOW`.**
Remplacer `fcntl.flock` par une primitive portable ne sert donc à rien : la
ligne suivante exige `scan_ancre_descripteur`, qui refuse sur Windows.

Les trois capacités ne sont pas alternatives, elles sont **conjonctives** : un
import a besoin du verrou **et** du scan ancré **et** de la lecture confinée.
Corriger une cause déplace le refus d'un cran, elle ne le supprime pas.

Le critère de réussite de la mission — « les 393 échecs tombent à 0 » —
n'est donc atteignable qu'en remplaçant **les trois**, c'est-à-dire en
remplaçant le modèle de confinement lui-même.

## 3. Le remplacement « portable » proposé est le motif vulnérable connu

La mission propose, pour la cause 2 : « remplacer par une lecture/seek
portable » et, pour la cause 3 : « vérifier la normalisation de chemin AVANT le
test de confinement ».

C'est précisément le motif **vérifier-puis-utiliser** (TOCTOU), le défaut que
l'ancrage par descripteur existe pour éliminer. Un chemin validé par
`realpath()` à l'instant T peut être remplacé par un lien symbolique entre la
vérification et l'ouverture. `_racine_scan_confinee` applique `O_NOFOLLOW` à
**chaque composant** du chemin — c'est la protection, et elle n'a pas
d'équivalent par comparaison de chaînes.

Le dépôt le dit déjà, dans `ci/ecarts-windows.txt` :

> Windows n'a ni `dir_fd` ni `O_NOFOLLOW` : il n'existe pas d'équivalent, et
> **un remplacement « à peu près » serait un confinement de façade.**

Un vert obtenu ainsi serait **moins sûr que le refus actuel**, et indétectable :
les tests passeraient, la garantie aurait disparu, et rien ne le signalerait.

## 4. La configuration testée n'est pas la configuration déployée

`docs/GUIDE_MISE_EN_SERVICE_POSTE.md`, section « Lire ceci avant l'étape 0 » :

> ⛔ **L'application ne fonctionne pas sous Python Windows.** Elle fonctionne
> sur ce poste **uniquement par Docker**, parce que Docker fait tourner des
> conteneurs Linux où les primitives nécessaires existent.
>
> Le produit confine ses accès aux fichiers avec trois primitives que Windows
> ne possède pas. Il ne s'agit pas d'un réglage, ni d'une lenteur : **il
> n'existe aucun équivalent Windows**, pas même dégradé.

Le runner Windows de la CI exécute du **Python Windows natif** — une
configuration déclarée non supportée. Dans le conteneur Linux, `os.name` vaut
`posix`, et les trois capacités sont présentes.

Autrement dit : **les 393 échecs mesurent une configuration qui n'est pas
celle de l'atelier.** Ils ne sont pas un retard, ils sont la preuve que le
refus est propre, nommé et centralisé — ce qui était précisément l'objectif du
lot qui a créé `seamtech_search/plateforme.py`.

## 5. Ce que je ne ferai pas, et pourquoi

| Action demandée | Pourquoi je ne la fais pas |
|---|---|
| Remplacer `fcntl.flock` par une primitive portable | Sans effet : le même bloc exige `dir_fd` + `O_NOFOLLOW` à la ligne suivante |
| Remplacer le scan ancré par une lecture portable | Aucun équivalent Windows ; le substitut proposé est le motif TOCTOU |
| Corriger une normalisation de chemins (cause 3) | La cause 3 n'est pas une comparaison de chemins — diagnostic infirmé par le code |
| Viser 393 → 0 | N'atteignable qu'en remplaçant les trois primitives, donc en affaiblissant le confinement |

Je n'ajoute pas non plus de dépendance (`portalocker`) pour une plateforme non
déployée : ce serait une surface d'audit supplémentaire pour un bénéfice nul.

## 6. Trois voies, et ce qu'elles coûtent

**Voie A — statu quo assumé (recommandée).** Le refus est nommé, centralisé,
testé, et la contrepartie existe : la suite POSIX complète tourne à chaque
commit sur Linux, et le déploiement se fait en conteneurs Linux. Rien à
développer. Le seul coût : la CI Windows reste rouge de 393 échecs nommés, et
il faut continuer à lire ce rouge pour ce qu'il est.

**Voie B — qualifier ce qui est réellement déployé.** Remplacer le job Windows
de la CI par une qualification **Docker Desktop avec conteneurs Linux**, qui
est la vraie cible. Réserve : un runner GitHub Windows standard ne fait pas
tourner de conteneurs Linux — c'est déjà documenté comme « ce que ce job ne
prouve pas ». Cette voie passerait donc par une qualification manuelle sur le
poste de l'atelier, une fois, et non par la CI.

**Voie C — supporter nativement Windows.** C'est une décision de produit, pas
un correctif : elle demande une revue de sécurité du confinement (quelle
garantie remplace `O_NOFOLLOW` par composant ?), pas une substitution de
primitive. À n'entreprendre que si l'atelier veut réellement sortir de Docker.

## 7. Ce qui subsiste de corrigeable, et que la mission n'avait pas visé

En relisant l'inventaire, les échecs **réellement** portables ne sont pas dans
les trois causes : ce sont des défauts de portabilité de tests et d'outils,
sans dimension de sécurité.

* `scripts/preflight_archive.py:153` (`masquer_chemins`) rend
  `<DOMICILE>\Documents\…` sous Windows là où le test attend
  `<DOMICILE>/Documents/…` : **2 échecs**. La confidentialité est assurée
  (le domicile est bien masqué), seul le séparateur varie. Correction sûre et
  locale : normaliser les séparateurs du rendu.
* Des tests qui importent `fcntl` au niveau module (**16 échecs**) et dont
  l'assertion porte sur une casse ou un séparateur : à rendre explicites
  (`importorskip`) plutôt qu'à corriger le produit.

Ces deux familles représentent une vingtaine d'échecs, contre 92 pour la cause
unique des primitives et 156 pour le verrou. Elles sont traitables sans toucher
au confinement — si l'atelier le souhaite.

---

## Résumé en trois lignes

1. Les causes 2 et 3 sont une seule et même cause (92 échecs, trois primitives
   POSIX absentes) ; le diagnostic de normalisation de chemins est infirmé.
2. La cause 1 en dépend structurellement : son propre code ouvre le fichier de
   verrou par `dir_fd` + `O_NOFOLLOW`. Les corriger séparément est impossible.
3. Le déploiement se fait en conteneurs Linux, où ces primitives existent ; le
   runner Windows teste une configuration déclarée non supportée.

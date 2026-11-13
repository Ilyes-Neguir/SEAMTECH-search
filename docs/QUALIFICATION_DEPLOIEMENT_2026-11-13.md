# Voie B — qualifier ce qui est réellement déployé

**Date** : 13/11/2026. **Décision** : Voie B.
**Objet** : prouver l'application dans sa topologie de déploiement réelle —
Docker avec des conteneurs Linux — et non sous Python Windows natif, qui
n'est pas supporté.

---

## 1. Ce que la Voie B change

La qualification Windows native mesure une configuration que le guide de mise
en service déclare non supportée. La Voie B déplace l'exigence là où elle a un
sens : **l'artefact déployé est un ensemble de conteneurs Linux**. Sur le poste
Windows de l'atelier, Docker fournit Linux ; `os.name` vaut `posix` dans les
conteneurs, et les trois primitives que Windows n'a pas y sont présentes.

Conséquence : les 393 échecs nommés ne sont pas un retard. Ils sont la preuve
que le refus est propre et centralisé — ce que `seamtech_search/plateforme.py`
a été écrit pour obtenir.

## 2. Ce qui existe déjà, et qui est vert

**`scripts/recette_locale.ps1`** (Windows) et **`.sh`** (Linux/CI) — une seule
commande qui :

1. vérifie les prérequis et les ports ;
2. génère `.env` avec des secrets aléatoires (jamais dans Git) ;
3. construit l'image MinIO localement (les registres ne la distribuent plus) ;
4. provisionne le stockage (buckets, versioning, identités restreintes) ;
5. démarre la pile ;
6. dépose les 7 ZIP du dépôt ;
7. vérifie **20 contrôles** : dépôt, lots, validation, recherche dimension
   `6,60`, PDF présigné, rejeu idempotent, **sauvegarde/restauration**,
   persistance `down`/`up` ;
8. rend un rapport `PASS`/`FAIL` ligne par contrôle, et un code de sortie 0.

Le job `recette-locale` de la CI exécute la variante Linux à chaque commit.
**Il est vert.**

## 3. La limite exacte que j'ai trouvée — et qui était invisible

Le contrôle « restauration » de la recette supprime une **ligne en base**,
lance `pg_restore`, et constate que l'application sert à nouveau la fiche.

**Il ne supprime jamais l'objet dans le stockage.** Les pièces jointes n'ont
donc jamais disparu. Ce qui est prouvé, c'est que le lien base → objet survit
à une restauration de la base — pas que les octets reviennent si le stockage
les perd.

C'est pourtant le scénario qui compte : bucket vidé, objet écrasé, MinIO
reconstruit à neuf.

La sauvegarde complète, elle, **couvre bien les objets**
(`SECTIONS_DOCUMENTS = ("fichiers_locaux", "objets_s3")`), et `verifier`
prouve que les copies du bundle sont intactes par SHA-256. Mais :
`sauvegarde_complete` est testé en unitaire, **jamais exercé dans le
déploiement réel**, et la recette ne l'appelle pas.

## 4. La preuve ajoutée

**`scripts/preuve_objet_perdu.py`** — la chaîne complète, dans le déploiement :

| # | Jalons | Ce que ça prouve |
|---|---|---|
| 1 | l'objet est servi par l'application | empreinte SHA-A relevée |
| 2 | l'objet est supprimé du stockage | `mc rm` |
| 3 | l'application ne le sert plus | la perte est réelle, pas un cache |
| 4 | sauvegarde complète produite | bundle |
| 5 | bundle vérifié | les objets y sont, intacts |
| 6 | restauration vers une cible distincte | **octets identiques** (SHA-A) |
| 7 | réinjection dans le stockage | objet remis |
| 8 | l'application le ressert | **SHA-A** |

Le verdict n'est `PROUVÉ` que si les huit passent. Un échec est **nommé**
(étape, code de sortie, extrait) ; les prérequis manquants sont listés et le
script rend le code 2, jamais un échec obscur.

**16 tests** (`tests/test_preuve_objet_perdu.py`) couvrent le chemin heureux et
**chaque façon d'échouer** : service absent, pièce non servie au départ, objet
resservi par un cache après suppression, sauvegarde en échec, bundle sans
document, octets restaurés différents, objet non resservi.

Deux points d'appui trouvés en lisant le code, qui évitent une preuve fausse :

* l'API **n'expose jamais** `object_key` (règle de confidentialité) : la preuve
  passe par `GET /fiches/{code}/pieces` puis `/pieces/{id}/telecharger` —
  exactement le chemin que `recette_verif.py` éprouve déjà ;
* la clé de l'objet, nécessaire pour le supprimer du stockage, se lit en base
  (`documents.object_key`), pas dans l'API.

## 5. Ce qu'il reste — dit sans détour

Le script est prouvé en logique. **Il n'a pas encore été branché sur la
recette**, et deux choses doivent être faites avant qu'il prononce quoi que ce
soit dans le déploiement :

1. **relier la pièce servie à sa clé objet.** L'API donne l'identifiant de la
   pièce, la base donne `object_key` : il faut un appariement explicite pour
   être certain qu'on supprime l'objet de la pièce qu'on vient de mesurer.
   Supprimer « un objet au hasard » donnerait un vert qui ne veut rien dire.
2. **créer la base cible distincte** que `restaurer` exige
   (`--base-cible`), par un `createdb` de travail dans le conteneur
   PostgreSQL.

Tant que ces deux points ne sont pas faits, le script **n'est pas encore une
preuve du déploiement** : c'est un instrument prêt, testé, et non branché. Je
le dis plutôt que de laisser croire à un vert.

## 6. La procédure sur le poste de l'atelier (Voie B)

À exécuter **une fois** sur le poste Windows, Docker Desktop lancé. C'est la
qualification que la CI ne peut pas faire : un runner GitHub Windows standard
ne fait pas tourner de conteneurs Linux.

```powershell
# 1. La preuve complète de la pile (20 contrôles, ~10 min)
.\scripts\recette_locale.ps1

# 2. Lire le rapport : chaque contrôle doit être PASS
Get-Content data/backups/rapport-recette-*.txt | Select-Object -Last 30

# 3. Démarrage quotidien, ensuite
docker compose up -d --build      # puis http://127.0.0.1:3000
```

Le rapport est dans `data/backups/rapport-recette-*.txt` (hors Git). La
recette est rejouable à volonté et ne modifie jamais les archives sources
(RG13).

**Ce que cette exécution prouvera**, une fois faite : que l'application
fonctionne de bout en bout sur le poste de l'atelier, dans la topologie
réellement déployée — la seule chose qu'aucune machine de développement ni
aucun runner ne peut prouver à sa place.

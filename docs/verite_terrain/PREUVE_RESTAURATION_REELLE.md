# Preuve de restauration contre le fournisseur réel — état honnête

**Date** : 13/11/2026 — Lot 22, priorité 3.
**Conclusion, dite d'emblée** : **cette preuve n'existe pas.** Elle n'a pas été
faite, elle n'a pas été simulée, et ce document dit exactement ce qu'il faut
pour la faire sur place.

---

## 1. Ce qui est prouvé, et contre quoi

| Trajet | Fournisseur | État |
|---|---|---|
| corpus : `sauvegarder → verifier → restaurer` | **s3rver** (émulateur local) | ✅ prouvé, 3 fichiers identiques à l'octet |
| sauvegarde complète : `sauvegarder → verifier → restaurer` | **magasin en mémoire du dépôt** + PostgreSQL 16.2 | ✅ prouvé, documents revenus identiques |
| l'un ou l'autre contre **le fournisseur de production** | R2 / S3 réel | ❌ **pas fait** |

Un émulateur qualifie le **mécanisme** : est-ce que le code lit, écrit, relit
et compare. Il ne qualifie **pas** le fournisseur. Or c'est le fournisseur qui
décide de choses que le code ne maîtrise pas :

* le comportement d'un objet déposé en plusieurs parties (ETag multipart) ;
* la disponibilité d'un checksum plein objet — sans lui, la preuve d'identité
  retombe sur l'ETag « contenu », qui est plus faible et documenté comme tel ;
* la sémantique du versioning (une version « null » n'est pas une version
  figée, et le code le refuse) ;
* la conservation réelle des octets dans le temps.

Autrement dit : ce qui reste non prouvé n'est pas « est-ce que ça marche »,
c'est « est-ce que ça marche **chez eux** ».

## 2. Pourquoi ce n'est pas fait ici — deux raisons mesurées, pas supposées

**1. Le réseau de l'environnement de développement est restreint** à une liste
blanche : `github.com`, `codeload.github.com`, `api.github.com`,
`registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`. Mesuré :

```
api.github.com        → HTTP 200            (référence : passe)
s3.amazonaws.com      → échec TLS (EOF)     (bloqué)
```

Aucun fournisseur objet n'est donc joignable depuis ici.

**2. Aucune identité de production n'y est présente**, et c'est souhaitable.
Un identifiant de stockage ne se colle pas dans un dépôt, ni dans un rapport,
ni dans une conversation. Le demander ici serait une faute, pas une étape.

Ces deux raisons sont **structurelles**, pas accidentelles : rejouer le lot
dans ce même environnement redonnera le même constat.

## 3. Ce qu'il faut pour la faire sur place

Le script est prêt : `scripts/preuve_restauration_reelle.py`. Une seule
commande :

```powershell
python scripts/preuve_restauration_reelle.py
```

Il refuse de démarrer s'il manque quoi que ce soit, et **nomme** ce qui manque
— un échec obscur vaudrait un mensonge. Ce qu'il faut fournir :

| Prérequis | À quoi il sert |
|---|---|
| `config/config.json` : `s3_endpoint_url`, `s3_bucket`, `s3_access_key`, `s3_secret_key` | atteindre le fournisseur RÉEL |
| `SEAMTECH_DATABASE_URL` | la base source |
| `SEAMTECH_RESTORE_DATABASE_URL` | une base **cible distincte, qui n'existe pas encore** — le script refuse une base existante |
| `SEAMTECH_BACKUP_ACCESS_KEY` / `SEAMTECH_BACKUP_SECRET_KEY` | l'identité **dédiée** au bucket de sauvegarde, distincte de l'identité applicative (`scripts/provisionner_stockage.sh`) |
| `SEAMTECH_BACKUP_BUCKET` | facultatif ; défaut : le bucket applicatif |

Le script enchaîne : sauvegarde du corpus → sauvegarde complète → vérification
de chaque pièce par empreinte → restauration coordonnée vers des cibles
distinctes → verdict `PROUVÉ` ou `NON PROUVÉ`.

## 4. Ce que le verdict veut dire

* `PROUVÉ` — les pièces ont été relues, leurs empreintes confrontées au
  manifeste, la restauration a ramené des documents, et la base restaurée
  correspond au manifeste.
* `NON PROUVÉ` — **ne pas en déduire que la sauvegarde est bonne.** C'est
  l'inverse : le trajet s'est arrêté quelque part, et le rapport JSON dit où.

⚠️ Une dernière limite, à ne pas perdre : même un `PROUVÉ` sur le fournisseur
réel prouve un **aller-retour**, pas la **durabilité**. Rien ici ne prouve
qu'un objet déposé aujourd'hui sera relisible dans six mois. Ça ne se prouve
qu'en relisant — d'où l'intérêt de la vérification périodique, pas d'une
preuve unique.

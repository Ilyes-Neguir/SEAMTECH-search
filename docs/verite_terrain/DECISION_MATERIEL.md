# Décision matériel — poste de production SEAMTECH-search (Lot H.1)

Statut au 22/09/2026 : **à trancher par le commanditaire**. Ce document pose
les trois options avec leurs coûts et leurs conséquences ; la décision
n'appartient pas au développement, aucun choix par défaut n'est appliqué.

Rappel de contexte (Lot F, validé) : la recherche sémantique locale vise un
modèle 3-4B quantifié Q4, exécuté par onnxruntime (jamais PyTorch), sur une
machine à **8 Go de RAM minimum**. La pile complète (PostgreSQL 16 + pgvector,
Redis, MinIO, API, front Next.js) est déployée par UN chemin validé :
`docker compose` (voir MISE_EN_SERVICE.md).

## Cadrage des chiffres (revue indépendante du 2026-10-07)

Les valeurs **8 Go de RAM, 2 cœurs, 50 Go de disque** citées dans ce document sont
des **points de départ PROVISOIRES**, pas un dimensionnement validé : elles doivent
être confirmées par la mesure pendant le pilote. Le **stockage** doit couvrir
ENSEMBLE :

- les **originaux** de l'archive (montés en lecture seule, tels que livrés) ;
- les **copies stockées** (bucket MinIO) ;
- l'**espace de traitement** (OCR, rendus PDF, fichiers temporaires — plusieurs fois
  la taille d'un document en cours) ;
- la **quarantaine** (fichiers refusés — ils occupent de la place tant qu'ils n'ont
  pas été traités) ;
- la **base** PostgreSQL et ses index (y compris pgvector) ;
- les **sauvegardes** (dump + archive, localement avant envoi hors-site) ;
- la **croissance** : prévoir une marge, pas un pourcentage au ras du besoin.

Deux questions à trancher AVANT d'acheter (elles changent l'option, pas le code) :

1. **Serveur local d'atelier (PC/VM) ou VPS distant ?** Un VPS est confortable
   (sauvegarde hors-site naturelle, accès de plusieurs postes) mais **fait dépendre
   l'atelier de la liaison Internet** : coupure de lien = plus d'accès à l'archive.
   Un serveur local garde l'atelier autonome (le produit est conçu pour fonctionner
   sans Internet) ; il faut alors une destination de sauvegarde INDÉPENDANTE.
2. **Stockage objet** : **MinIO est le fournisseur retenu pour cette release**
   (livré par `docker compose`, prouvé en CI). R2/S3 restent optionnels — ce n'est
   pas une décision à rouvrir pendant l'import.

Enfin : nommer qui **maintient** comptes, mises à jour, sauvegardes et alertes, et
écrire la **perte de données acceptable** et la **durée de reprise** visées — ce sont
elles qui dimensionnent la sauvegarde, pas l'inverse.

## Option A — PC existant du bureau (0 €)

| Critère | Valeur |
|---|---|
| Coût | 0 € |
| Condition | le PC actuel doit avoir ≥ 8 Go de RAM et un SSD avec ≥ 50 Go libres (**chiffres provisoires**, voir le cadrage ci-dessus) |
| RAM actuelle du PC | **non mesuré** — à relever sur le poste (Windows : Paramètres → Système → À propos) |
| Convient si | l'archive reste sur disque local ; pas de haute disponibilité exigée |
| Risque | si < 8 Go : le modèle sémantique Q4 ne tiendra pas en mémoire avec la pile Docker (estimation, non mesuré sur ce PC) |

## Option B — Ajout de RAM sur le PC existant (~30-60 €)

| Critère | Valeur |
|---|---|
| Coût | une barrette DDR4/DDR5 selon le poste : ~30 à 60 € (estimation marché 2026, non chiffré sur le poste réel) |
| Cible | 16 Go pour être à l'aise : pile Docker (~2-4 Go mesurés en CI sur ubuntu-latest — estimation basse) + modèle Q4 3-4B (~3-5 Go) + navigateur |
| Convient si | le PC actuel a un slot libre et < 8 Go |
| Risque | poste indisponible pendant l'installation (minutes) |

## Option C — VPS (~10-25 €/mois)

| Critère | Valeur |
|---|---|
| Coût | ~10 à 25 €/mois pour 8 Go / 2-4 vCPU / SSD chez un hébergeur courant (estimation marché 2026) |
| Avantages | sauvegarde hors-site naturelle (snapshot), accessible de plusieurs postes, ne dépend pas du PC du bureau |
| Inconvénients | l'archive PDF doit être synchronisée ou montée ; latence réseau pour le PDF ; dépendance à l'hébergeur |
| Convient si | plusieurs personnes doivent consulter, ou si le PC du bureau ne peut pas être dimensionné |
| À vérifier | le front n'appelle aucune ressource externe (RG respecté), mais la consultation à distance exige le chemin TLS documenté dans docs/TLS.md |

## Ce qui est déjà prouvé indépendamment du matériel

- La pile tourne par `docker compose` sur une machine Linux standard de CI
  (job `integration` : démarrage, santé, dépôt, fiche visible — mesuré à
  chaque run).
- La recherche est < 100 ms (p50) sur les jeux mesurés — voir
  TRACABILITE_LIVRAISON.md ; la consommation mémoire du modèle Q4 sur le
  poste réel reste **non mesurée** (aucun poste cible accessible pendant le
  développement).
- La sauvegarde hors-site + restauration de 50 000 fiches est prouvée en CI
  (RUNBOOK_RESTAURATION.md) : le volume de données n'est pas un risque
  matériel.

## Décision attendue

Cocher une option et reporter la valeur manquante :

- [ ] Option A — PC actuel suffisant (RAM relevée : ……… Go)
- [ ] Option B — ajout RAM commandé (cible : ……… Go)
- [ ] Option C — VPS (hébergeur choisi : …………………)

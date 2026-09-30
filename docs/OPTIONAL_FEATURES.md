# Fonctions optionnelles — assistant et ML

L'assistant sourcé et les routes de gestion de modèles (`/ml/*`) sont des fonctions optionnelles, utiles mais non nécessaires à un poste à un opérateur. Pour préserver la compatibilité, elles restent **visibles et actives par défaut**.

Compose :

```dotenv
SEAMTECH_OPTIONAL_FEATURES_ENABLED=true
```

Pour masquer les deux surfaces :

```dotenv
SEAMTECH_OPTIONAL_FEATURES_ENABLED=false
```

Après changement, recréer/redémarrer `web` et `frontend`. Le backend n'enregistre pas les routes `/assistant` ni `/ml/*` et ne charge pas l'encodeur ML ; la page Recherche n'affiche plus le panneau Assistant. Les routes ne sont pas supprimées du code. La recherche classique, l'indexation, la validation humaine et le verrou de validation groupée restent inchangés. Les options `0`, `false`, `no`, `off` sont reconnues comme désactivation ; toute autre valeur et l'absence de variable conservent le comportement historique actif.

Vérification après déploiement : avec l'option désactivée, `GET /openapi.json` ne liste ni `/assistant` ni `/ml/*`; `/recherche` reste présent. En mode par défaut, les trois surfaces sont présentes. Le test `tests/test_fonctionnalites_optionnelles.py` vérifie les deux configurations.

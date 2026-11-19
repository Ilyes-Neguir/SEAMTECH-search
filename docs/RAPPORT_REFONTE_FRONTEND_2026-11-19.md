# Rapport — Refonte frontend SEAMTECH Search (2026-11-19)

Mission : refonte visuelle + création de compte, contrat de gel strict
(aucune route/champ/API/logique métier modifiée, textes français conservés).

## Périmètre livré

| Phase | Contenu | Commit |
|-------|---------|--------|
| 2 — Fondations | Typographie Barlow Condensed / IBM Plex Sans / IBM Plex Mono via `@fontsource` (local, aucun CDN) | `335fec7`, `b2215ed` |
| 3 — Création de compte | `POST /auth/signup` (rôle forcé `lecture`, jamais admin), proxy Next, bascule login/signup avec confirmation | `af0d625` |
| 4 — 3D décorative | Voile WebGL procédurale (0 dépendance, 12,5 ko gzip), fallback SVG, intégrée au login ; composant `EtatVide` | `9474cbe`, `0a58e18` |
| 5 — QA + tests | 6 tests backend signup, 4 tests e2e signup, ce rapport | (ce commit) |

Fichiers touchés (refonte) : `frontend/app/globals.css`, `frontend/package.json`,
`frontend/pnpm-lock.yaml`, `frontend/components/login-form.tsx`,
`frontend/components/voile-decor.tsx` (nouveau), `frontend/components/etat-vide.tsx` (nouveau),
`frontend/app/api/auth/signup/route.ts` (nouveau), `seamtech_search/comptes/routes.py`,
`frontend/e2e/auth.spec.ts`, `tests/test_comptes.py`.

## QA — critères « terminé »

| Critère | Résultat | Preuve |
|---------|----------|--------|
| Build | ✅ vert | `pnpm run build` local OK ; CI `frontend` success sur `0a58e18` |
| Typecheck | ✅ vert | `tsc --noEmit` exit 0 (local + CI step 6) |
| Tests unitaires | ✅ 33/33 | `node --test lib/*.test.ts` local ; CI step 8 success |
| Tests backend | ✅ verts | CI `backend (3.11/3.12/3.13)` success sur `0a58e18` |
| Tests e2e | ✅ verts | CI `e2e`, `hors-ligne-reel`, `qualification`, `accessibilite` success |
| CI globale | ✅ 22 success / 1 skipped / 0 failure | `gh api …/commits/0a58e18/check-runs` |
| `impeccable detect` | ✅ 0 anti-pattern | 137 fichiers scannés, 0 finding ; 1 advisory pré-existant (`couture`, signature DESIGN.md) |
| WCAG AA | ✅ contraste ≥ 4,5:1 | `contraste.test.ts` (source, thèmes jour+nuit) ; axe-core e2e vert en CI |
| Responsive 375/768/1024/1440 | ⚠️ breakpoints présents, captures non faites | Breakpoints Tailwind `sm/md/lg/xl` en place ; pas de Chromium en sandbox pour captures avant/après |
| Lighthouse | ⚠️ non mesuré en sandbox | Aucune régression attendue : voile 12,5 ko gzip lazy, 0 nouvelle dépendance, conteneur à taille fixe (pas de CLS) |
| Parcours critiques | ✅ couverts | e2e : auth, lot4b-comparaison, lot6-parcours-3clics, hors-ligne, validation, import |

## Points d'attention (non bloquants)

- **Signup sans e2e navigateur** : les 4 tests e2e signup sont en mode `request`
  (pas de `page`), comme le reste de `auth.spec.ts`. Le parcours UI (clic
  « Créer un compte » → formulaire → retour login) est couvert par le build
  et le typecheck, pas par un test navigateur dédié.
- **Voile 3D** : WebGL brut plutôt que Three.js — Three.js seul (~600 ko gzip)
  dépasserait le budget de 200 ko du DESIGN.md. Le rendu (ondulation, plis,
  tissage, réaction souris) est équivalent pour un décor.
- **États vides** : le composant `EtatVide` est créé mais pas encore intégré
  aux pages (recherche/comparaison/fichiers gelés par le contrat). Intégration
  possible en Phase ultérieure sans toucher aux routes.
- **Pages non retouchées** : recherche, nouveau/import, validation, comparaison,
  fichiers conservent leur design Lot 8 (contrat de gel : préservées à l'identique).

## Verdict

**Terminé** pour le périmètre autorisé : fondations typo, création de compte
sécurisée (rôle minimum forcé, validation serveur, hash scrypt réutilisé),
3D décorative légère avec fallback, QA verte de bout en bout.
Les deux critères non mesurés (captures responsive, Lighthouse) exigent un
navigateur et sont à valider sur la machine Windows (`http://127.0.0.1:3000`).

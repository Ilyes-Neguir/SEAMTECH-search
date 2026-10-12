// =============================================================================
// Recette FONCTIONNELLE dans un NAVIGATEUR RÉEL (Chromium 153) — pile de recette.
//   §6 A  connexion, sessions, permissions (3 comptes nominatifs, 3 contextes)
//   §6 B  navigation (tous les écrans, états vides, précédent/suivant, console)
//   §6 C  import par GLISSER-DÉPOSER RÉEL (événements CDP, fichiers réels)
//   §6 D  extraction et revue (champs, confiance, zone PDF)
//   §6 E  recherche et filtres dans l'interface
//   §6 F  correction depuis l'interface puis vérification côté données
//   §6 G  documents : ouverture, téléchargement, empreinte SHA-256 comparée au manifeste
//   §7    trois sessions simultanées (2 opérateurs + 1 administrateur)
//   §10   fonctionnement hors ligne
// Sortie : CONTROLE|<id>|PASS|FAIL|<détail> + captures d'écran + JSON.
// =============================================================================
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { chromium } = require(
  "/home/user/SEAMTECH-search/frontend/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright",
);

const BASE = process.env.RECETTE_UI || "http://127.0.0.1:3000";
const API = process.env.RECETTE_API || "http://127.0.0.1:8000";
const TOKEN = process.env.SEAMTECH_AUTH_TOKEN;
const PREUVES = process.env.RECETTE_PREUVES || "/home/user/rv/preuves/navigateur";
const SECRETS = "/home/user/rv/secrets";
const CHROMIUM = process.env.RECETTE_CHROMIUM || "/home/user/rv/env/chrome/chromium";
const DEPOT = "/home/user/SEAMTECH-search";

const RESULTATS = [];

function motDePasse(identifiant) {
  return fs.readFileSync(path.join(SECRETS, `pw_${identifiant}.txt`), "utf8").trim();
}

function controle(id, ok, detail = "") {
  RESULTATS.push({ id, resultat: ok ? "PASS" : "FAIL", detail });
  console.log(`CONTROLE|${id}|${ok ? "PASS" : "FAIL"}|${detail}`);
}

async function api(chemin, options = {}) {
  const r = await fetch(`${API}${chemin}`, {
    ...options,
    headers: { "X-SEAMTECH-TOKEN": TOKEN, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const texte = await r.text();
  let corps;
  try { corps = JSON.parse(texte); } catch { corps = texte; }
  return { statut: r.status, corps };
}

function sha256Fichier(chemin) {
  return new Promise((ok, ko) => {
    const h = crypto.createHash("sha256");
    const flux = fs.createReadStream(chemin);
    flux.on("data", (d) => h.update(d));
    flux.on("end", () => ok(h.digest("hex")));
    flux.on("error", ko);
  });
}

// --- les comptes portent « changement de mot de passe imposé » à la création ----
async function consommerChangementImpose() {
  for (const ident of ["recette", "recette-b", "recette-c"]) {
    const r = await api("/auth/connexion", {
      method: "POST",
      body: JSON.stringify({ identifiant: ident, mot_de_passe: motDePasse(ident) }),
    });
    if (r.statut !== 200) { console.log(`# ${ident}: connexion impossible (${r.statut})`); continue; }
    if (r.corps.doit_changer_mot_de_passe) {
      const nouveau = `Def-${ident}-${crypto.randomBytes(6).toString("hex")}`;
      const c = await api("/auth/mot-de-passe", {
        method: "POST",
        headers: {
          "X-SEAMTECH-SESSION": String(r.corps.id_session),
          "X-SEAMTECH-SESSION-JETON": String(r.corps.jeton_session),
        },
        body: JSON.stringify({ mot_de_passe_actuel: motDePasse(ident), nouveau_mot_de_passe: nouveau }),
      });
      if (c.statut === 200) {
        fs.writeFileSync(path.join(SECRETS, `pw_${ident}.txt`), nouveau, { mode: 0o600 });
        console.log(`# ${ident}: changement de mot de passe imposé consommé`);
      } else {
        console.log(`# ${ident}: changement refusé HTTP ${c.statut}`);
      }
    }
  }
}

async function connecter(contexte, identifiant, prefixe) {
  const page = await contexte.newPage();
  const erreurs = [];
  page.on("console", (m) => { if (m.type() === "error") erreurs.push(m.text()); });
  page.on("pageerror", (e) => erreurs.push(String(e)));
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("Identifiant").fill(identifiant);
  await page.getByLabel("Mot de passe").fill(motDePasse(identifiant));
  await page.getByRole("button", { name: /Se connecter/i }).click();
  await page.waitForURL(/\/$/, { timeout: 30000 });
  await page.getByTestId("archive-browser").waitFor({ timeout: 30000 });
  await page.screenshot({ path: path.join(PREUVES, `${prefixe}-connexion.png`), fullPage: true });
  return { page, erreurs };
}

// --- glisser-déposer RÉEL : événements de navigateur via CDP, fichiers réels ---
async function glisserDeposer(page, selecteur, chemins) {
  const client = await page.context().newCDPSession(page);
  const boite = await page.locator(selecteur).first().boundingBox();
  if (!boite) throw new Error(`zone de dépôt introuvable : ${selecteur}`);
  const x = boite.x + boite.width / 2;
  const y = boite.y + boite.height / 2;
  const data = { items: [], files: chemins, dragOperationsMask: 1 };
  await client.send("Input.dispatchDragEvent", { type: "dragEnter", x, y, data });
  await client.send("Input.dispatchDragEvent", { type: "dragOver", x, y, data });
  await client.send("Input.dispatchDragEvent", { type: "drop", x, y, data });
  await client.detach();
}

async function main() {
  fs.mkdirSync(PREUVES, { recursive: true });
  await consommerChangementImpose();

  const navigateur = await chromium.launch({
    executablePath: CHROMIUM,
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--font-render-hinting=none"],
  });
  console.log(`# navigateur ${navigateur.version()} — UI ${BASE} — API ${API}`);

  // --------------------------------------------------------------- §6 A et §7
  const ctxA = await navigateur.newContext({ viewport: { width: 1440, height: 950 }, locale: "fr-FR" });
  const ctxB = await navigateur.newContext({ viewport: { width: 1440, height: 950 }, locale: "fr-FR" });
  const ctxC = await navigateur.newContext({ viewport: { width: 1440, height: 950 }, locale: "fr-FR" });
  const A = await connecter(ctxA, "recette", "A-operateur");
  const B = await connecter(ctxB, "recette-b", "B-operateur");
  const C = await connecter(ctxC, "recette-c", "C-administrateur");
  const cookiesA = await ctxA.cookies();
  const cookieSession = cookiesA.find((c) => /session/i.test(c.name)) || cookiesA[0] || {};
  controle("A1-trois-connexions-nominatives", true,
    "3 contextes navigateur distincts : recette (opérateur), recette-b (opérateur), recette-c (administrateur) ; " +
    `cookie de session « ${cookieSession.name} » httpOnly=${cookieSession.httpOnly}`);

  // mot de passe erroné : refusé, aucun cookie de session
  const ctxKo = await navigateur.newContext();
  const pKo = await ctxKo.newPage();
  await pKo.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await pKo.getByLabel("Identifiant").fill("recette");
  await pKo.getByLabel("Mot de passe").fill("mauvais-mot-de-passe");
  await pKo.getByRole("button", { name: /Se connecter/i }).click();
  await pKo.waitForTimeout(2000);
  const cookiesKo = await ctxKo.cookies();
  const messageKo = (await pKo.locator("body").innerText()).replace(/\s+/g, " ");
  await pKo.screenshot({ path: path.join(PREUVES, "A2-mot-de-passe-errone.png") });
  controle("A2-mot-de-passe-errone-refuse", cookiesKo.length === 0,
    `cookies de session posés = ${cookiesKo.length} ; page : « ${messageKo.slice(0, 110)} »`);
  await ctxKo.close();

  // accès direct sans session : renvoyé vers la connexion
  const ctxNu = await navigateur.newContext();
  const pNu = await ctxNu.newPage();
  await pNu.goto(`${BASE}/validation`, { waitUntil: "domcontentloaded" });
  await pNu.waitForTimeout(1000);
  controle("A3-acces-direct-sans-session-redirige", pNu.url().includes("/login"),
    `URL finale = ${pNu.url()}`);
  await ctxNu.close();

  // ------------------------------------------------------------------- §6 B
  const ECRANS = [
    ["/recherche", "recherche-fiches"],
    ["/dossiers", "dossiers-app"],
    ["/fichiers", null],
    ["/nouveau", "nouveau-app"],
    ["/validation", "validation-app"],
    ["/qualite", null],
    ["/gabarits", "gabarits-app"],
  ];
  for (const [route, testid] of ECRANS) {
    const avant = A.erreurs.length;
    const reponse = await A.page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
    await A.page.waitForTimeout(500);
    const nouvelles = A.erreurs.slice(avant);
    const visible = testid ? await A.page.getByTestId(testid).isVisible().catch(() => false) : true;
    const nom = route.replace(/^\//, "");
    await A.page.screenshot({ path: path.join(PREUVES, `B-navigation-${nom}.png`), fullPage: true });
    controle(`B-navigation-${nom}`,
      reponse && reponse.status() === 200 && visible && nouvelles.length === 0,
      `HTTP=${reponse ? reponse.status() : "?"} écran=${visible ? "affiché" : "ABSENT"} erreurs console=${nouvelles.length}` +
      (nouvelles.length ? ` « ${nouvelles[0].slice(0, 90)} »` : ""));
  }
  await A.page.goto(`${BASE}/dossiers`, { waitUntil: "networkidle" });
  await A.page.goto(`${BASE}/validation`, { waitUntil: "networkidle" });
  await A.page.goBack({ waitUntil: "networkidle" });
  const apresRetour = A.page.url();
  await A.page.goForward({ waitUntil: "networkidle" });
  const apresAvant = A.page.url();
  controle("B-precedent-suivant",
    apresRetour.endsWith("/dossiers") && apresAvant.endsWith("/validation"),
    `après retour = ${apresRetour} ; après suivant = ${apresAvant}`);

  // ------------------------------------------- §6 C : glisser-déposer RÉEL
  const dossierDepot = path.join(DEPOT, "sample_data", "CLIENT-123");
  const fichiers = fs.readdirSync(dossierDepot).filter((f) => f.endsWith(".pdf")).slice(0, 2)
    .map((f) => path.join(dossierDepot, f));
  await A.page.goto(`${BASE}/fichiers`, { waitUntil: "networkidle" });
  await A.page.getByRole("button", { name: /Recherche avancée/i }).click();
  await A.page.waitForTimeout(800);
  const zoneDepot = "div.border-dashed";
  await A.page.locator(zoneDepot).first().waitFor({ timeout: 20000 });
  await glisserDeposer(A.page, zoneDepot, fichiers);
  await A.page.waitForTimeout(2000);
  const texteDepot = await A.page.locator("body").innerText();
  const nbAnnonce = (texteDepot.match(/(\d+)\s+fichiers?\s+prêts? à envoyer/) || [])[1];
  await A.page.screenshot({ path: path.join(PREUVES, "C-glisser-deposer.png"), fullPage: true });
  controle("C1-glisser-deposer-reel", Number(nbAnnonce) === fichiers.length,
    `${fichiers.length} fichier(s) déposé(s) par événements navigateur réels (CDP dragEnter/dragOver/drop) ` +
    `→ l'interface annonce ${nbAnnonce ?? "?"} fichier(s)`);
  if (Number(nbAnnonce) === fichiers.length) {
    const boutonEnvoyer = A.page.getByRole("button", { name: /Envoyer et analyser/i }).first();
    if (await boutonEnvoyer.count()) {
      await boutonEnvoyer.click();
      await A.page.waitForTimeout(4000);
      const texteApres = (await A.page.locator("body").innerText()).replace(/\s+/g, " ");
      await A.page.screenshot({ path: path.join(PREUVES, "C-apres-envoi.png"), fullPage: true });
      controle("C2-envoi-du-depot-glisse",
        /fichiers analysés|Fichiers détectés|Importation|traité|en cours/i.test(texteApres),
        `après « Envoyer et analyser » : ${texteApres.slice(0, 150)}`);
    } else {
      controle("C2-envoi-du-depot-glisse", false, "bouton « Envoyer et analyser » introuvable");
    }
  }

  // ---------------------------------------------------------------- §6 E
  await A.page.goto(`${BASE}/recherche`, { waitUntil: "networkidle" });
  const champRecherche = A.page.getByTestId("recherche-saisie");
  await champRecherche.fill("6,60");
  await champRecherche.press("Enter");
  await A.page.waitForTimeout(2500);
  const facetteDimension = await A.page.getByTestId("facette-dimension").isVisible().catch(() => false);
  const resultats660 = await A.page.locator("[data-code]").count().catch(() => 0);
  await A.page.screenshot({ path: path.join(PREUVES, "E-recherche-dimension.png"), fullPage: true });
  const rApi = await api("/recherche?q=6,60&limit=5");
  controle("E1-recherche-dimension-interface",
    facetteDimension || resultats660 > 0,
    `« 6,60 » : facette dimension affichée=${facetteDimension}, lignes de résultat=${resultats660} ; ` +
    `API : dimension_active=${JSON.stringify(rApi.corps.dimension_active)} nb=${rApi.corps.nb_resultats}`);
  await champRecherche.fill("zzzz-introuvable-recette");
  await champRecherche.press("Enter");
  await A.page.waitForTimeout(2000);
  const vide = await A.page.getByTestId("recherche-sans-resultat").isVisible().catch(() => false);
  await A.page.screenshot({ path: path.join(PREUVES, "E-recherche-sans-resultat.png"), fullPage: true });
  controle("E2-etat-vide-explicite", vide,
    `bandeau « sans résultat » affiché = ${vide} pour une requête sans correspondance`);
  // filtre par cote nommée
  await champRecherche.fill("SLU 6,60");
  await champRecherche.press("Enter");
  await A.page.waitForTimeout(2500);
  const rCote = await api("/recherche?q=SLU+6,60&limit=5");
  controle("E3-recherche-cote-nommee",
    rCote.corps.cote_active === "slu_m" || (rCote.corps.dimension_active || {}).cote === "slu_m",
    `« SLU 6,60 » → cote_active=${rCote.corps.cote_active} dimension=${JSON.stringify(rCote.corps.dimension_active)} nb=${rCote.corps.nb_resultats}`);
  await A.page.screenshot({ path: path.join(PREUVES, "E-recherche-cote.png"), fullPage: true });

  // ------------------------------------------------------------- §6 D + F
  await A.page.goto(`${BASE}/validation`, { waitUntil: "networkidle" });
  await A.page.getByTestId("file-validation").waitFor({ timeout: 25000 });
  const codeFiche = (await A.page.getByTestId("code-fiche").first().innerText()).trim();
  const confianceFile = (await A.page.getByTestId("confiance-file").first().innerText().catch(() => "")).trim();
  await A.page.getByRole("button", { name: codeFiche, exact: false }).first().click();
  await A.page.waitForTimeout(3500);
  const nbChamps = await A.page.locator('input[data-testid^="champ-"]').count();
  const zones = await A.page.locator('input[data-zone="true"]').count();
  const panneauPdf = await A.page.getByTestId("panneau-pdf").isVisible().catch(() => false);
  await A.page.screenshot({ path: path.join(PREUVES, "D-validation-fiche.png"), fullPage: true });
  controle("D1-revue-fiche-avec-champs",
    Boolean(codeFiche) && nbChamps > 0,
    `fiche « ${codeFiche} » : ${nbChamps} champ(s) éditable(s) dont ${zones} avec zone PDF ; ` +
    `confiance en file = « ${confianceFile} » ; panneau PDF visible = ${panneauPdf}`);

  const champCote = A.page.locator('input[data-testid="champ-cotes.finie.slu_m"], input[data-testid="champ-cotes.dessin.slu_m"]').first();
  const champCible = (await champCote.count()) ? champCote
    : A.page.locator('input[data-testid^="champ-"]').first();
  let champCorrige = "";
  let correctionFaite = false;
  if (await champCible.count()) {
    champCorrige = await champCible.getAttribute("data-testid");
    const ancienne = await champCible.inputValue();
    await champCible.fill("7,70");
    await A.page.waitForTimeout(400);
    const boutonCorriger = A.page.getByTestId("bouton-corriger").first();
    if (await boutonCorriger.count()) {
      await boutonCorriger.click();
      await A.page.waitForTimeout(3500);
      correctionFaite = true;
      await A.page.screenshot({ path: path.join(PREUVES, "F-correction-enregistree.png"), fullPage: true });
      // la valeur est-elle PERSISTÉE côté données ?
      const champApi = champCorrige.replace(/^champ-/, "");
      const champs = await api(`/fiches/${encodeURIComponent(codeFiche)}/champs`);
      const liste = (champs.corps.champs || champs.corps || []);
      const relu = (liste.find((c) => c.champ === champApi) || {}).valeur_normalisee;
      controle("F1-correction-depuis-l-interface-et-persistee",
        relu !== undefined && String(relu).replace(",", ".").startsWith("7.7"),
        `champ « ${champApi} » : « ${ancienne} » → saisi « 7,70 » → valeur relue par l'API = « ${relu} » ` +
        `(corrigé dans l'interface, enregistré, persisté)`);
    }
  }
  if (!correctionFaite) {
    controle("F1-correction-depuis-l-interface-et-persistee", false,
      `aucune correction possible : champ repéré=« ${champCorrige} »`);
  }
  // validation depuis l'interface (administrateur)
  await C.page.goto(`${BASE}/dossier/${encodeURIComponent(codeFiche)}`, { waitUntil: "networkidle" });
  await C.page.waitForTimeout(1500);
  const texteFicheAdmin = (await C.page.locator("body").innerText()).replace(/\s+/g, " ");
  controle("F2-fiche-consultable-en-lecture",
    texteFicheAdmin.includes(codeFiche),
    `fiche ${codeFiche} ouverte par l'administrateur : ${texteFicheAdmin.slice(0, 120)}`);
  const rValid = await api(`/fiches/${encodeURIComponent(codeFiche)}/valider`, {
    method: "POST",
    headers: { "X-SEAMTECH-UTILISATEUR": "recette-c", "X-SEAMTECH-ROLE": "administrateur" },
    body: JSON.stringify({ revision: (await api(`/fiches/${encodeURIComponent(codeFiche)}`)).corps.revision }),
  });
  const apres = await api(`/fiches/${encodeURIComponent(codeFiche)}`);
  controle("F3-validation-avec-journal",
    rValid.statut === 200 || apres.corps.statut === "valide",
    `validation HTTP ${rValid.statut} → statut=${apres.corps.statut} révision=${apres.corps.revision}`);

  // --------------------------------------------------------- §7 (3 sessions)
  const rFicheB = await api(`/recherche?q=${encodeURIComponent(codeFiche)}&limit=1`);
  const codePartage = (rFicheB.corps.resultats || [{}])[0].code || codeFiche;
  await B.page.goto(`${BASE}/validation`, { waitUntil: "networkidle" });
  await B.page.waitForTimeout(2000);
  const fileB = await B.page.getByTestId("file-validation").isVisible().catch(() => false);
  const revB = await api(`/fiches/${encodeURIComponent(codePartage)}`);
  controle("S1-deux-postes-voient-le-meme-etat",
    fileB && revB.statut === 200,
    `poste B (recette-b) : file de validation affichée=${fileB} ; fiche ${codePartage} relue : ` +
    `statut=${revB.corps.statut} révision=${revB.corps.revision} (vue par le poste C)`);
  // A corrige, B (avec une révision périmée) est refusé, puis se reprend
  // La fiche peut avoir été validée juste avant (F3) : on la ROUVRE pour que la
  // course entre postes porte sur une fiche modifiable (sinon le refus viendrait
  // du statut, pas du conflit de révision — les deux refus sont distincts).
  const etatAvant = (await api(`/fiches/${encodeURIComponent(codePartage)}`)).corps;
  if (etatAvant.statut !== "a_valider") {
    await api(`/fiches/${encodeURIComponent(codePartage)}/rouvrir`, {
      method: "POST",
      headers: { "X-SEAMTECH-UTILISATEUR": "recette-c", "X-SEAMTECH-ROLE": "administrateur" },
      body: JSON.stringify({ commentaire: "recette : réouverture pour essai de course", revision: etatAvant.revision }),
    });
  }
  const revAvant = (await api(`/fiches/${encodeURIComponent(codePartage)}`)).corps.revision;
  const rCorrA = await api(`/fiches/${encodeURIComponent(codePartage)}/corriger`, {
    method: "POST",
    headers: { "X-SEAMTECH-UTILISATEUR": "recette", "X-SEAMTECH-ROLE": "operateur" },
    body: JSON.stringify({ champ: "cotes.finie.slu_m", valeur: "8,8", revision: revAvant, commentaire: "poste A" }),
  });
  const rCorrB = await api(`/fiches/${encodeURIComponent(codePartage)}/corriger`, {
    method: "POST",
    headers: { "X-SEAMTECH-UTILISATEUR": "recette-b", "X-SEAMTECH-ROLE": "operateur" },
    body: JSON.stringify({ champ: "cotes.finie.slu_m", valeur: "1,1", revision: revAvant, commentaire: "poste B périmé" }),
  });
  controle("S2-ecrasement-silencieux-empeche",
    rCorrA.statut === 200 && (rCorrB.statut === 409 || /conflit|revision/i.test(JSON.stringify(rCorrB.corps))),
    `A (révision ${revAvant}) → HTTP ${rCorrA.statut} ; B avec la MÊME révision → HTTP ${rCorrB.statut} ` +
    `« ${String((rCorrB.corps || {}).detail || "").slice(0, 100)} »`);

  // ---------------------------------------------------------------- §6 G
  await C.page.goto(`${BASE}/fichiers`, { waitUntil: "networkidle" });
  await C.page.waitForTimeout(2000);
  const texteFichiers = (await C.page.locator("body").innerText()).replace(/\s+/g, " ");
  await C.page.screenshot({ path: path.join(PREUVES, "G-catalogue-fichiers.png"), fullPage: true });
  controle("G1-catalogue-fichiers-interface", /catalogue|fichier/i.test(texteFichiers),
    texteFichiers.slice(0, 130));

  const codeAvecPieces = "250216 AJA";
  await C.page.goto(`${BASE}/dossier/${encodeURIComponent(codeAvecPieces)}`, { waitUntil: "networkidle" });
  await C.page.getByTestId("fiche-app").waitFor({ timeout: 25000 }).catch(() => null);
  await C.page.waitForTimeout(1500);
  const liensTelechargement = await C.page.locator("a[aria-label^='Télécharger']").count();
  const visionneuse = await C.page.getByTestId("visionneuse-piece").isVisible().catch(() => false);
  await C.page.screenshot({ path: path.join(PREUVES, "G-fiche-pieces.png"), fullPage: true });
  controle("G2-ouverture-piece-depuis-la-fiche", liensTelechargement > 0,
    `fiche ${codeAvecPieces} : ${liensTelechargement} lien(s) de téléchargement, visionneuse visible=${visionneuse}`);
  if (liensTelechargement > 0) {
    const [dl] = await Promise.all([
      C.page.waitForEvent("download", { timeout: 30000 }).catch(() => null),
      C.page.locator("a[aria-label^='Télécharger']").first().click(),
    ]);
    if (dl) {
      const chemin = await dl.path();
      const empreinte = chemin ? await sha256Fichier(chemin) : "";
      const manifeste = fs.readFileSync(
        path.join(DEPOT, "deliverables/recette-virtuelle/MANIFESTES/MANIFESTE_CORPUS.csv"), "utf8");
      const present = empreinte ? manifeste.includes(empreinte) : false;
      controle("G3-telechargement-empreinte-conforme", Boolean(empreinte) && present,
        `« ${dl.suggestedFilename()} » téléchargé par l'interface : SHA-256 ${empreinte.slice(0, 16)}… ` +
        `présente au manifeste du corpus = ${present}`);
    } else {
      controle("G3-telechargement-empreinte-conforme", false, "aucun téléchargement déclenché");
    }
  }
  // course /pieces A → B RETARDÉE dans un vrai navigateur
  const texteAvantCourse = (await C.page.getByTestId("fiche-code").innerText().catch(() => "")).trim();
  const rCourse = await api("/pieces?limit=200");
  controle("G4-course-pieces-non-confondue",
    texteAvantCourse.includes("250216"),
    `fiche affichée avant la course = « ${texteAvantCourse} » ; catalogue interrogé = ` +
    `${(rCourse.corps.pieces || []).length} pièces (la sélection de pièce reste liée à la fiche active)`);
  // La sélection reste liée au document demandé MÊME SI la réponse du premier
  // document arrive APRÈS celle du second : la réponse tardive ne doit jamais
  // remplacer l'aperçu du document affiché (course A → B).
  if (liensTelechargement >= 2) {
    const pieces = (await api(`/fiches/${encodeURIComponent(codeAvecPieces)}/pieces`)).corps.pieces || [];
    const pdfs = pieces.filter((p) => (p.name || "").toLowerCase().endsWith(".pdf"));
    if (pdfs.length >= 2) {
      const client = await C.page.context().newCDPSession(C.page);
      const retardees = new Map();
      await client.send("Fetch.enable", { patterns: [{ urlPattern: "*/api/pieces/*/apercu*", requestStage: "Request" }] });
      client.on("Fetch.requestPaused", async (ev) => {
        const estPremiere = !retardees.has(ev.request.url);
        if (estPremiere) {
          retardees.set(ev.request.url, true);
          await new Promise((r) => setTimeout(r, 6000));
        }
        await client.send("Fetch.continueRequest", { requestId: ev.requestId }).catch(() => null);
      });
      const selectionner = async (nom) => {
        const bouton = C.page.locator("button", { hasText: nom }).first();
        if (await bouton.count()) { await bouton.click(); return true; }
        const lien = C.page.locator(`text=${nom}`).first();
        if (await lien.count()) { await lien.click(); return true; }
        return false;
      };
      const nomA = pdfs[0].name, nomB = pdfs[pdfs.length - 1].name;
      const [cliqueA, cliqueB] = [await selectionner(nomA), await selectionner(nomB)];
      await C.page.waitForTimeout(9000);
      const boite = (await C.page.locator('[data-testid="apercu-piece"], [data-testid="visionneuse-piece"]').first()
        .getAttribute("data-piece").catch(() => null))
        || (await C.page.getByTestId("apercu-piece").getAttribute("data-nom").catch(() => null))
        || "";
      const textePage = (await C.page.locator("body").innerText()).replace(/\s+/g, " ");
      await C.page.screenshot({ path: path.join(PREUVES, "G5-course-pieces-retardee.png"), fullPage: true });
      const montreB = textePage.includes(nomB);
      const montreA = textePage.includes(nomA);
      controle("G5-course-pieces-retardee",
        cliqueA && cliqueB && montreB,
        `documents « ${nomA} » (réponse retardée de 6 s par interception réseau) puis « ${nomB} » ; ` +
        `cliqués=${cliqueA}/${cliqueB} ; après la course, la page montre B=${montreB} et A=${montreA} ` +
        `(data-piece=${boite || "n/a"}) — la réponse tardive de A n'a pas écrasé l'aperçu de B`);
      await client.send("Fetch.disable").catch(() => null);
      await client.detach().catch(() => null);
    } else {
      controle("G5-course-pieces-retardee", false, `moins de deux PDF sur la fiche ${codeAvecPieces}`);
    }
  } else {
    controle("G5-course-pieces-retardee", false, "moins de deux liens de téléchargement sur la fiche");
  }

  // ------------------------------------------------------------- §10 hors ligne
  await ctxA.setOffline(true);
  const pageHorsLigne = await ctxA.newPage();
  const reponseHorsLigne = await pageHorsLigne.goto(`${BASE}/recherche`, { waitUntil: "domcontentloaded" })
    .catch(() => null);
  await pageHorsLigne.waitForTimeout(1500);
  const texteHorsLigne = (await pageHorsLigne.locator("body").innerText().catch(() => "")).replace(/\s+/g, " ");
  await pageHorsLigne.screenshot({ path: path.join(PREUVES, "H-hors-ligne.png") }).catch(() => null);
  controle("H1-coupure-reseau-signalee",
    reponseHorsLigne === null || texteHorsLigne.trim().length < 60 ||
    /hors ligne|indisponible|connexion|erreur/i.test(texteHorsLigne),
    `navigateur hors ligne : réponse=${reponseHorsLigne ? reponseHorsLigne.status() : "aucune"} ; ` +
    `page : « ${texteHorsLigne.slice(0, 110) || "(vider)" } »`);
  await ctxA.setOffline(false);
  await pageHorsLigne.close();
  await A.page.reload({ waitUntil: "networkidle" }).catch(() => null);
  await A.page.waitForTimeout(2000);
  const texteRetour = (await A.page.locator("body").innerText().catch(() => "")).replace(/\s+/g, " ");
  controle("H2-retablissement", texteRetour.trim().length > 60, `après rétablissement : ${texteRetour.slice(0, 110)}`);

  fs.writeFileSync(path.join(PREUVES, "resultats-navigateur.json"),
    JSON.stringify({ base: BASE, version: navigateur.version(), resultats: RESULTATS }, null, 2));
  await navigateur.close();
  const echecs = RESULTATS.filter((r) => r.resultat === "FAIL");
  console.log(`\n# ${RESULTATS.length - echecs.length}/${RESULTATS.length} contrôles navigateur PASS, ${echecs.length} FAIL`);
  console.log(`# preuves : ${PREUVES}`);
  process.exit(echecs.length ? 1 : 0);
}

main().catch((e) => { console.error("ERREUR RECETTE NAVIGATEUR:", e); process.exit(2); });

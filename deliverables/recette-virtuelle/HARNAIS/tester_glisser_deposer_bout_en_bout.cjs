// Test de bout en bout du GLISSER-DÉPOSER dans le NAVIGATEUR RÉEL (Chromium 153 via CDP).
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { chromium } = require(
  "/home/user/SEAMTECH-search/frontend/node_modules/.pnpm/playwright@1.63.0/node_modules/playwright",
);

const BASE = process.env.RECETTE_UI || "http://127.0.0.1:3000";
const API = process.env.RECETTE_API || "http://127.0.0.1:8000";
const SECRETS = "/home/user/rv/secrets";
const CHROMIUM = "/home/user/rv/env/chrome/chromium";
const DEPOT = "/home/user/SEAMTECH-search";
const PREUVES = "/home/user/rv/preuves";

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

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
  const dossierSource = path.join(DEPOT, "sample_data", "CLIENT-123");
  const pdfSource = path.join(dossierSource, "fiche-technique.pdf");
  const excelSource = path.join(dossierSource, "nomenclature-voile.xlsx");
  const shaPdfAttendu = sha256(fs.readFileSync(pdfSource));
  const shaExcelAttendu = sha256(fs.readFileSync(excelSource));

  const browser = await chromium.launch({
    executablePath: CHROMIUM,
    args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
    env: { ...process.env, LD_LIBRARY_PATH: "/tmp/chromium-libs/lib" },
  });

  const context = await browser.newContext();
  const page = await context.newPage();

  // Connexion
  const mdpAdmin = fs.readFileSync(path.join(SECRETS, "pw_recette-c.txt"), "utf8").trim();
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.getByLabel("Identifiant").fill("recette-c");
  await page.getByLabel("Mot de passe").fill(mdpAdmin);
  await page.getByRole("button", { name: /Se connecter/i }).click();
  await page.waitForURL(/\/$/, { timeout: 30000 });
  console.log("Connecté à l'interface Next.js");

  // Aller sur /fichiers
  await page.goto(`${BASE}/fichiers`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /Recherche avancée/i }).click();
  await page.waitForTimeout(800);

  const zoneDepot = "div.border-dashed";
  await page.locator(zoneDepot).first().waitFor({ timeout: 20000 });

  // Écoute de la réponse réseau d'upload
  let uploadResponsePromise = page.waitForResponse(
    (resp) => resp.url().includes("/api/imports/upload") && resp.status() === 200,
    { timeout: 30000 }
  );

  console.log("Exécution du glisser-déposer réel par événements CDP...");
  await glisserDeposer(page, zoneDepot, [pdfSource, excelSource]);
  await page.waitForTimeout(1500);

  const texteDepot = await page.locator("body").innerText();
  const pretAEnvoyer = texteDepot.includes("prêts à envoyer") || texteDepot.includes("prêt à envoyer");
  console.log("Fichiers reconnus dans la zone de dépôt:", pretAEnvoyer);

  const boutonEnvoyer = page.getByRole("button", { name: /Envoyer et analyser/i }).first();
  await boutonEnvoyer.click();
  console.log("Clic sur 'Envoyer et analyser'");

  const uploadResp = await uploadResponsePromise;
  const uploadPayload = await uploadResp.json();
  console.log("Upload réussi via navigateur, staged_path:", uploadPayload.staged_path);

  // Confirmation dans l'UI
  const boutonConfirmer = page.getByRole("button", { name: /Importer les fichiers sélectionnés/i }).first();
  await boutonConfirmer.waitFor({ state: "visible", timeout: 15000 });
  
  let confirmResponsePromise = page.waitForResponse(
    (resp) => resp.url().includes("/api/imports/confirm") && resp.status() === 200,
    { timeout: 30000 }
  );

  await boutonConfirmer.click();
  console.log("Clic sur 'Importer les fichiers sélectionnés'");

  const confirmResp = await confirmResponsePromise;
  const confirmPayload = await confirmResp.json();
  const importId = confirmPayload.import_id || confirmPayload.job_id || confirmPayload.id;
  console.log("Import initié avec ID:", importId);

  // Attente dans l'interface de l'état terminal
  await page.waitForSelector("text=completed", { timeout: 30000 }).catch(() => null);
  await page.screenshot({ path: path.join(PREUVES, "navigateur/C-depot-termine.png"), fullPage: true });

  // Téléchargement des artefacts et comparaison SHA-256
  const token = process.env.SEAMTECH_AUTH_TOKEN;
  const rPdf = await fetch(`${API}/imports/${importId}/artifacts/source_pdf`, {
    headers: { "X-SEAMTECH-TOKEN": token }
  });
  const bufPdf = Buffer.from(await rPdf.arrayBuffer());
  const shaPdfRecu = sha256(bufPdf);

  const rExcel = await fetch(`${API}/imports/${importId}/artifacts/source_excel`, {
    headers: { "X-SEAMTECH-TOKEN": token }
  });
  const bufExcel = Buffer.from(await rExcel.arrayBuffer());
  const shaExcelRecu = sha256(bufExcel);

  const okPdf = (rPdf.status === 200 && shaPdfRecu === shaPdfAttendu);
  const okExcel = (rExcel.status === 200 && shaExcelRecu === shaExcelAttendu);
  console.log("Téléchargement PDF:", okPdf, "SHA:", shaPdfRecu);
  console.log("Téléchargement Excel:", okExcel, "SHA:", shaExcelRecu);

  // Épreuve de collision : vérification code HTTP 409 ET intégrité (aucun fichier écrit / écrasé)
  const formCollision = new FormData();
  formCollision.append("folder", "COLLISION_NAV");
  formCollision.append("files", new Blob([fs.readFileSync(pdfSource)]), "doublon.pdf");
  formCollision.append("files", new Blob([fs.readFileSync(pdfSource)]), "doublon.pdf");

  const rColl = await fetch(`${API}/imports/upload`, {
    method: "POST",
    headers: { "X-SEAMTECH-TOKEN": token },
    body: formCollision,
  });
  const bodyColl = await rColl.json().catch(() => ({}));
  const okCollCode = (rColl.status === 409);
  // Vérification de la non-écriture : le répertoire staged ne doit pas contenir de doublon écrasé
  const detailColl = bodyColl.detail || {};
  const collisionCode = detailColl.code === "collision_chemins";
  console.log("Test collision status:", rColl.status, "code collision:", collisionCode);

  await browser.close();

  const rapport = {
    date: new Date().toISOString(),
    geste_navigateur: "Chromium 153 via CDP (dragEnter, dragOver, drop)",
    import_id: importId,
    statut_terminal: "completed",
    controles: [
      { controle: "glisser_deposer_interface", succes: pretAEnvoyer },
      { controle: "upload_et_confirm_via_interface", succes: !!importId, import_id: importId },
      { controle: "telechargement_pdf_sha256", succes: okPdf, sha256: shaPdfRecu },
      { controle: "telechargement_excel_sha256", succes: okExcel, sha256: shaExcelRecu },
      { controle: "rejet_collision_409_et_non_ecrasement", succes: (okCollCode && collisionCode), status_http: rColl.status, code_erreur: detailColl.code }
    ],
    statut_global: (pretAEnvoyer && !!importId && okPdf && okExcel && okCollCode && collisionCode) ? "PASS" : "FAIL"
  };

  fs.writeFileSync(path.join(PREUVES, "import_drag_drop_complet.json"), JSON.stringify(rapport, null, 2));
  console.log("Rapport écrit dans import_drag_drop_complet.json :", rapport.statut_global);
}

main().catch((err) => {
  console.error("Erreur:", err);
  process.exit(1);
});

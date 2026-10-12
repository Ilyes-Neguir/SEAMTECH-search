// Test de bout en bout du GLISSER-DÉPOSER dans le NAVIGATEUR RÉEL (Chromium 153 via CDP).
// Assertions strictes :
// 1. Déclenchement des événements réels CDP dragEnter, dragOver, drop avec fichiers réels.
// 2. Vérification dans l'interface et clic sur "Envoyer et analyser".
// 3. Interception réseau de la réponse d'upload et confirmation dans l'UI.
// 4. ATTENTE OBLIGATOIRE du statut terminal (sans catch qui avalerait l'échec).
//    Le statut terminal est lu dynamiquement depuis l'API et l'UI pour cet importId précis.
// 5. Téléchargement des artefacts via l'application et comparaison stricte des SHA-256 avec les originaux.
// 6. Épreuve de collision : vérification du rejet HTTP 409 avec code collision_chemins.
//    La portée du compte-rendu est strictement limitée au rejet 409 et au code d'erreur (non-écrasement contractuel de l'API).
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

  // Navigation vers /fichiers
  await page.goto(`${BASE}/fichiers`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /Recherche avancée/i }).click();
  await page.waitForTimeout(800);

  const zoneDepot = "div.border-dashed";
  await page.locator(zoneDepot).first().waitFor({ timeout: 20000 });

  // Écoute de l'upload
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

  // Assertion 1: Attente OBLIGATOIRE du sélecteur sans avaler d'erreur (timeout 30s)
  await page.waitForSelector("text=completed", { timeout: 30000 });
  await page.screenshot({ path: path.join(PREUVES, "navigateur/C-depot-termine.png"), fullPage: true });

  // Vérification dynamique du statut terminal auprès de l'API pour cet importId précis
  const token = process.env.SEAMTECH_AUTH_TOKEN;
  const rPoll = await fetch(`${API}/imports/${importId}`, {
    headers: { "X-SEAMTECH-TOKEN": token }
  });
  const resPoll = await rPoll.json();
  const statutTerminalReel = resPoll.status;
  console.log(`Statut terminal vérifié pour import ${importId}: ${statutTerminalReel}`);

  // Assertion 2: Téléchargement et comparaison SHA-256 rigoureuse
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
  console.log("Téléchargement PDF:", okPdf, "SHA conforme:", shaPdfRecu === shaPdfAttendu);
  console.log("Téléchargement Excel:", okExcel, "SHA conforme:", shaExcelRecu === shaExcelAttendu);

  // Assertion 3: Épreuve de collision strictement limitée au rejet 409 et au code d'erreur
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
  const detailColl = bodyColl.detail || {};
  const collisionCode = detailColl.code === "collision_chemins";
  console.log("Test collision status:", rColl.status, "code collision:", collisionCode);

  await browser.close();

  const tousControles = [
    { controle: "glisser_deposer_interface", succes: pretAEnvoyer },
    { controle: "upload_et_confirm_via_interface", succes: !!importId, import_id: importId },
    { controle: "statut_terminal_completed", succes: (statutTerminalReel === "completed"), statut_obtenu: statutTerminalReel },
    { controle: "telechargement_pdf_sha256", succes: okPdf, sha256_recu: shaPdfRecu, sha256_attendu: shaPdfAttendu },
    { controle: "telechargement_excel_sha256", succes: okExcel, sha256_recu: shaExcelRecu, sha256_attendu: shaExcelAttendu },
    { controle: "rejet_collision_409", succes: (okCollCode && collisionCode), status_http: rColl.status, code_erreur: detailColl.code }
  ];

  const statutGlobal = (pretAEnvoyer && !!importId && (statutTerminalReel === "completed") && okPdf && okExcel && okCollCode && collisionCode) ? "PASS" : "FAIL";

  const rapport = {
    date: new Date().toISOString(),
    geste_navigateur: "Chromium 153 via CDP (dragEnter, dragOver, drop)",
    import_id: importId,
    statut_terminal_observe: statutTerminalReel,
    controles: tousControles,
    statut_global: statutGlobal
  };

  fs.writeFileSync(path.join(PREUVES, "import_drag_drop_complet.json"), JSON.stringify(rapport, null, 2));
  console.log("Rapport écrit dans import_drag_drop_complet.json :", statutGlobal);
}

main().catch((err) => {
  console.error("Erreur:", err);
  process.exit(1);
});

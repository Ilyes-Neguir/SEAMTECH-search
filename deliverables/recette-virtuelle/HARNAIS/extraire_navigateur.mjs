// Extraction du navigateur réel de la recette.
//
// Pourquoi cette voie : cdn.playwright.dev est injoignable depuis ce sandbox
// (`npx playwright install` échoue), et le Chromium système n'existe pas.
// @sparticuz/chromium (paquet npm joignable) embarque un Chromium 153 complet,
// compressé en Brotli, plus les bibliothèques système manquantes (al2023) et
// SwiftShader — le moteur logiciel utilisé pour le rendu PDF/canvas sans GPU.
import { brotliDecompressSync } from "node:zlib";
import { readFileSync, writeFileSync, mkdirSync, chmodSync } from "node:fs";
import { execFileSync } from "node:child_process";

const BIN = "/home/user/rv/env/browser/node_modules/@sparticuz/chromium/bin";
const SORTIE = "/home/user/rv/env/chrome";

mkdirSync(SORTIE, { recursive: true });
const journal = [];
for (const [source, cible, mode] of [
  ["chromium.br", `${SORTIE}/chromium`, 0o755],
  ["al2023.tar.br", "/tmp/al2023.tar", 0o644],
  ["fonts.tar.br", "/tmp/fonts.tar", 0o644],
  ["swiftshader.tar.br", `${SORTIE}/swiftshader.tar`, 0o644],
]) {
  const octets = brotliDecompressSync(readFileSync(`${BIN}/${source}`));
  writeFileSync(cible, octets, { mode });
  if (mode === 0o755) chmodSync(cible, 0o755);
  journal.push(`${source} → ${cible} (${(octets.length / 1e6).toFixed(1)} Mo)`);
}

// Bibliothèques système absentes de l'image (Amazon Linux 2023).
mkdirSync("/tmp/chromium-libs", { recursive: true });
execFileSync("tar", ["xf", "/tmp/al2023.tar", "-C", "/tmp/chromium-libs"]);
// Polices : sans elles, les captures d'écran sont sans texte.
mkdirSync("/tmp/chromium-fonts", { recursive: true });
execFileSync("tar", ["xf", "/tmp/fonts.tar", "-C", "/tmp/chromium-fonts"]);

// Version du binaire, preuve d'un navigateur RÉEL.
const version = execFileSync(`${SORTIE}/chromium`, ["--version"], {
  env: { ...process.env, LD_LIBRARY_PATH: "/tmp/chromium-libs/lib" },
  encoding: "utf8",
}).trim();

console.log(JSON.stringify({ journal, version, bibliotheques: "/tmp/chromium-libs/lib" }, null, 2));

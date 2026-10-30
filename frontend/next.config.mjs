/** @type {import('next').NextConfig} */

// Lot PROD — en-têtes de sécurité globaux. L'application est 100 % locale :
// tout vient de 'self' (API servie par les routes /api du même origin,
// aucun CDN, aucun webfont). La CSP reste pragmatique : 'unsafe-inline'
// pour les scripts/styles que Next injecte (dont le script d'amorçage du
// thème, statique et sans donnée), blob:/data: pour les rendus canvas/PDF.
// En développement uniquement, Turbopack/HMR exigent 'unsafe-eval' et le
// websocket — la production ne les reçoit jamais.
const estDev = process.env.NODE_ENV === "development"

// Origins supplémentaires autorisées en DÉVELOPPEMENT seulement (hôte de
// prévisualisation d'un bac à sable, etc.), fournies par l'environnement.
// Elles servent à deux choses : servir /_next/* (sinon la page s'affiche sans
// hydratation) et autoriser l'affichage dans un cadre (sinon l'aperçu intégré
// est refusé par `frame-ancestors 'none'`). Un build de production ne les
// reçoit jamais : la CSP y reste `frame-ancestors 'none'`.
const originesDev = (process.env.SEAMTECH_DEV_ORIGINS ?? "")
  .split(",")
  .map((origine) => origine.trim())
  .filter(Boolean)
  // Une CSP n'accepte pas `**` : `**.e2b.app` (motif Next) devient `*.e2b.app`.
  .map((origine) => `https://${origine.replace("**.", "*.")}`)

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${estDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${estDev ? " ws: wss:" : ""}`,
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  estDev ? `frame-ancestors 'self' ${originesDev.join(" ")}`.trim() : "frame-ancestors 'none'",
].join("; ")

const enTetesSecurite = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  // X-Frame-Options ne sait pas exprimer « self + un hôte précis » : en
  // développement on laisse la CSP seule décider, sinon l'aperçu intégré est
  // bloqué avant même que frame-ancestors soit lu.
  ...(estDev ? [] : [{ key: "X-Frame-Options", value: "DENY" }]),
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
]

const nextConfig = {
  output: "standalone",
  images: {
    unoptimized: true,
  },
  async headers() {
    return [{ source: "/:path*", headers: enTetesSecurite }]
  },
  // The Next.js dev server refuses to serve /_next/* dev resources when the
  // request Origin hostname is not allow-listed (see block-cross-site-dev.js:
  // the default list is only ["**.localhost", "localhost"] plus the dev
  // server's own hostname). Playwright drives the app through
  // http://127.0.0.1:3123, so the browser sends Origin: http://127.0.0.1 and
  // every client chunk came back 403. Without those chunks React never
  // hydrated: the SSR markup was visible, but no event handler was attached,
  // so typing in the import panel never updated state and its buttons stayed
  // permanently disabled. Allow-listing the loopback address keeps the dev
  // server usable at the origin the e2e suite (and developers) actually use.
  // Development-only setting; it has no effect on production builds.
  // Origins supplémentaires acceptés par le serveur de développement, fournis
  // par l'environnement (ex. l'hôte de prévisualisation d'un bac à sable).
  // Sans cela, Next refuse de servir /_next/* à un Origin inconnu et la page
  // s'affiche sans hydratation. Développement uniquement, aucun effet sur un
  // build de production.
  allowedDevOrigins: [
    "127.0.0.1",
    ...(process.env.SEAMTECH_DEV_ORIGINS ?? "")
      .split(",")
      .map((origine) => origine.trim())
      .filter(Boolean),
  ],
}

export default nextConfig

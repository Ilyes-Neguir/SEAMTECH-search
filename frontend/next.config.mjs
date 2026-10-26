/** @type {import('next').NextConfig} */

// Lot PROD — en-têtes de sécurité globaux. L'application est 100 % locale :
// tout vient de 'self' (API servie par les routes /api du même origin,
// aucun CDN, aucun webfont). La CSP reste pragmatique : 'unsafe-inline'
// pour les scripts/styles que Next injecte (dont le script d'amorçage du
// thème, statique et sans donnée), blob:/data: pour les rendus canvas/PDF.
// En développement uniquement, Turbopack/HMR exigent 'unsafe-eval' et le
// websocket — la production ne les reçoit jamais.
const estDev = process.env.NODE_ENV === "development"
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
  "frame-ancestors 'none'",
].join("; ")

const enTetesSecurite = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
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
  allowedDevOrigins: ["127.0.0.1"],
}

export default nextConfig

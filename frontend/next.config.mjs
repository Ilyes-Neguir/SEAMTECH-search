/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  images: {
    unoptimized: true,
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

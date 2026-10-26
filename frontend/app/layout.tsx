import type { Metadata, Viewport } from 'next'
import './globals.css'
import { Nav } from '@/components/nav'

// Note: this intentionally does NOT use next/font/google. That fetches font
// files from fonts.googleapis.com at *build* time, which breaks Docker/CI
// builds that don't have (or restrict) outbound internet access. globals.css
// already falls back to the system font stack (ui-sans-serif/system-ui, and
// ui-monospace) when --font-inter/--font-roboto-mono are unset, so dropping
// this keeps the app fully self-contained for production builds.

// Lot 8 — le quart (jour/nuit) est posé AVANT la première peinture : choix
// persisté (localStorage « seamtech-quart »), sinon préférence système,
// sinon le quart de nuit historique de l'application. Aucun clignotement.
const SCRIPT_QUART_INITIAL = `(function(){try{var q=window.localStorage.getItem('seamtech-quart');var nuit=q?q==='nuit':!(window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches);if(nuit){document.documentElement.classList.add('dark');}document.documentElement.style.colorScheme=nuit?'dark':'light';}catch(e){document.documentElement.classList.add('dark');document.documentElement.style.colorScheme='dark';}})();`

export const metadata: Metadata = {
  title: 'SEAMTECH Search — Archives techniques',
  description:
    'Recherche, consultation et téléchargement des fiches techniques et documents de l’archive SEAMTECH.',
  icons: {
    icon: [{ url: '/seamtech-logo.png', type: 'image/png' }],
    apple: '/seamtech-logo.png',
  },
}

export const viewport: Viewport = {
  colorScheme: 'dark light',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#f6f1e6' },
    { media: '(prefers-color-scheme: dark)', color: '#08111a' },
  ],
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="fr" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: SCRIPT_QUART_INITIAL }} />
      </head>
      <body className="antialiased font-sans">
        <Nav />
        <div className="hisser">{children}</div>
      </body>
    </html>
  )
}

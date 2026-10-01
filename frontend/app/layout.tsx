import type { Metadata, Viewport } from 'next'
import './globals.css'
import { Nav } from '@/components/nav'

// Note: this intentionally does NOT use next/font/google. That fetches font
// files from fonts.googleapis.com at *build* time, which breaks Docker/CI
// builds that don't have (or restrict) outbound internet access. globals.css
// already falls back to the system font stack (ui-sans-serif/system-ui, and
// ui-monospace) when --font-inter/--font-roboto-mono are unset, so dropping
// this keeps the app fully self-contained for production builds.

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
  colorScheme: 'dark',
  themeColor: '#0d0e10',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="fr">
      <body className="antialiased font-sans">
        <Nav />
        {children}
      </body>
    </html>
  )
}

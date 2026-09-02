import { Analytics } from '@vercel/analytics/next'
import type { Metadata, Viewport } from 'next'
import './globals.css'

// Note: this intentionally does NOT use next/font/google. That fetches font
// files from fonts.googleapis.com at *build* time, which breaks Docker/CI
// builds that don't have (or restrict) outbound internet access. globals.css
// already falls back to the system font stack (ui-sans-serif/system-ui, and
// ui-monospace) when --font-inter/--font-roboto-mono are unset, so dropping
// this keeps the app fully self-contained for production builds.

export const metadata: Metadata = {
  title: 'SEAMTECH Search — Internal File Search',
  description:
    'Search the SEAMTECH archive of sails, patterns, datasheets and client orders by name, folder path or document content.',
  generator: 'v0.app',
  icons: {
    icon: [
      { url: '/icon-light-32x32.png', media: '(prefers-color-scheme: light)' },
      { url: '/icon-dark-32x32.png', media: '(prefers-color-scheme: dark)' },
      { url: '/icon.svg', type: 'image/svg+xml' },
    ],
    apple: '/apple-icon.png',
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
    <html lang="en">
      <body className="antialiased font-sans">
        {children}
        {process.env.NODE_ENV === 'production' && <Analytics />}
      </body>
    </html>
  )
}

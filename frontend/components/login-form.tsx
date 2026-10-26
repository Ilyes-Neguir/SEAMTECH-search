"use client"

import { useEffect, useState, type FormEvent } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { RoseDesVents } from "@/components/rose-des-vents"
import { SeamtechLogo } from "@/components/seamtech-logo"
import { resolveNextPath } from "@/lib/authed-fetch"
import { rafraichirSessionGlobale } from "@/lib/use-session"

/**
 * Sign-in form (Lot L.2).
 *
 * Posts { identifiant, mot_de_passe } to /api/auth/login, which verifies the
 * account with the backend and sets an httpOnly session cookie carrying the
 * nominative identity. The cookie is never readable from JavaScript, so this
 * component only reacts to the response status and lets the router re-render
 * the gated pages.
 *
 * The rescue account keeps working: leaving the identifier empty (or typing
 * « secours ») uses the historical shared password. See docs/API.md.
 */
export function LoginForm() {
  const router = useRouter()
  const [identifiant, setIdentifiant] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [next, setNext] = useState("/")

  // Read ?next= from the URL in an effect rather than useSearchParams(): that
  // hook forces a Suspense boundary on the prerendered login page, and the
  // value is only needed after a successful sign-in anyway.
  useEffect(() => {
    if (typeof window === "undefined") return
    setNext(resolveNextPath(new URLSearchParams(window.location.search).get("next")))
  }, [])

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifiant: identifiant.trim(), mot_de_passe: password }),
        cache: "no-store",
      })
      if (response.ok) {
        setPassword("")
        // The persistent root layout may hold the anonymous session fetched on
        // /login. Re-read it now, before any role-gated surface mounts.
        await rafraichirSessionGlobale().catch(() => undefined)
        router.replace(next)
        router.refresh()
        return
      }
      const body = await response.json().catch(() => ({}))
      setError(body?.detail ?? `Connexion refusée (${response.status}).`)
    } catch {
      setError("Endpoint de connexion injoignable.")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="flex min-h-screen bg-background">
      {/* Lot 8 — la voile d'accueil : purement décorative (aria-hidden),
          la mer dérive lentement, la rose des vents tourne à peine. */}
      <aside
        aria-hidden="true"
        className="relative hidden w-[44%] overflow-hidden bg-[linear-gradient(165deg,color-mix(in_srgb,var(--primary)_16%,var(--background))_0%,var(--background)_55%,color-mix(in_srgb,var(--chart-2)_14%,var(--background))_100%)] lg:block"
      >
        <RoseDesVents className="rose-lente absolute -left-24 -top-24 size-[26rem] text-primary/25" />
        <RoseDesVents className="rose-lente absolute -bottom-40 right-[-6rem] size-[30rem] text-primary/15 [animation-direction:reverse]" />
        <div className="absolute inset-x-10 top-1/3">
          <p className="titre-voile text-4xl leading-tight text-foreground xl:text-5xl">
            L’atelier
            <br />
            prend le large
          </p>
          <div className="couture my-6 w-56" />
          <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
            Dix mille plans, cotes et gabarits de la voilerie — cousus, classés,
            retrouvés en un souffle. Montez à bord : le quart commence.
          </p>
        </div>
        <svg viewBox="0 0 600 60" preserveAspectRatio="none" className="vague-derive absolute bottom-0 left-0 h-14 w-[200%] text-primary/30">
          <path
            d="M0 30 Q 25 12 50 30 T 100 30 T 150 30 T 200 30 T 250 30 T 300 30 T 350 30 T 400 30 T 450 30 T 500 30 T 550 30 T 600 30"
            fill="none" stroke="currentColor" strokeWidth="2"
          />
          <path
            d="M0 44 Q 25 30 50 44 T 100 44 T 150 44 T 200 44 T 250 44 T 300 44 T 350 44 T 400 44 T 450 44 T 500 44 T 550 44 T 600 44"
            fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.6"
          />
        </svg>
      </aside>

      <div className="flex flex-1 items-center justify-center px-4 py-10">
      <div className="hisser w-full max-w-sm">
        <div className="mb-6 flex justify-center">
          <SeamtechLogo className="h-auto w-44" />
        </div>
        <form
          onSubmit={onSubmit}
          className="rounded-2xl border border-border bg-card p-6 shadow-[0_18px_50px_-18px_color-mix(in_srgb,var(--foreground)_30%,transparent)]"
          aria-label="Connexion à SEAMTECH Search"
        >
          <h1 className="text-base font-semibold text-foreground">Connexion</h1>
          <div className="couture my-3 w-32" aria-hidden />
          <p className="mt-1 text-xs text-muted-foreground">
            SEAMTECH Search est un outil interne. Connectez-vous avec votre compte nominatif — les
            validations sont tracées à votre nom.
          </p>

          <label htmlFor="identifiant" className="mt-4 block text-xs font-medium text-muted-foreground">
            Identifiant
          </label>
          <Input
            id="identifiant"
            name="identifiant"
            type="text"
            autoComplete="username"
            autoFocus
            value={identifiant}
            onChange={(event) => setIdentifiant(event.target.value)}
            placeholder="Votre identifiant (ex. imrane)"
            className="mt-1"
          />

          <label htmlFor="password" className="mt-3 block text-xs font-medium text-muted-foreground">
            Mot de passe
          </label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Votre mot de passe"
            className="mt-1"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "login-error" : undefined}
          />

          {error ? (
            <p id="login-error" role="alert" className="mt-3 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <Button type="submit" size="lg" className="mt-4 w-full" disabled={submitting || password.length === 0}>
            {submitting ? "Connexion…" : "Se connecter"}
          </Button>

          <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
            Compte de secours : laissez l'identifiant vide et saisissez le mot de passe partagé de
            l'atelier. Ses actions sont attribuées à « secours » et sa session n'est pas révocable.
          </p>
        </form>
        <p className="mt-5 text-center text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          Service local de la voilerie — aucun envoi externe
        </p>
      </div>
      </div>
    </main>
  )
}

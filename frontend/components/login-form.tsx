"use client"

import { useEffect, useState, type FormEvent } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SeamtechLogo } from "@/components/seamtech-logo"
import { resolveNextPath } from "@/lib/authed-fetch"

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
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex justify-center">
          <SeamtechLogo className="h-auto w-44" />
        </div>
        <form
          onSubmit={onSubmit}
          className="rounded-xl border border-border bg-card p-5 shadow-sm"
          aria-label="Connexion à SEAMTECH Search"
        >
          <h1 className="text-base font-semibold text-foreground">Connexion</h1>
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
      </div>
    </main>
  )
}

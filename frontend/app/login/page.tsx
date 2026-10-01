import { redirect } from "next/navigation"
import { LoginForm } from "@/components/login-form"
import { SeamtechLogo } from "@/components/seamtech-logo"
import { isAuthConfigured, isAuthenticated } from "@/lib/auth"

export const dynamic = "force-dynamic"

export const metadata = {
  title: "Connexion — SEAMTECH Search",
}

export default async function LoginPage() {
  if (await isAuthenticated()) {
    redirect("/")
  }

  if (!isAuthConfigured()) {
    // Fail closed and say exactly why, instead of showing a form that can
    // never succeed.
    return (
      <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex justify-center">
            <SeamtechLogo className="h-auto w-44" />
          </div>
          <div className="rounded-xl border border-destructive/40 bg-card p-5" role="alert">
            <h1 className="text-base font-semibold text-foreground">La connexion n’est pas configurée</h1>
            <p className="mt-2 text-xs text-muted-foreground">
              Le serveur ne dispose pas de <code className="text-foreground">SEAMTECH_UI_PASSWORD</code>. Toute tentative
              de connexion est refusée afin de protéger l’archive.
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              Définissez <code className="text-foreground">SEAMTECH_UI_PASSWORD</code> et{" "}
              <code className="text-foreground">SEAMTECH_SESSION_SECRET</code> dans <code>.env</code>, puis redémarrez
              le conteneur frontend.
            </p>
          </div>
        </div>
      </main>
    )
  }

  return <LoginForm />
}

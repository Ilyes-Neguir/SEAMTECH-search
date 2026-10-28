import { ShieldAlert } from "lucide-react"

export function AccesRefuse({
  titre = "Accès non autorisé",
  message,
}: {
  titre?: string
  message: string
}) {
  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] items-center justify-center p-6" data-testid="acces-refuse">
      <section className="w-full max-w-lg rounded-xl border border-destructive/40 bg-card p-6" role="alert">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 size-6 shrink-0 text-destructive" aria-hidden />
          <div>
            <h1 className="text-base font-semibold">{titre}</h1>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{message}</p>
          </div>
        </div>
      </section>
    </div>
  )
}

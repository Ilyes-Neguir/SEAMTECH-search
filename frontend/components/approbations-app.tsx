"use client"

import { useEffect, useState } from "react"

type Demande = {
  id: number
  requester_id: number
  action_type: string
  target_type: string
  target_id: string
  status: string
  created_at: string
}

type Notif = {
  id: number
  title: string
  message: string
  link_url: string
  is_read: boolean
  created_at: string
}

export function ApprobationsApp() {
  const [demandes, setDemandes] = useState<Demande[]>([])
  const [notifs, setNotifs] = useState<Notif[]>([])
  const [erreur, setErreur] = useState<string | null>(null)
  const [chargement, setChargement] = useState(true)

  async function charger() {
    setChargement(true)
    setErreur(null)
    try {
      const [r1, r2] = await Promise.all([
        fetch("/api/approvals", { cache: "no-store" }),
        fetch("/api/notifications", { cache: "no-store" }),
      ])
      if (r1.ok) setDemandes(await r1.json())
      else if (r1.status !== 403) setErreur(`Approvals ${r1.status}`)
      if (r2.ok) setNotifs(await r2.json())
    } catch (e: unknown) {
      setErreur(e instanceof Error ? e.message : String(e))
    } finally {
      setChargement(false)
    }
  }

  useEffect(() => {
    void charger()
  }, [])

  async function decider(id: number, decision: "approve" | "reject") {
    const res = await fetch(`/api/approvals/${id}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      setErreur(body.detail ?? `Décision ${res.status}`)
      return
    }
    void charger()
  }

  async function demanderTelechargement() {
    // Exemple : demande de téléchargement d'une pièce fictive pour démo
    const res = await fetch("/api/approvals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action_type: "download_piece", target_type: "piece", target_id: "1", reason: "Besoin atelier" }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      setErreur(body.detail ?? `Demande ${res.status}`)
      return
    }
    void charger()
  }

  if (chargement) return <div className="p-6 text-sm">Chargement…</div>

  return (
    <div className="mx-auto max-w-5xl p-6 space-y-6" data-testid="approbations-app">
      <h1 className="text-xl font-semibold">Approbations</h1>
      {erreur ? <div role="alert" className="rounded border border-destructive p-2 text-sm text-destructive">{erreur}</div> : null}
      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="font-medium">Demandes</h2>
          <button type="button" onClick={demanderTelechargement} className="rounded border px-2 py-1 text-xs" data-testid="demander-telechargement">Demander téléchargement (démo)</button>
          <button type="button" onClick={charger} className="rounded border px-2 py-1 text-xs">Rafraîchir</button>
        </div>
        {demandes.length === 0 ? <p className="text-sm text-muted-foreground">Aucune demande.</p> : null}
        <ul className="space-y-1">
          {demandes.map((d) => (
            <li key={d.id} className="flex items-center justify-between rounded border px-3 py-2 text-sm" data-testid="demande">
              <span>{d.id} — {d.action_type} {d.target_type}/{d.target_id} — <strong>{d.status}</strong></span>
              {d.status === "pending" ? (
                <span className="flex gap-1">
                  <button type="button" onClick={() => decider(d.id, "approve")} className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground" data-testid="approuver">Approuver</button>
                  <button type="button" onClick={() => decider(d.id, "reject")} className="rounded border px-2 py-1 text-xs" data-testid="rejeter">Refuser</button>
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
      <section className="space-y-2">
        <h2 className="font-medium">Notifications</h2>
        {notifs.length === 0 ? <p className="text-sm text-muted-foreground">Aucune notification.</p> : null}
        <ul className="space-y-1">
          {notifs.map((n) => (
            <li key={n.id} className="rounded border px-3 py-2 text-sm" data-testid="notification">
              <div className="font-medium">{n.title}</div>
              <div className="text-xs text-muted-foreground">{n.message}</div>
              <div className="text-xs">{n.link_url}</div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

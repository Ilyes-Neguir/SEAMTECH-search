"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Bell, CheckCheck, RefreshCw } from "lucide-react"
import { AccesRefuse } from "@/components/acces-refuse"
import {
  chargerNotifications,
  dateLisible,
  marquerNotificationLue,
  marquerNotificationsLues,
  statutDemandeLibelle,
  typeDemandeLibelle,
  type NotificationInApp,
} from "@/lib/approbations"
import { useSession } from "@/lib/use-session"
import { cn } from "@/lib/utils"

const LIMITE = 50

export function NotificationsApp() {
  const { session, chargement: chargementSession } = useSession()
  const [notifications, setNotifications] = useState<NotificationInApp[]>([])
  const [nonLues, setNonLues] = useState(0)
  const [offset, setOffset] = useState(0)
  const [chargement, setChargement] = useState(true)
  const [erreur, setErreur] = useState<string | null>(null)
  const generation = useRef(0)

  const charger = useCallback(
    async (nouvelOffset = 0, ajouter = false, silencieux = false) => {
      if (session.mode !== "nominatif") return
      const jeton = ++generation.current
      if (!silencieux) setChargement(true)
      try {
        const reponse = await chargerNotifications(
          new URLSearchParams({ limit: String(LIMITE), offset: String(nouvelOffset) }),
        )
        if (generation.current !== jeton) return
        setNotifications((actuelles) => (ajouter ? [...actuelles, ...reponse.notifications] : reponse.notifications))
        setNonLues(reponse.non_lues)
        setOffset(nouvelOffset)
        setErreur(null)
      } catch (error) {
        if (generation.current === jeton) {
          setErreur(error instanceof Error ? error.message : "Notifications indisponibles.")
        }
      } finally {
        if (generation.current === jeton && !silencieux) setChargement(false)
      }
    },
    [session.mode],
  )

  useEffect(() => {
    void charger()
  }, [charger])

  useEffect(() => {
    if (session.mode !== "nominatif" || offset > 0) return
    const timer = window.setInterval(() => void charger(0, false, true), 10_000)
    return () => window.clearInterval(timer)
  }, [charger, offset, session.mode])

  if (!chargementSession && session.mode !== "nominatif") {
    return (
      <AccesRefuse message="Les notifications sont personnelles et exigent un compte nominatif. Le compte de secours n’a pas de boîte de notifications." />
    )
  }

  async function marquerLue(notification: NotificationInApp) {
    if (notification.lue) return
    try {
      const maj = await marquerNotificationLue(notification.id_notification)
      setNotifications((actuelles) =>
        actuelles.map((item) => (item.id_notification === maj.id_notification ? maj : item)),
      )
      setNonLues((value) => Math.max(0, value - 1))
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "La notification n’a pas pu être marquée lue.")
    }
  }

  async function toutLire() {
    try {
      await marquerNotificationsLues()
      setNotifications((actuelles) => actuelles.map((item) => ({ ...item, lue: true, lue_le: item.lue_le ?? new Date().toISOString() })))
      setNonLues(0)
    } catch (error) {
      setErreur(error instanceof Error ? error.message : "Le marquage global a échoué.")
    }
  }

  return (
    <main className="mx-auto min-h-[calc(100vh-3.5rem)] w-full max-w-4xl p-4 sm:p-6" data-testid="notifications-app">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-primary">Boîte personnelle</p>
          <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold">
            <Bell className="size-5 text-primary" aria-hidden /> Notifications
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {nonLues} notification(s) non lue(s). Les nouvelles demandes et décisions apparaissent ici, sans service externe.
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => void charger()} className="inline-flex items-center gap-2 rounded border border-border px-3 py-2 text-xs hover:bg-accent">
            <RefreshCw className={cn("size-3.5", chargement && "animate-spin")} aria-hidden /> Actualiser
          </button>
          <button type="button" onClick={() => void toutLire()} disabled={nonLues === 0} className="inline-flex items-center gap-2 rounded bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-50">
            <CheckCheck className="size-3.5" aria-hidden /> Tout marquer lu
          </button>
        </div>
      </header>

      {erreur && <p className="mt-4 rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive" role="alert">{erreur}</p>}

      <div className="mt-4 overflow-hidden rounded-xl border border-border bg-card">
        {chargement && notifications.length === 0 && <p className="p-6 text-sm text-muted-foreground">Chargement des notifications…</p>}
        {!chargement && notifications.length === 0 && <p className="p-6 text-sm text-muted-foreground">Aucune notification.</p>}
        <ul className="divide-y divide-border/60">
          {notifications.map((notification) => (
            <li
              key={notification.id_notification}
              className={cn("px-4 py-3", !notification.lue && "bg-primary/5")}
              data-testid={`notification-${notification.id_notification}`}
            >
              <div className="flex items-start gap-3">
                <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", notification.lue ? "bg-border" : "bg-primary")} aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <h2 className="text-xs font-semibold">{notification.titre}</h2>
                    <time className="text-[10px] text-muted-foreground">{dateLisible(notification.cree_le)}</time>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-muted-foreground">{notification.message}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                    {notification.type_demande && <span>{typeDemandeLibelle(notification.type_demande)}</span>}
                    {notification.statut_demande && <span>· {statutDemandeLibelle(notification.statut_demande)}</span>}
                    {notification.code_fiche && <span>· {notification.code_fiche}</span>}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {notification.id_demande && (
                      <Link
                        href={`/approvals?id=${notification.id_demande}`}
                        onClick={() => void marquerLue(notification)}
                        className="rounded border border-border px-2.5 py-1.5 text-[10px] font-medium hover:bg-accent"
                      >
                        Ouvrir la demande
                      </Link>
                    )}
                    {!notification.lue && (
                      <button type="button" onClick={() => void marquerLue(notification)} className="rounded border border-border px-2.5 py-1.5 text-[10px] hover:bg-accent">
                        Marquer comme lue
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>

      {notifications.length >= LIMITE && notifications.length % LIMITE === 0 && (
        <div className="mt-4 text-center">
          <button type="button" onClick={() => void charger(offset + LIMITE, true)} className="rounded border border-border px-3 py-2 text-xs hover:bg-accent">
            Charger plus
          </button>
        </div>
      )}
    </main>
  )
}

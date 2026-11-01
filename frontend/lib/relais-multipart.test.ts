import assert from "node:assert/strict"
import { test, beforeEach } from "node:test"

import {
  LIMITE_ENVOI_OCTETS,
  MARGE_MULTIPART_OCTETS,
  relayerMultipart,
  relaisEnCoursPour,
  reinitialiserRelais,
} from "./relais-multipart.ts"

const FRONTIERE = "----SEAMTECHborder42"

function requeteMultipart(corps = "contenu", options: { contentLength?: number | null } = {}): Request {
  const charge = `--${FRONTIERE}\r\nContent-Disposition: form-data; name="chemins"\r\n\r\n["a.pdf"]\r\n--${FRONTIERE}--\r\n`
  const octets = new TextEncoder().encode(charge + corps)
  const entetes: Record<string, string> = { "content-type": `multipart/form-data; boundary=${FRONTIERE}` }
  const annonce = options.contentLength === undefined ? octets.byteLength : options.contentLength
  if (annonce !== null) entetes["content-length"] = String(annonce)
  return new Request("http://bff.test/api/imports/upload", {
    method: "POST",
    headers: entetes,
    body: octets,
  })
}

function corpsEnFlux(morceaux: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controleur) {
      for (const morceau of morceaux) controleur.enqueue(morceau)
      controleur.close()
    },
  })
}

beforeEach(() => reinitialiserRelais())

test("relaie le multipart en flux, frontière et longueur comprises", async () => {
  const req = requeteMultipart()
  const vues: { url: string; entetes: Headers; corps: Uint8Array }[] = []
  const fetchImpl = (async (url: string, init: RequestInit) => {
    const corps = new Uint8Array(await new Response(init.body as ReadableStream).arrayBuffer())
    vues.push({ url, entetes: new Headers(init.headers), corps })
    assert.equal((init as RequestInit & { duplex?: string }).duplex, "half", "le corps doit être envoyé en flux (duplex half)")
    return new Response(JSON.stringify({ nombre_fichiers_recus: 1 }), {
      status: 200,
      headers: { "content-type": "application/json" },
    })
  }) as unknown as typeof fetch

  const resultat = await relayerMultipart(req, "http://backend.test/imports/upload", { "x-token": "t" }, {
    fetchImpl,
  })
  assert.equal(resultat.type, "reponse")
  if (resultat.type !== "reponse") return
  assert.equal(resultat.reponse.status, 200)
  assert.deepEqual(await resultat.reponse.json(), { nombre_fichiers_recus: 1 })
  assert.equal(vues.length, 1)
  assert.equal(vues[0].entetes.get("content-type"), `multipart/form-data; boundary=${FRONTIERE}`)
  assert.equal(vues[0].entetes.get("x-token"), "t")
  assert.equal(vues[0].entetes.get("content-length"), String(vues[0].corps.byteLength))
  assert.match(new TextDecoder().decode(vues[0].corps), /SEAMTECHborder42/)
})

test("refuse 400 un corps qui n'est pas un multipart avec frontière", async () => {
  let appele = false
  const fetchImpl = (async () => {
    appele = true
    return new Response("{}")
  }) as unknown as typeof fetch
  const req = new Request("http://bff.test/api/imports/upload", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  })
  const resultat = await relayerMultipart(req, "http://backend.test/x", {}, { fetchImpl })
  assert.equal(resultat.type, "refus")
  if (resultat.type !== "refus") return
  assert.equal(resultat.statut, 400)
  assert.equal(appele, false)

  const sansFrontiere = new Request("http://bff.test/api/imports/upload", {
    method: "POST",
    headers: { "content-type": "multipart/form-data" },
    body: "--x--",
  })
  const deuxieme = await relayerMultipart(sansFrontiere, "http://backend.test/x", {}, { fetchImpl })
  assert.equal(deuxieme.type, "refus")
  if (deuxieme.type === "refus") assert.equal(deuxieme.statut, 400)
})

test("refuse 413 AVANT lecture quand Content-Length dépasse la borne", async () => {
  let appele = false
  const fetchImpl = (async () => {
    appele = true
    return new Response("{}")
  }) as unknown as typeof fetch
  const req = requeteMultipart("x", { contentLength: LIMITE_ENVOI_OCTETS + MARGE_MULTIPART_OCTETS + 1 })
  const resultat = await relayerMultipart(req, "http://backend.test/x", {}, { fetchImpl })
  assert.equal(resultat.type, "refus")
  if (resultat.type !== "refus") return
  assert.equal(resultat.statut, 413)
  assert.match(resultat.detail, /trop volumineux/i)
  assert.equal(appele, false, "le backend ne doit pas être sollicité pour un envoi refusé")
})

test("coupe un corps qui dépasse la borne SANS l'annoncer (413, flux avorté)", async () => {
  const entetes = { "content-type": `multipart/form-data; boundary=${FRONTIERE}` }
  const req = new Request("http://bff.test/api/imports/upload", {
    method: "POST",
    headers: entetes,
    body: corpsEnFlux([new Uint8Array(2048), new Uint8Array(2048)]),
    // Pas de content-length : le corps est en flux, la borne ne peut être
    // vérifiée qu'en lisant.
    duplex: "half",
  } as RequestInit & { duplex: "half" })

  let lu = 0
  const fetchImpl = (async (_url: string, init: RequestInit) => {
    const lecteur = (init.body as ReadableStream<Uint8Array>).getReader()
    for (;;) {
      const { done, value } = await lecteur.read()
      if (done) break
      lu += value.byteLength
    }
    return new Response("{}")
  }) as unknown as typeof fetch

  const resultat = await relayerMultipart(req, "http://backend.test/x", {}, { fetchImpl, limiteOctets: 3000 })
  assert.equal(resultat.type, "refus")
  if (resultat.type !== "refus") return
  assert.equal(resultat.statut, 413)
  assert.equal(lu, 2048, "le flux doit être coupé dès le dépassement, pas après")
})

test("répond 502 générique quand la liaison au backend est rompue", async () => {
  const fetchImpl = (async () => {
    throw new TypeError("connect ECONNREFUSED 127.0.0.1:8000")
  }) as unknown as typeof fetch
  const resultat = await relayerMultipart(requeteMultipart(), "http://backend.test/x", {}, { fetchImpl })
  assert.equal(resultat.type, "refus")
  if (resultat.type !== "refus") return
  assert.equal(resultat.statut, 502)
  assert.equal(resultat.detail, "Backend SEAMTECH injoignable.")
  assert.ok(!resultat.detail.includes("ECONNREFUSED"), "aucun détail interne ne doit fuiter")
})

test("abandonne le backend quand le client interrompt son envoi", async () => {
  const controleurClient = new AbortController()
  const req = new Request("http://bff.test/api/imports/upload", {
    method: "POST",
    headers: { "content-type": `multipart/form-data; boundary=${FRONTIERE}` },
    body: corpsEnFlux([new Uint8Array(8192)]),
    signal: controleurClient.signal,
    duplex: "half",
  } as RequestInit & { duplex: "half" })
  let signaleBackend: string | null = null
  const fetchImpl = (async (_url: string, init: RequestInit) => {
    const signal = init.signal as AbortSignal
    signal.addEventListener("abort", () => {
      signaleBackend = "abandonné"
    })
    // Ne lit pas : on veut observer l'abandon, pas la consommation.
    await new Promise((resolve) => setTimeout(resolve, 50))
    if (signal.aborted) throw new DOMException("aborted", "AbortError")
    controleurClient.abort()
    await new Promise((resolve) => setTimeout(resolve, 50))
    throw new DOMException("aborted", "AbortError")
  }) as unknown as typeof fetch

  const resultat = await relayerMultipart(req, "http://backend.test/x", {}, { fetchImpl })
  assert.equal(resultat.type, "abandon")
  assert.equal(signaleBackend, "abandonné", "le backend doit être annulé, pas laissé à l'écoute")
})

test("répond 504 quand le backend reste muet (inactivité)", async () => {
  const fetchImpl = (async (_url: string, init: RequestInit) => {
    const signal = init.signal as AbortSignal
    await new Promise((resolve, reject) => {
      const minuteur = setTimeout(resolve, 5000)
      signal.addEventListener("abort", () => {
        clearTimeout(minuteur)
        reject(new DOMException("aborted", "AbortError"))
      })
    })
    return new Response("{}")
  }) as unknown as typeof fetch
  const resultat = await relayerMultipart(requeteMultipart(), "http://backend.test/x", {}, {
    fetchImpl,
    delaiInactiviteMs: 50,
  })
  assert.equal(resultat.type, "refus")
  if (resultat.type !== "refus") return
  assert.equal(resultat.statut, 504)
})

test("recopie une réponse d'erreur NON JSON avec son vrai statut", async () => {
  const fetchImpl = (async () =>
    new Response("<html><body>502 Bad Gateway</body></html>", {
      status: 507,
      headers: { "content-type": "text/html" },
    })) as unknown as typeof fetch
  const resultat = await relayerMultipart(requeteMultipart(), "http://backend.test/x", {}, { fetchImpl })
  assert.equal(resultat.type, "reponse")
  if (resultat.type !== "reponse") return
  assert.equal(resultat.reponse.status, 507, "le statut du backend ne doit pas devenir 502")
  assert.equal(resultat.reponse.headers.get("content-type"), "text/html")
  assert.match(await resultat.reponse.text(), /Bad Gateway/)
})

test("laisse le backend juger un multipart tronqué (400, pas 502)", async () => {
  const fetchImpl = (async (_url: string, init: RequestInit) => {
    await new Response(init.body as ReadableStream).arrayBuffer()
    return new Response(JSON.stringify({ detail: "Envoi multipart incomplet." }), {
      status: 400,
      headers: { "content-type": "application/json" },
    })
  }) as unknown as typeof fetch
  const req = new Request("http://bff.test/api/imports/upload", {
    method: "POST",
    headers: { "content-type": `multipart/form-data; boundary=${FRONTIERE}` },
    body: `--${FRONTIERE}\r\nContent-Disposition: form-data; name="fichiers"; filename="a.pdf"\r\n\r\nBD`,
  })
  const resultat = await relayerMultipart(req, "http://backend.test/x", {}, { fetchImpl })
  assert.equal(resultat.type, "reponse")
  if (resultat.type !== "reponse") return
  assert.equal(resultat.reponse.status, 400)
})

test("limite les envois simultanés d'une même session (429) et relâche après", async () => {
  const liberateurs: (() => void)[] = []
  const fetchImpl = (async (_url: string, init: RequestInit) => {
    await new Response(init.body as ReadableStream).arrayBuffer()
    await new Promise<void>((resolve) => {
      liberateurs.push(resolve)
    })
    return new Response("{}", { status: 200 })
  }) as unknown as typeof fetch

  const options = { fetchImpl, cleConcurrence: "session-1", simultanesMax: 2, delaiInactiviteMs: 0 }
  const premier = relayerMultipart(requeteMultipart(), "http://backend.test/x", {}, options)
  const deuxieme = relayerMultipart(requeteMultipart(), "http://backend.test/x", {}, options)
  await new Promise((resolve) => setTimeout(resolve, 20))

  const troisieme = await relayerMultipart(requeteMultipart(), "http://backend.test/x", {}, options)
  assert.equal(troisieme.type, "refus")
  if (troisieme.type === "refus") {
    assert.equal(troisieme.statut, 429)
    assert.match(troisieme.detail, /simultan/i)
  }
  assert.equal(relaisEnCoursPour("session-1"), 2, "deux envois occupent la session")

  // Une AUTRE session garde son droit d'envoi (la limite est par session).
  const fetchImmediat = (async (_url: string, init: RequestInit) => {
    await new Response(init.body as ReadableStream).arrayBuffer()
    return new Response("{}", { status: 200 })
  }) as unknown as typeof fetch
  const autre = await relayerMultipart(requeteMultipart(), "http://backend.test/x", {}, {
    fetchImpl: fetchImmediat,
    cleConcurrence: "session-2",
    simultanesMax: 2,
    delaiInactiviteMs: 0,
  })
  assert.equal(autre.type, "reponse")

  assert.equal(liberateurs.length, 2)
  for (const liberer of liberateurs) liberer()
  await premier
  await deuxieme
  assert.equal(relaisEnCoursPour("session-1"), 0, "le compteur doit être relâché même en cas de succès")
})

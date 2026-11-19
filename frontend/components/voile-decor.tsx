"use client"

/**
 * Voile décorative WebGL (Phase 4 — « L'atelier prend le large »).
 *
 * Maillage procédural d'une voile triangulaire, ondulée au vent par un
 * vertex shader, avec tissage et plis dans le fragment shader. Réaction
 * douce à la souris. Purement décorative (aria-hidden, pointer-events none).
 *
 * Garde-fous :
 * - WebGL brut, 0 dépendance (Three.js seul dépasserait le budget 200 ko gzip).
 * - Lazy : le canvas ne démarre que dans le viewport (IntersectionObserver).
 * - Fallback SVG (plan de voile en traits fins) si WebGL indisponible,
 *   mobile, ou prefers-reduced-motion.
 * - DPR plafonné à 1.5, pause hors viewport, aucun modèle externe.
 */

import { useEffect, useRef, useState } from "react"

const VERT = `
attribute vec2 aPos;
attribute vec2 aUv;
uniform float uTemps;
uniform vec2 uSouris;
uniform float uForce;
varying vec2 vUv;
varying float vPlis;
void main() {
  vUv = aUv;
  vec3 p = vec3(aPos, 0.0);
  // Ondulation au vent : deux ondes croisées, amplitude croissante vers la chute.
  float amp = mix(0.06, 0.34, aUv.x);
  float onde = sin(aUv.x * 7.0 + uTemps * 1.15) * 0.6
             + sin(aUv.y * 5.0 - uTemps * 0.8) * 0.4;
  p.z += onde * amp;
  // Plis verticaux (lattes) : crêtes douces.
  p.z += sin(aUv.x * 22.0 + uTemps * 0.5) * 0.018 * aUv.x;
  // Réaction souris : la voile s'ouvre doucement vers le pointeur.
  float d = distance(aUv, uSouris);
  p.z += smoothstep(0.55, 0.0, d) * uForce * 0.22;
  p.xy += (aUv - uSouris) * smoothstep(0.55, 0.0, d) * uForce * 0.05;
  vPlis = onde * amp;
  gl_Position = vec4(p.xy * vec2(1.55, 1.9) + vec2(-0.12, -0.05), 0.0, 1.0);
}
`

const FRAG = `
precision mediump float;
uniform vec3 uCouleur;
uniform vec3 uFond;
uniform float uTemps;
varying vec2 vUv;
varying float vPlis;
void main() {
  // Tissage : trame fine croisée, très subtile.
  float trame = sin(vUv.x * 260.0) * sin(vUv.y * 260.0) * 0.035;
  // Laizes : bandes verticales (panneaux de toile cousus).
  float laize = step(0.985, fract(vUv.x * 5.0)) * 0.10;
  // Ombre des plis : le creux assombrit, la crête éclaircit.
  float lumiere = 0.82 + vPlis * 0.9;
  vec3 col = uCouleur * lumiere + trame - laize;
  // Bordure fine (guindant/chute) : fondu vers le fond en bordure.
  float bord = smoothstep(0.0, 0.04, vUv.x) * smoothstep(1.0, 0.96, vUv.x)
             * smoothstep(0.0, 0.03, vUv.y) * smoothstep(1.0, 0.97, vUv.y);
  col = mix(uFond, col, bord);
  gl_FragColor = vec4(col, 1.0);
}
`

function compiler(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader)
    return null
  }
  return shader
}

function lireCouleur(variable: string, repli: string) {
  if (typeof window === "undefined") return repli
  const valeur = getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
  return valeur || repli
}

function hexVersRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "")
  if (h.length !== 6) return [0.5, 0.4, 0.2]
  return [
    parseInt(h.slice(0, 2), 16) / 255,
    parseInt(h.slice(2, 4), 16) / 255,
    parseInt(h.slice(4, 6), 16) / 255,
  ]
}

/** Plan de voile en traits fins — fallback 2D (SVG pur, currentColor). */
export function VoilePlan({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 200 240" fill="none" className={className} aria-hidden="true">
      {/* Guindant (bord d'attaque) */}
      <path d="M40 20 L40 220" stroke="currentColor" strokeWidth="1.2" opacity="0.7" />
      {/* Chute (bord de fuite) */}
      <path d="M40 20 Q 150 90 165 200" stroke="currentColor" strokeWidth="1.2" opacity="0.7" />
      {/* Bordure */}
      <path d="M40 220 Q 110 215 165 200" stroke="currentColor" strokeWidth="1.2" opacity="0.7" />
      {/* Lattes */}
      <path d="M40 70 Q 95 78 118 82" stroke="currentColor" strokeWidth="0.8" opacity="0.45" />
      <path d="M40 120 Q 110 128 138 138" stroke="currentColor" strokeWidth="0.8" opacity="0.45" />
      <path d="M40 170 Q 125 178 155 182" stroke="currentColor" strokeWidth="0.8" opacity="0.45" />
      {/* Point d'écoute */}
      <circle cx="165" cy="200" r="2.5" fill="currentColor" opacity="0.8" />
      {/* Tête */}
      <circle cx="40" cy="20" r="2.5" fill="currentColor" opacity="0.8" />
    </svg>
  )
}

export function VoileDecor({ className, force = 1 }: { className?: string; force?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [fallback, setFallback] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    // Fallback immédiat : mobile, reduced-motion, ou pas de WebGL.
    const mobile = window.matchMedia("(max-width: 1023px)").matches
    const reduit = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const gl = canvas.getContext("webgl", { alpha: false, antialias: true })
    if (mobile || reduit || !gl) {
      setFallback(true)
      return
    }

    const vs = compiler(gl, gl.VERTEX_SHADER, VERT)
    const fs = compiler(gl, gl.FRAGMENT_SHADER, FRAG)
    if (!vs || !fs) {
      setFallback(true)
      return
    }
    const programme = gl.createProgram()
    if (!programme) {
      setFallback(true)
      return
    }
    gl.attachShader(programme, vs)
    gl.attachShader(programme, fs)
    gl.linkProgram(programme)
    if (!gl.getProgramParameter(programme, gl.LINK_STATUS)) {
      setFallback(true)
      return
    }
    gl.useProgram(programme)

    // Maillage procédural : voile triangulaire (guindant vertical, chute courbe).
    const SEG_X = 36
    const SEG_Y = 28
    const positions: number[] = []
    const uvs: number[] = []
    const indices: number[] = []
    for (let j = 0; j <= SEG_Y; j++) {
      for (let i = 0; i <= SEG_X; i++) {
        const u = i / SEG_X
        const v = j / SEG_Y
        // Guindant à x=0, chute qui s'évase vers le bas (forme triangulaire).
        const largeur = 0.25 + 0.75 * v
        positions.push(-0.5 + u * largeur, 0.5 - v)
        uvs.push(u, 1 - v)
      }
    }
    for (let j = 0; j < SEG_Y; j++) {
      for (let i = 0; i < SEG_X; i++) {
        const a = j * (SEG_X + 1) + i
        const b = a + 1
        const c = a + SEG_X + 1
        const d = c + 1
        indices.push(a, b, c, b, d, c)
      }
    }
    const tamponPos = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, tamponPos)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(positions), gl.STATIC_DRAW)
    const tamponUv = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, tamponUv)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(uvs), gl.STATIC_DRAW)
    const tamponIdx = gl.createBuffer()
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, tamponIdx)
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(indices), gl.STATIC_DRAW)

    const locPos = gl.getAttribLocation(programme, "aPos")
    const locUv = gl.getAttribLocation(programme, "aUv")
    gl.enableVertexAttribArray(locPos)
    gl.bindBuffer(gl.ARRAY_BUFFER, tamponPos)
    gl.vertexAttribPointer(locPos, 2, gl.FLOAT, false, 0, 0)
    gl.enableVertexAttribArray(locUv)
    gl.bindBuffer(gl.ARRAY_BUFFER, tamponUv)
    gl.vertexAttribPointer(locUv, 2, gl.FLOAT, false, 0, 0)

    const locTemps = gl.getUniformLocation(programme, "uTemps")
    const locSouris = gl.getUniformLocation(programme, "uSouris")
    const locForce = gl.getUniformLocation(programme, "uForce")
    const locCouleur = gl.getUniformLocation(programme, "uCouleur")
    const locFond = gl.getUniformLocation(programme, "uFond")

    const [cr, cg, cb] = hexVersRgb(lireCouleur("--primary", "#8f5c07"))
    const [fr, fg, fb] = hexVersRgb(lireCouleur("--background", "#f6f1e6"))
    gl.uniform3f(locCouleur, cr, cg, cb)
    gl.uniform3f(locFond, fr, fg, fb)
    gl.uniform1f(locForce, force)

    let souris: [number, number] = [0.5, 0.5]
    let raf = 0
    let visible = true
    let demarre = performance.now()

    const redimensionner = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5)
      const rect = canvas.getBoundingClientRect()
      canvas.width = Math.max(1, Math.floor(rect.width * dpr))
      canvas.height = Math.max(1, Math.floor(rect.height * dpr))
      gl.viewport(0, 0, canvas.width, canvas.height)
    }
    redimensionner()
    window.addEventListener("resize", redimensionner)

    const surSouris = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      souris = [
        Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
        Math.min(1, Math.max(0, 1 - (e.clientY - rect.top) / rect.height)),
      ]
    }
    window.addEventListener("mousemove", surSouris)

    const observateur = new IntersectionObserver(
      ([entree]) => {
        visible = entree.isIntersecting
        if (visible) demarre = performance.now() - (temps % 100000)
      },
      { threshold: 0.05 },
    )
    observateur.observe(canvas)

    let temps = 0
    const boucle = (maintenant: number) => {
      raf = requestAnimationFrame(boucle)
      if (!visible) return
      temps = (maintenant - demarre) / 1000
      gl.uniform1f(locTemps, temps)
      gl.uniform2f(locSouris, souris[0], souris[1])
      gl.drawElements(gl.TRIANGLES, indices.length, gl.UNSIGNED_SHORT, 0)
    }
    raf = requestAnimationFrame(boucle)

    return () => {
      cancelAnimationFrame(raf)
      observateur.disconnect()
      window.removeEventListener("resize", redimensionner)
      window.removeEventListener("mousemove", surSouris)
      gl.getExtension("WEBGL_lose_context")?.loseContext()
    }
  }, [force])

  if (fallback) {
    return <VoilePlan className={className} />
  }
  return <canvas ref={canvasRef} className={className} aria-hidden="true" />
}

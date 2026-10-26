// Rose des vents — emblème de la voilerie (Lot 8). SVG pur, currentColor :
// elle prend la couleur du contexte (laiton/ambre selon le quart) et tourne
// très lentement quand on lui donne la classe `rose-lente`.

export function RoseDesVents({ className }: { className?: string }) {
  // 8 pointes longues (cardinaux + intercardinaux) et 8 courtes, construites
  // en étoile : chaque pointe est un losange effilé depuis le centre.
  const pointes = (longueur: number, largeur: number, decalage: number) =>
    Array.from({ length: 8 }, (_, i) => {
      const angle = ((i * 45 + decalage) * Math.PI) / 180
      const x = 50 + longueur * Math.cos(angle)
      const y = 50 + longueur * Math.sin(angle)
      const px = 50 + largeur * Math.cos(angle + Math.PI / 2)
      const py = 50 + largeur * Math.sin(angle + Math.PI / 2)
      const qx = 50 + largeur * Math.cos(angle - Math.PI / 2)
      const qy = 50 + largeur * Math.sin(angle - Math.PI / 2)
      return `M50 50 L${px.toFixed(1)} ${py.toFixed(1)} L${x.toFixed(1)} ${y.toFixed(1)} L${qx.toFixed(1)} ${qy.toFixed(1)} Z`
    }).join(" ")

  return (
    <svg viewBox="0 0 100 100" fill="none" className={className} aria-hidden="true">
      <circle cx="50" cy="50" r="47" stroke="currentColor" strokeWidth="0.8" opacity="0.55" />
      <circle cx="50" cy="50" r="36" stroke="currentColor" strokeWidth="0.5" opacity="0.35" />
      <path d={pointes(26, 3.4, 22.5)} fill="currentColor" opacity="0.45" />
      <path d={pointes(44, 4.6, 0)} fill="currentColor" opacity="0.85" />
      <circle cx="50" cy="50" r="4.5" fill="currentColor" />
      {/* Le nord, marqué d'une flèche d'insigne */}
      <path d="M50 1.5 L53 8 L47 8 Z" fill="currentColor" />
    </svg>
  )
}

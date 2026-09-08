"use client"

import { useEffect, useState } from "react"
import type { ImportCorrection, ImportData } from "@/lib/types"

interface Props {
  data: ImportData
  saving: boolean
  onSave: (correction: ImportCorrection) => void
}

function num(value: string): number | undefined {
  if (value.trim() === "") return undefined
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
}

export function CorrectionForm({ data, saving, onSave }: Props) {
  const dims = data.dimensions ?? {}
  const [reference, setReference] = useState(data.reference ?? "")
  const [material, setMaterial] = useState(data.material ?? "")
  const [quantity, setQuantity] = useState(data.quantity != null ? String(data.quantity) : "")
  const [description, setDescription] = useState(data.description ?? "")
  const [length, setLength] = useState(dims.length != null ? String(dims.length) : "")
  const [width, setWidth] = useState(dims.width != null ? String(dims.width) : "")
  const [height, setHeight] = useState(dims.height != null ? String(dims.height) : "")
  const [unit, setUnit] = useState(dims.unit ?? "mm")

  // Refresh the form whenever a new import result (or a saved correction) arrives.
  useEffect(() => {
    const d = data.dimensions ?? {}
    setReference(data.reference ?? "")
    setMaterial(data.material ?? "")
    setQuantity(data.quantity != null ? String(data.quantity) : "")
    setDescription(data.description ?? "")
    setLength(d.length != null ? String(d.length) : "")
    setWidth(d.width != null ? String(d.width) : "")
    setHeight(d.height != null ? String(d.height) : "")
    setUnit(d.unit ?? "mm")
  }, [data])

  function save() {
    const correction: ImportCorrection = {
      reference: reference.trim() || undefined,
      material: material.trim() || undefined,
      quantity: num(quantity),
      description: description.trim() || undefined,
      dimensions: {
        length: num(length),
        width: num(width),
        height: num(height),
        unit: unit || undefined,
      },
    }
    if (
      correction.dimensions &&
      correction.dimensions.length === undefined &&
      correction.dimensions.width === undefined &&
      correction.dimensions.height === undefined
    ) {
      delete correction.dimensions
    }
    onSave(correction)
  }

  const inputCls =
    "min-h-9 w-full rounded-md border border-input bg-background px-2.5 text-sm outline-none ring-primary focus:ring-2"

  return (
    <div className="mt-4 rounded-md border border-border bg-card/60 p-4">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h3 className="text-sm font-semibold">Manual correction</h3>
        <span className="text-xs text-muted-foreground">
          Saving re-validates, regenerates both reports and re-uploads
        </span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="text-xs text-muted-foreground">
          Reference
          <input className={inputCls} value={reference} onChange={(e) => setReference(e.target.value)} />
        </label>
        <label className="text-xs text-muted-foreground">
          Material
          <input className={inputCls} value={material} onChange={(e) => setMaterial(e.target.value)} />
        </label>
        <label className="text-xs text-muted-foreground">
          Quantity
          <input
            className={inputCls}
            inputMode="numeric"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
        </label>
        <label className="text-xs text-muted-foreground">
          Unit
          <select className={inputCls} value={unit} onChange={(e) => setUnit(e.target.value)}>
            <option value="mm">mm</option>
            <option value="cm">cm</option>
            <option value="m">m</option>
          </select>
        </label>
        <label className="text-xs text-muted-foreground">
          Length
          <input className={inputCls} inputMode="decimal" value={length} onChange={(e) => setLength(e.target.value)} />
        </label>
        <label className="text-xs text-muted-foreground">
          Width
          <input className={inputCls} inputMode="decimal" value={width} onChange={(e) => setWidth(e.target.value)} />
        </label>
        <label className="text-xs text-muted-foreground">
          Height (optional)
          <input className={inputCls} inputMode="decimal" value={height} onChange={(e) => setHeight(e.target.value)} />
        </label>
        <label className="text-xs text-muted-foreground sm:col-span-2">
          Description
          <input className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <button
          className="min-h-9 rounded-md bg-primary px-5 text-sm font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
          disabled={saving}
          onClick={save}
        >
          {saving ? "Saving…" : "Save correction"}
        </button>
      </div>
    </div>
  )
}

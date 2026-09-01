"use client"

import { useCallback, useRef, useState } from "react"
import type { SearchResponse, SearchResult } from "@/lib/types"

const LIMIT = 25

interface State {
  query: string
  results: SearchResult[]
  hasMore: boolean
  loading: boolean
  loadingMore: boolean
  searched: boolean
  error: string | null
}

const initial: State = {
  query: "",
  results: [],
  hasMore: false,
  loading: false,
  loadingMore: false,
  searched: false,
  error: null,
}

async function fetchSearch(q: string, offset: number): Promise<SearchResponse> {
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=${LIMIT}&offset=${offset}`)
  const body = await res.json()
  if (!res.ok) throw new Error(body?.detail ?? "Search request failed.")
  return body as SearchResponse
}

export function useSearch() {
  const [state, setState] = useState<State>(initial)
  const reqId = useRef(0)

  const run = useCallback(async (q: string) => {
    const query = q.trim()
    if (!query) {
      setState(initial)
      return
    }
    const id = ++reqId.current
    setState((s) => ({ ...s, query, loading: true, error: null, searched: true }))
    try {
      const data = await fetchSearch(query, 0)
      if (id !== reqId.current) return
      setState((s) => ({
        ...s,
        query,
        results: data.results,
        hasMore: data.has_more,
        loading: false,
        searched: true,
        error: null,
      }))
    } catch (e) {
      if (id !== reqId.current) return
      setState((s) => ({ ...s, loading: false, error: (e as Error).message, results: [], hasMore: false }))
    }
  }, [])

  const loadMore = useCallback(async () => {
    setState((cur) => {
      if (cur.loadingMore || !cur.hasMore) return cur
      const offset = cur.results.length
      const query = cur.query
      const id = ++reqId.current
      fetchSearch(query, offset)
        .then((data) => {
          if (id !== reqId.current) return
          setState((s) => ({
            ...s,
            results: [...s.results, ...data.results],
            hasMore: data.has_more,
            loadingMore: false,
          }))
        })
        .catch((e) => {
          if (id !== reqId.current) return
          setState((s) => ({ ...s, loadingMore: false, error: (e as Error).message }))
        })
      return { ...cur, loadingMore: true }
    })
  }, [])

  return { ...state, run, loadMore }
}

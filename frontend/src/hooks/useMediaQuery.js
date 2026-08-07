import { useEffect, useState } from 'react'

// Live `window.matchMedia` result for `query`.
//
// The first value is read SYNCHRONOUSLY in the initialiser, not in an effect.
// An effect-only version paints the desktop tree once and then swaps, which on a
// phone is a visible flash of exactly the wide table this hook exists to avoid.
export function useMediaQuery(query) {
  const read = () => typeof window !== 'undefined' && !!window.matchMedia?.(query).matches
  const [matches, setMatches] = useState(read)

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mql = window.matchMedia(query)
    // Re-read on mount: `query` may have changed, and the viewport may have been
    // resized between the initialiser running and the effect firing.
    setMatches(mql.matches)
    const onChange = (e) => setMatches(e.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])

  return matches
}

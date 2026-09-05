import type { Star, Title } from '~/types/api'

/**
 * Display filter for a star's title list.
 *
 * Hides titles that don't belong in a star's solo showcase:
 * - VR works (【VR】/[VR] in the title, or a VR resolution tag like [8KVR])
 * - Multi-star (共演/omnibus) works
 *
 * Rules were validated against the full titles table (2026-08-29):
 * 63 VR + 8 multi-star hits out of 1948, with 1 known false positive
 * (a "Series: Name Name, Who ..." English title matching the cast-list
 * pattern). Recall is intentionally conservative — hiding a legit solo
 * work is worse than letting an unlabeled omnibus through.
 */

/** Max titles shown per star section. */
export const MAX_TITLES_PER_STAR = 21

const VR_TITLE_RE = /(【VR】|\[VR\])/i
const MULTI_KEYWORD_RE = /共演|オムニバス/

/** Latin cast list: two or more comma-separated "First Last" names. */
const LATIN_NAME = '[A-Z][a-z]+ [A-Z][a-z]+'
const COLON_CAST_RE = new RegExp(`[:：]\\s*(?:${LATIN_NAME},\\s*)+${LATIN_NAME}`)
const WHOLE_CAST_RE = new RegExp(`^(?:${LATIN_NAME},\\s*){2,}${LATIN_NAME}\\.?\\s*$`)
/** Japanese cast run: 3+ kanji names separated by 、or space. */
const JP_CAST_RE = /(?:[一-龥]{2,4}[、\s]){2,}[一-龥]{2,4}/

export function isVrTitle(title: Title): boolean {
  if (title.title && VR_TITLE_RE.test(title.title)) return true
  return !!title.resolution && /vr/i.test(title.resolution)
}

export function isMultiStarTitle(title: Title, ownStarCode: string, roster: Star[]): boolean {
  const text = (title.title ?? '').trim()
  if (!text) return false
  if (MULTI_KEYWORD_RE.test(text)) return true

  const lower = text.toLowerCase()
  for (const star of roster) {
    if (star.code === ownStarCode) continue
    for (const name of [star.name, star.jp]) {
      // Skip very short aliases — substring matching them is noise-prone
      if (name && name.trim().length >= 3 && lower.includes(name.trim().toLowerCase())) {
        return true
      }
    }
  }

  return COLON_CAST_RE.test(text) || WHOLE_CAST_RE.test(text) || JP_CAST_RE.test(text)
}

/** Filter VR/multi-star works and cap the list for display. */
export function filterDisplayTitles(star: Star, roster: Star[]): Title[] {
  return (star.titles ?? [])
    .filter(t => !isVrTitle(t) && !isMultiStarTitle(t, star.code, roster))
    .slice(0, MAX_TITLES_PER_STAR)
}

/** Sort key for a DD/MM/YYYY date string; missing/unparseable -> oldest. */
export function titleDateKey(dateStr?: string): string {
  const parts = dateStr?.split('/')
  if (parts?.length === 3) {
    return `${parts[2]}${parts[1].padStart(2, '0')}${parts[0].padStart(2, '0')}`
  }
  return '00000000'
}

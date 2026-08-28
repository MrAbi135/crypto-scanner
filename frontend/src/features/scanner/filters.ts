// §9's filter grammar, from the chips (Blueprint §21.4).
//
// **Filters are the server's.** §9 says a filter the server did not apply "is
// a lie the client believes", and it refuses an unknown one with a 422 rather
// than ignoring it. Narrowing the rows here after they arrived would also
// break the one number the quiet-feed state depends on: `live_total` is the
// unfiltered denominator, and it is only honest because the server does the
// filtering and still reports what it did not send.

export type FilterField = 'grade' | 'direction' | 'timeframe' | 'archetype'

/** Selected values per field. Empty or absent means "no filter on this one". */
export type Selection = Partial<Record<FilterField, readonly string[]>>

/**
 * The chips, in the order they are shown.
 *
 * Values are the doctrine's own words -- §9.4's grades, §8.6's archetypes,
 * §3's directions. §9 requires the API's filter vocabulary to be SLS's, and a
 * UI that invented friendlier labels would either send the wrong value or need
 * a translation table nobody maintains. The *label* may differ from the value
 * where the doctrine's word is not a word (`UP` reads as Long); the value
 * never does.
 */
export const CHIPS: readonly {
  readonly field: FilterField
  readonly legend: string
  readonly options: readonly { readonly value: string; readonly label: string }[]
}[] = [
  {
    field: 'grade',
    legend: 'Grade',
    // §9.4. There is no C: below the lowest floor a setup is not published.
    options: [
      { value: 'S', label: 'S' },
      { value: 'A', label: 'A' },
      { value: 'B', label: 'B' },
    ],
  },
  {
    field: 'direction',
    legend: 'Direction',
    options: [
      { value: 'UP', label: 'Long' },
      { value: 'DOWN', label: 'Short' },
    ],
  },
  {
    field: 'timeframe',
    legend: 'Timeframe',
    options: [
      { value: 'M5', label: 'M5' },
      { value: 'M15', label: 'M15' },
      { value: 'H1', label: 'H1' },
      { value: 'H4', label: 'H4' },
    ],
  },
  {
    field: 'archetype',
    legend: 'Archetype',
    options: [
      { value: 'A1', label: 'A1 sweep reversal' },
      { value: 'A2', label: 'A2 breaker retest' },
      { value: 'A3', label: 'A3 continuation' },
      { value: 'A4', label: 'A4 FVG continuation' },
      { value: 'A5', label: 'A5 range play' },
    ],
  },
]

/**
 * Turn a selection into §9's query parameters.
 *
 * One value uses `filter[field]=v` and several use `filter[field][in]=a,b`.
 * Both are grammar the endpoint declares; sending `[in]` for a single value
 * would work too, and does not, because a request should say what it means and
 * `[in]` with one member reads as a list that happens to be short.
 *
 * Fields AND together and values within a field OR -- which is what the
 * endpoint does, and is stated here because it is the thing a reader of the
 * chips will assume and should be right about.
 */
export function toQuery(selection: Selection): Record<string, string> {
  const params: Record<string, string> = {}

  for (const { field } of CHIPS) {
    const values = selection[field]

    if (values === undefined || values.length === 0) continue

    if (values.length === 1) {
      params[`filter[${field}]`] = values[0] as string
    } else {
      params[`filter[${field}][in]`] = values.join(',')
    }
  }

  return params
}

/** Add or remove one value, leaving the other fields alone. */
export function toggle(
  selection: Selection,
  field: FilterField,
  value: string,
): Selection {
  const current = selection[field] ?? []
  const next = current.includes(value)
    ? current.filter((v) => v !== value)
    : [...current, value]

  // The key is dropped rather than left as an empty array, so `isFiltered`
  // and `toQuery` both read "no filter" from the same absence. Left behind,
  // an empty array makes `isFiltered` true with nothing selected -- and the
  // quiet feed would then blame a filter that is switched off.
  if (next.length === 0) {
    const rest = { ...selection }

    delete rest[field]

    return rest
  }

  return { ...selection, [field]: next }
}

export function isFiltered(selection: Selection): boolean {
  return CHIPS.some(({ field }) => (selection[field] ?? []).length > 0)
}

/** A sentence naming what is on, for the empty state to state (§21.19). */
export function describe(selection: Selection): string {
  const parts = CHIPS.filter(({ field }) => (selection[field] ?? []).length > 0).map(
    ({ field, legend }) => `${legend.toLowerCase()} ${(selection[field] ?? []).join(' or ')}`,
  )

  return parts.join(', ')
}

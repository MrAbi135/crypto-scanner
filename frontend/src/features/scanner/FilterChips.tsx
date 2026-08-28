// Blueprint §21.4's filter chips.
//
// Toggle buttons in a labelled group per field, not a row of styled divs: a
// chip is a control, and one that only a mouse can reach and a screen reader
// cannot name is decoration that happens to change the results.

import { CHIPS, type FilterField, type Selection } from '@features/scanner/filters'

export interface FilterChipsProps {
  readonly selection: Selection
  readonly onToggle: (field: FilterField, value: string) => void
  readonly onClear: () => void
  readonly busy: boolean
}

export function FilterChips({ selection, onToggle, onClear, busy }: FilterChipsProps) {
  const anyOn = CHIPS.some(({ field }) => (selection[field] ?? []).length > 0)

  return (
    <div className="chips" data-testid="filter-chips">
      {CHIPS.map(({ field, legend, options }) => (
        // A fieldset per field, so a screen reader announces "Grade, S,
        // pressed" rather than a loose "S" with no idea what it grades.
        <fieldset key={field} className="chips__group">
          <legend className="chips__legend">{legend}</legend>

          {options.map(({ value, label }) => {
            const on = (selection[field] ?? []).includes(value)

            return (
              <button
                key={value}
                type="button"
                className={`chip${on ? ' chip--on' : ''}`}
                // `aria-pressed` and not a checkbox role: these are toggles
                // that re-run a query, and the pressed state is what a reader
                // needs to know before they can read the board.
                aria-pressed={on}
                data-testid={`chip-${field}-${value}`}
                disabled={busy}
                onClick={() => onToggle(field, value)}
              >
                {label}
              </button>
            )
          })}
        </fieldset>
      ))}

      {/* Only when there is something to clear. A permanently visible control
          that does nothing most of the time teaches people to ignore it. */}
      {anyOn && (
        <button type="button" className="chips__clear" onClick={onClear} disabled={busy}>
          Clear all
        </button>
      )}
    </div>
  )
}

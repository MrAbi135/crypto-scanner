// Blueprint §16.3's zone-state scale, as the chart's side of a contract.
//
// The states are the server's, from three enums that overlap in confusing ways
// (`ZoneState`, `FvgState`, `IfvgState` in `domain/ict/model.py`). Two words
// mean "untouched" -- `FRESH` for order blocks, `OPEN` for fair-value gaps --
// and §8.3.1's scoring table was once written in only one of them, so every FVG
// scored zero for spelling it differently. This list carries both.
//
// **The point of the list is the state that is not on it.** Before §16.3 was
// implemented, `data-state` was rendered and nothing read it, so a MITIGATED
// zone was drawn exactly like a FRESH one. The fix would reintroduce that the
// day the server grows a sixth state: the new value would match no rule and
// inherit the base `.zone` wash, which is the FRESH treatment. So an unknown
// state gets a loud outline of its own and is counted on the chart's label.

export const TREATED_STATES: readonly string[] = [
  // Untouched.
  'FRESH',
  'OPEN',
  // Entered and held.
  'TESTED',
  'TOUCHED',
  // Spent, still readable.
  'MITIGATED',
  'CE_FILLED',
  // Gone against.
  'INVALIDATED',
  'INVERTED',
  // Over.
  'EXPIRED',
  'DEAD',
  'FILLED',
  // Not yet proven (inverse FVG).
  'UNPROVEN',
]

export function isTreated(state: string): boolean {
  return TREATED_STATES.includes(state)
}

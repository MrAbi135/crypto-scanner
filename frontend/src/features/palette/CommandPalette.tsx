// Blueprint C18 / §21.12: everything is a few keystrokes and Enter away.
//
// Two of §21.12's five groups are built. `NOT_REACHED` in `commands.ts` names
// the other three and why, and this renders that list -- because a palette
// that silently reaches two of five teaches a reader that the missing three do
// not exist.

import { useCallback, useEffect, useRef, useState } from 'react'

import { fetchUniverse } from '@services/api/client'
import { type Command, NOT_REACHED, screenCommands, search, symbolCommands } from '@features/palette/commands'

import './palette.css'

export interface CommandPaletteProps {
  readonly views: readonly { readonly id: string; readonly label: string }[]
  readonly onScreen: (id: string) => void
  readonly onSymbol: (symbol: string) => void
}

export function CommandPalette({ views, onScreen, onSymbol }: CommandPaletteProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [at, setAt] = useState(0)
  const [symbols, setSymbols] = useState<Command[] | null>(null)
  const [symbolError, setSymbolError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const returnTo = useRef<Element | null>(null)

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        // Remembered before the dialog takes it. Returning focus to the body
        // would drop a keyboard reader at the top of the page every time they
        // closed the palette.
        returnTo.current = document.activeElement
        setOpen((was) => !was)
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Symbols are fetched on first open, not on mount. The palette is chrome on
  // every screen; paying for a universe request on every page load to populate
  // a list most sessions never open is a cost with no reader attached.
  useEffect(() => {
    if (!open || symbols !== null) return

    let cancelled = false

    void fetchUniverse()
      .then((response) => {
        if (!cancelled) setSymbols(symbolCommands(response.data))
      })
      .catch(() => {
        // Not silent. A palette that quietly lists no symbols is
        // indistinguishable from a platform that has none -- the exact
        // confusion the universe screen was built to end.
        if (!cancelled) {
          setSymbols([])
          setSymbolError('Symbols could not be loaded.')
        }
      })

    return () => {
      cancelled = true
    }
  }, [open, symbols])

  useEffect(() => {
    if (open) input.current?.focus()
  }, [open])

  const close = useCallback(() => {
    setOpen(false)
    setQuery('')
    setAt(0)

    const previous = returnTo.current as HTMLElement | null

    previous?.focus?.()
  }, [])

  if (!open) return null

  const all = [...screenCommands(views), ...(symbols ?? [])]
  const results = search(all, query)
  const active = results[Math.min(at, Math.max(results.length - 1, 0))]

  function run(command: Command | undefined) {
    if (command === undefined) return

    const split = command.id.indexOf(':')
    const kind = command.id.slice(0, split)
    const value = command.id.slice(split + 1)

    close()

    if (kind === 'screen') onScreen(value)
    if (kind === 'symbol') onSymbol(value)
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      setAt((was) => (results.length === 0 ? 0 : (was + 1) % results.length))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setAt((was) => (results.length === 0 ? 0 : (was - 1 + results.length) % results.length))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      run(active)
    }
  }

  return (
    <div className="palette__scrim" data-testid="palette">
      <div className="palette" role="dialog" aria-modal="true" aria-label="Search">
        <input
          ref={input}
          className="palette__input"
          type="text"
          value={query}
          aria-label="Search symbols and screens"
          // The listbox is a sibling, so the input keeps the keyboard and the
          // active option is announced through aria-activedescendant. Moving
          // real focus into the list would take it off the text field and stop
          // typing working mid-navigation.
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-results"
          aria-activedescendant={active === undefined ? undefined : `palette-${active.id}`}
          onChange={(event) => {
            setQuery(event.target.value)
            setAt(0)
          }}
          onKeyDown={onKeyDown}
        />

        {symbolError !== null && (
          <p className="palette__note" role="alert" data-testid="palette-symbol-error">
            {symbolError}
          </p>
        )}

        {results.length === 0 ? (
          <p className="palette__note" data-testid="palette-empty">
            Nothing matches that. A symbol missing here is a symbol not in the universe — the
            Universe screen says which status it is in.
          </p>
        ) : (
          <ul className="palette__results" id="palette-results" role="listbox" aria-label="Results">
            {results.map((command) => (
              <li
                key={command.id}
                id={`palette-${command.id}`}
                role="option"
                aria-selected={command.id === active?.id}
                className={`palette__row${command.id === active?.id ? ' palette__row--on' : ''}`}
                data-testid={`palette-${command.id}`}
                onClick={() => run(command)}
              >
                <span className="palette__group">{command.group}</span>
                <span className="palette__label">{command.label}</span>
                <span className="palette__detail">{command.detail}</span>
              </li>
            ))}
          </ul>
        )}

        {/* §21.12 lists five groups and two are built. Naming the other three
            costs a line and stops the palette from teaching that they do not
            exist. */}
        <p className="palette__note palette__gap" data-testid="palette-not-reached">
          Not searchable yet: {NOT_REACHED.join('; ')}.
        </p>
      </div>
    </div>
  )
}

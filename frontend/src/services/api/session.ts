// The access token, for as long as the tab is open (API Spec §18.1).
//
// **In memory, deliberately.** A token in `localStorage` is readable by any
// script that gets onto the page and survives the tab, which is the shape of
// every stolen-session incident worth naming. The refresh cookie the server
// sets is `HttpOnly` and is what carries a session across a reload -- so the
// cost of holding this here is one refresh call after F5, and the benefit is
// that an XSS cannot read it out of storage at leisure.
//
// A module-level value rather than React state because the API client is not a
// component and must not become one to reach it.

export interface Session {
  readonly accessToken: string
  readonly userId: string
  readonly tenantId: string
  /** Epoch milliseconds. */
  readonly expiresAt: number
}

let current: Session | null = null

const listeners = new Set<(session: Session | null) => void>()

export function currentSession(): Session | null {
  return current
}

export function setSession(session: Session | null): void {
  current = session

  for (const listener of listeners) listener(session)
}

/** Subscribe to sign-in and sign-out. Returns the unsubscribe. */
export function onSessionChange(listener: (session: Session | null) => void): () => void {
  listeners.add(listener)

  return () => {
    listeners.delete(listener)
  }
}

/**
 * Is the token past its life, with a margin?
 *
 * The margin exists because a token that expires between the check and the
 * server reading it produces a 401 the user did not do anything to earn. Thirty
 * seconds is longer than any request here takes and shorter than the token's
 * own life by a wide margin.
 */
export function isExpired(session: Session, now: number): boolean {
  return session.expiresAt - 30_000 <= now
}

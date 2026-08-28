import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SignIn } from './SignIn'
import { logout } from '@services/api/client'
import { currentSession, setSession } from '@services/api/session'

function stubLogin(response: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Promise.resolve(
        new Response(JSON.stringify(response), {
          status,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    ),
  )
}

const TOKEN = {
  access_token: 'abc',
  token_type: 'Bearer',
  expires_in: 900,
  user_id: 'u1',
  tenant_id: 't1',
}

async function fillAndSubmit() {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'a@b.c' } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret' } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
}

beforeEach(() => {
  setSession(null)
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('SignIn', () => {
  it('stores the session and tells the screen', async () => {
    stubLogin(TOKEN)

    const onSignedIn = vi.fn()

    render(<SignIn onSignedIn={onSignedIn} />)

    await fillAndSubmit()

    await waitFor(() => expect(onSignedIn).toHaveBeenCalledOnce())

    expect(currentSession()?.accessToken).toBe('abc')
  })

  it('turns the token lifetime into an instant, once', async () => {
    // Keeping `expires_in` as a duration and recomputing later would restart
    // the countdown on every read, and the session would never expire.
    stubLogin(TOKEN)

    const before = Date.now()

    render(<SignIn onSignedIn={vi.fn()} />)

    await fillAndSubmit()

    await waitFor(() => expect(currentSession()).not.toBeNull())

    const expiresAt = currentSession()?.expiresAt ?? 0

    expect(expiresAt).toBeGreaterThanOrEqual(before + 900_000)
    expect(expiresAt).toBeLessThan(before + 900_000 + 5_000)
  })

  it('sends the credentials as a body, never as a query string', async () => {
    // A password in a URL lands in access logs, browser history and any proxy
    // in between.
    stubLogin(TOKEN)

    render(<SignIn onSignedIn={vi.fn()} />)

    await fillAndSubmit()

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())

    const [url, init] = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock
      .calls[0] as [string, RequestInit]

    expect(url).not.toContain('secret')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ email: 'a@b.c', password: 'secret' })
  })

  it('shows the server’s own refusal, with its correlation id', async () => {
    // §18.1 answers one code for wrong password, unknown address, disabled and
    // deleted, deliberately. Inventing a more specific message here would undo
    // that on the client.
    stubLogin(
      { error: { code: 'AUTH_REQUIRED', message: 'Invalid credentials.', correlation_id: 'cid-1' } },
      401,
    )

    const onSignedIn = vi.fn()

    render(<SignIn onSignedIn={onSignedIn} />)

    await fillAndSubmit()

    const alert = await screen.findByRole('alert')

    expect(alert.textContent).toContain('Invalid credentials.')
    expect(alert.textContent).toContain('cid-1')
    expect(onSignedIn).not.toHaveBeenCalled()
    expect(currentSession()).toBeNull()
  })

  it('does not leave the button disabled after a refusal', async () => {
    // A failed sign-in that locks its own form is indistinguishable from a
    // broken page, and the user has no way to try the other password.
    stubLogin({ error: { code: 'AUTH_REQUIRED', message: 'no', correlation_id: 'c' } }, 401)

    render(<SignIn onSignedIn={vi.fn()} />)

    await fillAndSubmit()

    await screen.findByRole('alert')

    expect((screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('clears the session even when the logout call fails', async () => {
    // A logout that leaves the token in memory because the network was down is
    // the failure that matters: the user believes they signed out and did not.
    setSession({ accessToken: 'abc', userId: 'u1', tenantId: 't1', expiresAt: Date.now() + 1e6 })

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Promise.reject(new Error('offline'))),
    )

    await expect(logout()).rejects.toThrow('offline')

    expect(currentSession()).toBeNull()
  })
})

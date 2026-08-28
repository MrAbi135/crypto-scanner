// Sign-in for the S13a screen (API Spec §18.1).
//
// The smallest thing that makes the chart usable. Not a routed auth flow, not
// a password reset, not a remembered session -- those are S13. This exists
// because the read rows require a bearer token and the screen had none, so
// every overlay it drew was a 401 the developer could not do anything about.

import { useState, type FormEvent } from 'react'

import { ApiRequestError, login } from '@services/api/client'

export interface SignInProps {
  readonly onSignedIn: () => void
}

export function SignIn({ onSignedIn }: SignInProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()

    setBusy(true)
    setError(null)

    try {
      await login(email, password)
      onSignedIn()
    } catch (cause) {
      // The correlation id travels with the message, as everywhere else: it is
      // the only thing that makes a reported failure findable in the logs.
      setError(
        cause instanceof ApiRequestError
          ? `${cause.message} (${cause.correlationId})`
          : 'Sign-in failed.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="signin" onSubmit={submit} data-testid="signin">
      <h2 className="signin__title">Sign in</h2>

      <label className="signin__field">
        Email
        <input
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="username"
          required
        />
      </label>

      <label className="signin__field">
        Password
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
        />
      </label>

      <button type="submit" disabled={busy}>
        {busy ? 'Signing in…' : 'Sign in'}
      </button>

      {error !== null && (
        // `alert`, not a quiet line: the form is the only thing on screen and
        // a failure that does not announce itself reads as a dead button.
        <p role="alert" className="signin__error" data-testid="signin-error">
          {error}
        </p>
      )}
    </form>
  )
}

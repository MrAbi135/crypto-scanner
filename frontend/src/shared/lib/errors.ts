// AppError base + API error-envelope parser contract (S0.3 §4; API §7).
// The full parser lands with the generated client (S11); the class shape and
// correlation_id carrying are fixed now.

export class AppError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly correlationId?: string,
  ) {
    super(message)
    this.name = 'AppError'
  }
}

export interface ApiErrorEnvelope {
  readonly code: string
  readonly message: string
  readonly correlation_id?: string
}

export function fromEnvelope(envelope: ApiErrorEnvelope): AppError {
  return new AppError(envelope.code, envelope.message, envelope.correlation_id)
}

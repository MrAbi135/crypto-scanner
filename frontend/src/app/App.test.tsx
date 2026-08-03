import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { App } from './App'

describe('App shell', () => {
  it('mounts and shows the product name', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: /Institutional AI Crypto Scanner/i })).toBeDefined()
  })
})

// Test environment setup.
//
// Testing Library unmounts a render only if something registers cleanup. It
// does that automatically when a global `afterEach` exists, which it does not
// here -- the suite imports `describe`/`it` explicitly rather than running with
// `globals: true`. Registering it by hand is the smaller change and says what
// it does.
//
// Without this, renders accumulate in one jsdom document and queries start
// matching elements left behind by earlier tests: usually a loud "found
// multiple elements", occasionally a quiet pass against the wrong node.
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(cleanup)

// Flat ESLint config (S0.1 §4/§19/§22, extended in S0.2 §4).
// Encodes the feature-isolation boundary law and react-hooks rules before the
// first real component (S13): features never cross-import; shared imports
// nothing above it.
import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import boundaries from 'eslint-plugin-boundaries'
import reactHooks from 'eslint-plugin-react-hooks'

export default tseslint.config(
  { ignores: ['dist', '.vite', 'coverage'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,

  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { boundaries, 'react-hooks': reactHooks },
    settings: {
      'boundaries/elements': [
        { type: 'app', pattern: 'src/app/**', partialMatch: false },
        { type: 'features', pattern: 'src/features/**', partialMatch: false },
        { type: 'services', pattern: 'src/services/**', partialMatch: false },
        { type: 'entities', pattern: 'src/entities/**', partialMatch: false },
        { type: 'shared', pattern: 'src/shared/**', partialMatch: false },
      ],
      'boundaries/files': [
        { category: 'test', pattern: '**/*.test.{ts,tsx}' },
      ],
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../../*'],
              message: 'Use path aliases (@shared, @features, …), not deep relative imports.',
            },
          ],
        },
      ],

      'boundaries/no-unknown-dependencies': 'error',

      'boundaries/dependencies': [
        'error',
        {
          default: 'disallow',
          policies: [
            {
              from: { element: { type: 'app' } },
              allow: {
                to: {
                  element: {
                    types: {
                      anyOf: ['features', 'services', 'entities', 'shared', 'app'],
                    },
                  },
                },
              },
            },
            {
              from: { element: { type: 'features' } },
              allow: {
                to: {
                  element: {
                    types: {
                      anyOf: ['services', 'entities', 'shared', 'features'],
                    },
                  },
                },
              },
            },
            {
              from: { element: { type: 'services' } },
              allow: {
                to: {
                  element: {
                    types: {
                      anyOf: ['entities', 'shared', 'services'],
                    },
                  },
                },
              },
            },
            {
              from: { element: { type: 'entities' } },
              allow: {
                to: {
                  element: {
                    types: {
                      anyOf: ['shared', 'entities'],
                    },
                  },
                },
              },
            },
            {
              from: { element: { type: 'shared' } },
              allow: {
                to: { element: { type: 'shared' } },
              },
            },
          ],
        },
      ],
    },
  },

  {
    files: ['src/**/*.test.{ts,tsx}'],
    rules: {
      'boundaries/no-unknown-dependencies': 'off',
    },
  },
)

import { describe, expect, test } from 'vitest'

import { buildWebUiUrl } from './web-ui-url'

describe('Web UI credential isolation', () => {
  test.each([
    'https://yacd.metacubex.one/?secret=%secret',
    'https://metacubex.github.io/metacubexd/#/setup?secret=%secret',
    'http://127.0.0.1:9097/ui/?secret=%secret',
    'https://dashboard.example/?secret=saved-controller-secret',
    'https://dashboard.example/#/setup?%73ecret=saved-controller-secret',
  ])('rejects saved secret templates: %s', (template) => {
    expect(() => buildWebUiUrl(template, '127.0.0.1:9097')).toThrow(
      'Controller secrets are never inserted',
    )
  })

  test('still resolves non-secret controller placeholders', () => {
    expect(
      buildWebUiUrl(
        'https://dashboard.example/#/?host=%host&port=%port',
        '[::1]:9097',
      ),
    ).toBe('https://dashboard.example/#/?host=127.0.0.1&port=9097')
  })

  test.each([
    'javascript:alert(1)',
    'https://user:password@dashboard.example/',
  ])('rejects unsafe URL %s', (template) => {
    expect(() => buildWebUiUrl(template)).toThrow()
  })
})

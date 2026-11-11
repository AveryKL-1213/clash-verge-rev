import { afterEach, expect, test, vi } from 'vitest'

const check = vi.hoisted(() => vi.fn(async () => null))
vi.mock('@tauri-apps/plugin-updater', () => ({ check }))

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
  check.mockClear()
})

test('local builds reject update checks before contacting the updater', async () => {
  vi.stubEnv('VITE_LOCAL_BUILD', 'true')
  const { checkUpdateSafe } = await import('./update')
  await expect(checkUpdateSafe()).rejects.toThrow(
    'Downloaded binary updates are disabled',
  )
  expect(check).not.toHaveBeenCalled()
})

test('upstream builds retain the updater path', async () => {
  vi.stubEnv('VITE_LOCAL_BUILD', 'false')
  const { checkUpdateSafe } = await import('./update')
  await expect(checkUpdateSafe()).resolves.toBeNull()
  expect(check).toHaveBeenCalledOnce()
})

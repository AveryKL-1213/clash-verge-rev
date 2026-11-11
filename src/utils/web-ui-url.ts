export const WEB_UI_CREDENTIAL_NOTICE =
  'Controller secrets are never inserted into Web UI URLs. Remove secret parameters and %secret from saved links. Use a trusted local dashboard for controller access.'

export function buildWebUiUrl(template: string, server?: string): string {
  if (/%secret/i.test(template)) {
    throw new Error(WEB_UI_CREDENTIAL_NOTICE)
  }

  let value = template.trim().replaceAll('%host', '127.0.0.1')
  if (value.includes('%port')) {
    if (!server) throw new Error('Controller address is unavailable')
    const controller = new URL(`http://${server}`)
    if (!controller.port) throw new Error('Controller port is unavailable')
    value = value.replaceAll('%port', controller.port)
  }

  const url = new URL(value)
  if (/(?:[?&#])secret=/i.test(decodeURIComponent(url.toString()))) {
    throw new Error(WEB_UI_CREDENTIAL_NOTICE)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('Only HTTP and HTTPS Web UI URLs are allowed')
  }
  if (url.username || url.password) {
    throw new Error('Web UI URLs must not contain credentials')
  }
  return url.toString()
}

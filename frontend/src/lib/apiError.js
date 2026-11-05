// Plain-language text for a failed server call. Network failures and server
// crashes read the same to an engineer: the server could not do it, try again.
// The raw error still goes to the console for diagnostics.
export function friendlyError(err) {
  console.warn('DemandIQ request failed:', err)
  const msg = String(err?.message ?? err ?? '')
  if (err instanceof TypeError || /^HTTP 5\d\d/.test(msg) || /failed to fetch|networkerror|load failed/i.test(msg)) {
    return "Couldn't reach the server — try again. If it keeps happening, use Export diagnostics (bottom left) and send the file to support."
  }
  if (/^HTTP \d+/.test(msg) || msg === '[object Object]' || msg === '') return 'The server could not process this request — try again.'
  return msg
}

// Shared security-cookie names and CSRF header.
//
// This module intentionally has no Node-only imports: it is imported by the
// server auth helpers, the Next proxy, and browser-side fetch helpers. Keeping
// the names in one tiny module prevents the proxy and the client wrapper from
// drifting apart.

export const SESSION_COOKIE = "seamtech_session"
export const CSRF_COOKIE = "seamtech_csrf"
export const CSRF_HEADER = "x-csrf-token"

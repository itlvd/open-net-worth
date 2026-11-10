import { useState, useEffect, useSyncExternalStore } from 'react'
import { RouterProvider } from '@tanstack/react-router'
import { Login } from './components/Login'
import { NoPasswordYet } from './components/NoPasswordYet'
import { useBrand } from './brand-context'
import { authFetch, setUnauthorizedHandler } from './api/client'
import { router } from './router'
import { useServiceWorkerUpdate } from './registerSW.tsx'
import { clearQueryCache } from './state/queryCache'
import { desktopBridge } from './platform/desktop'
import {
  startGatewayReauth,
  subscribeReauthing,
  isReauthing,
  setGatewayPortal,
} from './utils/gatewayReauth'
import { signOutOfGateway, type SignOutScope } from './utils/gatewaySignOut'
import { DesktopStartupSplash } from './components/DesktopStartupSplash'
import { BootUnreachable } from './components/BootUnreachable'

interface AuthStatus {
  required: boolean
  authenticated: boolean
  /** `gateway`: a trusted proxy owns sign-in (ONW_GATEWAY_TOKEN). `demo`: a public demo that
   * needs no password (DEMO_OPEN_ACCESS). Either way the SPA has no login of its own. */
  mode?: 'gateway' | 'demo'
  /** A wallet is linked, so the login screen offers "Sign in with wallet". */
  wallet_login?: boolean
}

// ── Optimistic auth (2026-07-27) ─────────────────────────────────────────────
// `auth` used to start at null on every mount, which renders the blocking splash
// until GET /api/auth/status returns — the whole app gated behind one round trip.
//
// That is barely visible on a warm desktop tab, but it is THE reopen experience on
// iOS: a standalone PWA ("display":"standalone" in the manifest) is torn down rather
// than suspended when backgrounded, so every reopen is a cold start — fresh JS
// context, fresh React tree, `auth === null`, splash. Stacked on cold JS parse, a
// service-worker wakeup and remote network latency, reopening the app read as "it
// reloads and hangs every time".
//
// iOS discarding the page is not preventable from web APIs. What IS preventable is
// blocking the first paint on the network: remember the last known status, seed state
// from it, render immediately, and re-verify in the background.
//
// This is NOT a security boundary — it only decides which UI renders first. Every API
// call is still authorized server-side, and a stale "authenticated" resolves itself on
// the first 401 via the unauthorizedHandler wired below.
// The key is versioned: a visitor who bounced off the demo's old password screen has a cached
// "logged out" under the previous key, which would paint Login for a moment on their return.
const AUTH_CACHE_KEY = 'onw-auth-status-v2'

const knownMode = (m: unknown): AuthStatus['mode'] =>
  m === 'gateway' || m === 'demo' ? m : undefined

// Boot reads give up after this, so a hung server shows a retry screen instead of a blank one.
const BOOT_TIMEOUT_MS = 15_000

function readCachedAuth(): AuthStatus | null {
  try {
    const raw = localStorage.getItem(AUTH_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<AuthStatus>
    // Shape-check rather than trusting the blob: a corrupt/hand-edited value must fall
    // back to the splash, never crash the shell before it can render anything.
    if (typeof parsed?.required !== 'boolean' || typeof parsed?.authenticated !== 'boolean') {
      return null
    }
    return {
      required: parsed.required,
      authenticated: parsed.authenticated,
      ...(knownMode(parsed.mode) && { mode: knownMode(parsed.mode) }),
    }
  } catch {
    return null // private mode / storage disabled → previous blocking behaviour
  }
}

function writeCachedAuth(status: AuthStatus | null): void {
  try {
    if (status) localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify(status))
    else localStorage.removeItem(AUTH_CACHE_KEY)
  } catch {
    /* storage unavailable — the app works, it just pays the splash next reopen */
  }
}

export default function App() {
  const [auth, setAuth] = useState<AuthStatus | null>(readCachedAuth)
  const [unreachable, setUnreachable] = useState(false)
  const brand = useBrand()
  const reauthing = useSyncExternalStore(subscribeReauthing, isReauthing)
  useServiceWorkerUpdate()

  const applyAuth = (status: AuthStatus) => {
    setAuth(status)
    writeCachedAuth(status)
  }

  // Resolves with what the server said (null when it could not be asked), so a caller that
  // stays on screen — "I've set it, sign in" — can tell nothing changed.
  const checkAuthStatus = (): Promise<AuthStatus | null> =>
    authFetch('/api/auth/status', { signal: AbortSignal.timeout(BOOT_TIMEOUT_MS) })
      .then((r) => r.json())
      .then((s: AuthStatus & { gateway?: { portal_url?: string | null } }) => {
        setUnreachable(false)
        setGatewayPortal(s.gateway?.portal_url)
        const next: AuthStatus = {
          required: !!s.required,
          authenticated: !!s.authenticated,
          ...(knownMode(s.mode) && { mode: knownMode(s.mode) }),
          ...(s.wallet_login === true && { wallet_login: true }),
        }
        applyAuth(next)
        return next
      })
      // If the status route itself is unreachable, assume open (dormant/dev) so
      // we never hard-lock the user out of a local instance. Deliberately NOT
      // persisted, and it never downgrades an existing value: a transient network
      // failure on a protected instance must not cache "open" and flash the app on
      // the next reopen. A server that never ANSWERS (timeout) is different: nothing
      // behind it would work either, so say so and offer a retry.
      .catch((err) => {
        if ((err as Error)?.name === 'TimeoutError') setUnreachable(true)
        else setAuth((prev) => prev ?? { required: false, authenticated: true })
        return null
      })

  useEffect(() => {
    setUnauthorizedHandler((reauth) => {
      // A `reauth` URL only ever comes from the gateway; the cached status is written on every
      // applyAuth, so it is the current mode. In gateway mode the app never has a logged-out
      // state — the proxy is asked again instead, after the person's input is saved.
      if (readCachedAuth()?.mode === 'gateway') {
        startGatewayReauth(reauth)
        return
      }
      clearQueryCache()
      applyAuth({ required: true, authenticated: false })
    })
    checkAuthStatus()
    return () => setUnauthorizedHandler(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Auth gate. With a cached status this is skipped entirely and the app renders
  // straight away; the splash now only appears on a genuine first visit (or when
  // storage is unavailable), which is the one time there is nothing better to show.
  if (reauthing) {
    return (
      <div className="auth-splash" role="status">
        <div className="auth-splash-brand">{brand.app_name.toUpperCase()}</div>
        <div className="auth-splash-spinner" />
        <div className="auth-splash-note">Signing you back in…</div>
      </div>
    )
  }
  if (auth === null) {
    if (unreachable) {
      return (
        <BootUnreachable
          onRetry={() => {
            setUnreachable(false)
            void checkAuthStatus()
          }}
        />
      )
    }
    if (desktopBridge()) return <DesktopStartupSplash />
    return (
      <div className="auth-splash">
        <div className="auth-splash-brand">{brand.app_name.toUpperCase()}</div>
        <div className="auth-splash-spinner" />
      </div>
    )
  }
  if (auth.required && !auth.authenticated) {
    return <Login onAuthenticated={checkAuthStatus} walletLogin={auth.wallet_login === true} />
  }
  if (!auth.required && !auth.authenticated) {
    return <NoPasswordYet onRetry={checkAuthStatus} />
  }

  return (
    <RouterProvider
      router={router}
      context={{
        auth,
        onLogout: async (scope: SignOutScope = 'app') => {
          if (auth.mode === 'gateway') {
            clearQueryCache()
            signOutOfGateway(scope)
            return
          }
          try {
            await authFetch('/api/auth/logout', { method: 'POST' })
          } catch {
            /* best-effort — show login regardless */
          }
          clearQueryCache()
          // applyAuth (not setAuth): persist the logged-out status so the next reopen
          // shows Login immediately instead of optimistically flashing the app.
          applyAuth({ required: true, authenticated: false })
        },
        onAuthChanged: checkAuthStatus,
      }}
    />
  )
}

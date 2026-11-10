// Settings → Security → Wallet sign-in: link one Ethereum wallet (MetaMask, Rabby, …) that can sign
// in beside the password. Linking and unlinking need the current password, exactly as changing the
// password does; removing the password unlinks the wallet too.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { tooManyAttemptsMessage } from '../../../../api/rateLimit'
import { shortAddress, type SignedChallenge } from '../../../../api/wallet'
import { PasswordField } from '../../../ui/PasswordField'
import { WalletButton } from '../../../WalletButton'
import { Btn, Field, Panel } from '../ds'

export interface WalletState {
  password_set: boolean
  current_password_required: boolean
  wallet_address?: string
}

const WRONG_CURRENT_DETAIL = 'current password incorrect'

export function WalletPanel({
  state,
  refresh,
  toast,
}: {
  state: WalletState | undefined
  refresh: () => Promise<unknown> | void
  toast: (message: string) => void
}) {
  const [oldPw, setOldPw] = useState('')
  const [busy, setBusy] = useState(false)
  if (!state?.password_set) return null
  const linked = state.wallet_address || ''
  const needsCurrent = state.current_password_required

  async function post(path: string, body: Record<string, unknown>, done: string) {
    setBusy(true)
    try {
      const res = await authFetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...body,
          old_password: needsCurrent ? oldPw || undefined : undefined,
        }),
      })
      if (!res.ok) {
        if (res.status === 429) return toast(tooManyAttemptsMessage(res))
        const detail = (await res.json().catch(() => null))?.detail
        if (res.status === 403 && detail === WRONG_CURRENT_DETAIL) {
          return toast('Current password is wrong')
        }
        return toast(typeof detail === 'string' ? detail : `Could not save (HTTP ${res.status})`)
      }
      setOldPw('')
      void refresh()
      toast(done)
    } catch {
      toast('Could not reach the server. Try again.')
    } finally {
      setBusy(false)
    }
  }

  function requireCurrent(): boolean {
    if (needsCurrent && !oldPw) {
      toast('Enter your current password first')
      return false
    }
    return true
  }

  async function link({ nonce, signature }: SignedChallenge) {
    await post('/api/auth/wallet/link', { nonce, signature }, 'Wallet linked')
  }

  return (
    <Panel
      title="Wallet sign-in"
      sub="Sign in with an Ethereum wallet such as MetaMask or Rabby, beside your password. Signing costs no gas and sends no transaction."
    >
      <div className="rd-formgrid">
        <Field label="Linked wallet">
          <code title={linked || undefined}>{linked ? shortAddress(linked) : 'None'}</code>
        </Field>
        {needsCurrent && (
          <Field label="Confirm with your current password">
            <PasswordField
              className="rd-input"
              autoComplete="current-password"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
            />
          </Field>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <WalletButton
          purpose="link"
          label={linked ? 'Link a different wallet' : 'Link wallet'}
          disabled={busy || (needsCurrent && !oldPw)}
          onSigned={link}
          onError={toast}
        />
        {linked && (
          <Btn
            disabled={busy}
            onClick={() => {
              if (requireCurrent()) void post('/api/auth/wallet/unlink', {}, 'Wallet unlinked')
            }}
          >
            Unlink wallet
          </Btn>
        )}
      </div>
    </Panel>
  )
}

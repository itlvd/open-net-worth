// One button for "use a browser wallet": finds the installed wallets (EIP-6963), lets the user pick
// when there is more than one (MetaMask and Rabby side by side), and hands back a signed challenge.
import { useState } from 'react'
import {
  discoverWallets,
  isUserRejection,
  signChallenge,
  type Purpose,
  type SignedChallenge,
  type WalletOption,
} from '../api/wallet'

export function WalletButton({
  purpose,
  label,
  className,
  disabled,
  onSigned,
  onError,
}: {
  purpose: Purpose
  label: string
  className?: string
  disabled?: boolean
  onSigned: (signed: SignedChallenge) => Promise<void> | void
  onError: (message: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [choices, setChoices] = useState<WalletOption[] | null>(null)

  async function signWith(wallet: WalletOption) {
    setChoices(null)
    setBusy(true)
    try {
      await onSigned(await signChallenge(wallet, purpose))
    } catch (err) {
      if (!isUserRejection(err)) {
        onError(err instanceof Error ? err.message : 'The wallet could not sign the request')
      }
    } finally {
      setBusy(false)
    }
  }

  async function start() {
    setBusy(true)
    const wallets = await discoverWallets()
    setBusy(false)
    if (wallets.length === 0) {
      onError('No browser wallet found. Install MetaMask, Rabby or another Ethereum wallet.')
      return
    }
    if (wallets.length === 1) {
      await signWith(wallets[0]!)
      return
    }
    setChoices(wallets)
  }

  return (
    <div className="wallet-button">
      <button
        type="button"
        className={className ?? 'btn-secondary'}
        disabled={disabled || busy}
        onClick={() => void start()}
      >
        {busy ? 'Waiting for your wallet…' : label}
      </button>
      {choices && (
        <ul className="wallet-choices" aria-label="Choose a wallet">
          {choices.map((w) => (
            <li key={w.id}>
              <button type="button" className="wallet-choice" onClick={() => void signWith(w)}>
                {w.icon && <img src={w.icon} alt="" width={20} height={20} />}
                <span>{w.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

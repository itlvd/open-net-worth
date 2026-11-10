// Browser wallets (MetaMask, Rabby, Coinbase Wallet, …) for Sign-In with Ethereum.
//
// Discovery is EIP-6963: every installed wallet announces itself, so a browser with both MetaMask
// and Rabby lets the user pick instead of whichever won the race for `window.ethereum`. A wallet
// too old for 6963 still works through the legacy `window.ethereum` (EIP-1193).
//
// The server writes the message; this module only asks the wallet to sign it, verbatim.
import { authFetch } from './client'

export interface Eip1193Provider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>
}

export interface WalletOption {
  id: string
  name: string
  icon?: string
  provider: Eip1193Provider
}

interface AnnounceDetail {
  info: { uuid: string; name: string; icon?: string; rdns?: string }
  provider: Eip1193Provider
}

const DISCOVERY_MS = 300

export async function discoverWallets(): Promise<WalletOption[]> {
  const found = new Map<string, WalletOption>()
  const onAnnounce = (event: Event) => {
    const detail = (event as CustomEvent<AnnounceDetail>).detail
    if (!detail?.info || !detail.provider) return
    const id = detail.info.rdns || detail.info.uuid
    found.set(id, { id, name: detail.info.name, icon: detail.info.icon, provider: detail.provider })
  }
  window.addEventListener('eip6963:announceProvider', onAnnounce)
  window.dispatchEvent(new Event('eip6963:requestProvider'))
  await new Promise((resolve) => setTimeout(resolve, DISCOVERY_MS))
  window.removeEventListener('eip6963:announceProvider', onAnnounce)
  const legacy = (window as unknown as { ethereum?: Eip1193Provider }).ethereum
  if (found.size === 0 && legacy) {
    found.set('injected', { id: 'injected', name: 'Browser wallet', provider: legacy })
  }
  return [...found.values()]
}

export type Purpose = 'login' | 'link'

export interface SignedChallenge {
  nonce: string
  signature: string
  address: string
}

// The user rejecting the request in the wallet (EIP-1193 code 4001) is not an error to report.
export function isUserRejection(err: unknown): boolean {
  return typeof err === 'object' && err !== null && (err as { code?: unknown }).code === 4001
}

async function serverChallenge(address: string, chainId: number, purpose: Purpose) {
  const res = await authFetch('/api/auth/wallet/challenge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ address, chain_id: chainId, purpose }),
  })
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail
    throw new Error(
      typeof detail === 'string' ? detail : `Could not start wallet sign-in (HTTP ${res.status})`,
    )
  }
  return (await res.json()) as { nonce: string; message: string }
}

// Connect, fetch the server's EIP-4361 message for this address, and have the wallet sign it.
export async function signChallenge(
  wallet: WalletOption,
  purpose: Purpose,
): Promise<SignedChallenge> {
  const accounts = (await wallet.provider.request({ method: 'eth_requestAccounts' })) as string[]
  const address = accounts?.[0]
  if (!address) throw new Error('The wallet did not share an account')
  const chainHex = (await wallet.provider.request({ method: 'eth_chainId' })) as string
  const chainId = Number.parseInt(chainHex, 16) || 1
  const { nonce, message } = await serverChallenge(address, chainId, purpose)
  const signature = (await wallet.provider.request({
    method: 'personal_sign',
    params: [utf8Hex(message), address],
  })) as string
  return { nonce, signature, address }
}

// personal_sign takes the message as hex-encoded UTF-8; every wallet displays it decoded.
function utf8Hex(text: string): string {
  let hex = '0x'
  for (const byte of new TextEncoder().encode(text)) hex += byte.toString(16).padStart(2, '0')
  return hex
}

export function shortAddress(address: string): string {
  return address.length > 12 ? `${address.slice(0, 6)}…${address.slice(-4)}` : address
}

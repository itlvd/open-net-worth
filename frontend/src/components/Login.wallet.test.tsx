// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Login } from './Login'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../api/client', () => ({ authFetch }))
vi.mock('../brand-context', () => ({ useBrand: () => ({ app_name: 'Open Net Worth' }) }))

const ADDRESS = '0x00000000000000000000000000000000000000a1'

function json(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body, headers: new Headers() }
}

function announce(name: string, request: (args: { method: string }) => Promise<unknown>) {
  const handler = () =>
    window.dispatchEvent(
      new CustomEvent('eip6963:announceProvider', {
        detail: { info: { uuid: name, name, rdns: `io.${name}` }, provider: { request } },
      }),
    )
  window.addEventListener('eip6963:requestProvider', handler)
  return () => window.removeEventListener('eip6963:requestProvider', handler)
}

const providerRequest = vi.fn(async ({ method }: { method: string }) => {
  if (method === 'eth_requestAccounts') return [ADDRESS]
  if (method === 'eth_chainId') return '0x1'
  if (method === 'personal_sign') return '0xsigned'
  throw new Error(method)
})

const cleanups: (() => void)[] = []
afterEach(() => {
  cleanup()
  authFetch.mockReset()
  providerRequest.mockClear()
  cleanups.splice(0).forEach((fn) => fn())
})

describe('Login — wallet sign-in', () => {
  it('is not offered when no wallet is linked', () => {
    render(<Login onAuthenticated={() => {}} />)
    expect(screen.queryByRole('button', { name: /sign in with wallet/i })).toBeNull()
  })

  it('signs the server message with the announced wallet and signs in', async () => {
    cleanups.push(announce('rabby', providerRequest))
    authFetch.mockImplementation(async (path: string) =>
      path === '/api/auth/wallet/challenge'
        ? json(200, { nonce: 'n1', message: 'host wants you to sign in' })
        : json(200, { ok: true }),
    )
    const onAuthenticated = vi.fn()
    render(<Login onAuthenticated={onAuthenticated} walletLogin />)
    fireEvent.click(screen.getByRole('button', { name: /sign in with wallet/i }))
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalled())

    const challenge = JSON.parse(authFetch.mock.calls[0]![1].body)
    expect(challenge).toEqual({ address: ADDRESS, chain_id: 1, purpose: 'login' })
    const signCall = providerRequest.mock.calls.find(([a]) => a.method === 'personal_sign')!
    const [hexMessage] = (signCall[0] as unknown as { params: string[] }).params
    expect(hexMessage).toBe('0x' + Buffer.from('host wants you to sign in').toString('hex'))
    expect(JSON.parse(authFetch.mock.calls[1]![1].body)).toEqual({
      nonce: 'n1',
      signature: '0xsigned',
    })
  })

  it('lets the user pick when several wallets are installed', async () => {
    cleanups.push(announce('MetaMask', providerRequest), announce('Rabby', providerRequest))
    render(<Login onAuthenticated={() => {}} walletLogin />)
    fireEvent.click(screen.getByRole('button', { name: /sign in with wallet/i }))
    expect(await screen.findByRole('button', { name: 'MetaMask' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Rabby' })).toBeTruthy()
  })

  it('says so when the wallet is not the linked one', async () => {
    cleanups.push(announce('rabby', providerRequest))
    authFetch.mockImplementation(async (path: string) =>
      path === '/api/auth/wallet/challenge'
        ? json(200, { nonce: 'n1', message: 'm' })
        : json(401, { detail: 'wallet sign-in failed' }),
    )
    render(<Login onAuthenticated={() => {}} walletLogin />)
    fireEvent.click(screen.getByRole('button', { name: /sign in with wallet/i }))
    expect(await screen.findByText("This wallet isn't linked to this instance")).toBeTruthy()
  })

  it('explains when no wallet is installed', async () => {
    render(<Login onAuthenticated={() => {}} walletLogin />)
    fireEvent.click(screen.getByRole('button', { name: /sign in with wallet/i }))
    expect(await screen.findByText(/no browser wallet found/i)).toBeTruthy()
  })
})

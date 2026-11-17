import { ReactNode, useState } from 'react'
import { errorMessage } from './api'

/** Success/error banners for a record page, and `act` to run a request that returns the updated record:
 * the record is reloaded and `success` shown, or the error is shown and re-thrown. */
export function useActions<T>(load: (record: T) => void, describe: (e: unknown) => ReactNode = errorMessage) {
  const [message, setMessage] = useState('')
  const [error, setError] = useState<ReactNode>('')
  const act = async (request: () => Promise<{ data: T }>, success: string) => {
    try {
      load((await request()).data)
      setMessage(success)
      setError('')
    } catch (e) {
      setError(describe(e))
      throw e
    }
  }
  return { message, setMessage, error, setError, act }
}

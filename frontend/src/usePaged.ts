import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage, Page } from './api'

/** Load a paginated list endpoint; refetches whenever page, page size or params change. */
export function usePaged<T>(url: string, params: Record<string, unknown> = {}, initialPageSize = 20) {
  const [data, setData] = useState<Page<T>>({ items: [], total: 0, page: 1, page_size: initialPageSize })
  const [page, setPage] = useState(0) // zero-based for MUI TablePagination
  const [pageSize, setPageSize] = useState(initialPageSize)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const paramsKey = JSON.stringify(params)

  const reload = useCallback(() => {
    setLoading(true)
    const cleanParams = Object.fromEntries(Object.entries(JSON.parse(paramsKey)).filter(([, v]) => v !== '' && v != null))
    api
      .get<Page<T>>(url, { params: { ...cleanParams, page: page + 1, page_size: pageSize } })
      .then((r) => {
        setData(r.data)
        setError('')
      })
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoading(false))
  }, [url, paramsKey, page, pageSize])

  useEffect(reload, [reload])
  useEffect(() => setPage(0), [paramsKey]) // new filters start from the first page

  const pagination = {
    count: data.total,
    page,
    rowsPerPage: pageSize,
    onPageChange: (_: unknown, p: number) => setPage(p),
    onRowsPerPageChange: (e: React.ChangeEvent<HTMLInputElement>) => {
      setPageSize(Number(e.target.value))
      setPage(0)
    },
  }
  return { data, loading, error, reload, pagination }
}

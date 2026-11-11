import { Alert, Box, Button, Typography } from '@mui/material'
import { useLockFn } from 'ahooks'
import type { Ref } from 'react'
import { useImperativeHandle, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { BaseDialog, BaseEmpty, DialogRef } from '@/components/base'
import { useClashInfo } from '@/hooks/use-clash'
import { useVerge } from '@/hooks/use-verge'
import { showNotice } from '@/services/notice-service'
import { openExternalUrl } from '@/utils/open-external-url'
import { buildWebUiUrl, WEB_UI_CREDENTIAL_NOTICE } from '@/utils/web-ui-url'

import { WebUIItem } from './web-ui-item'

const DEFAULT_WEB_UI_LIST = [
  'https://metacubex.github.io/metacubexd/#/setup?http=true&hostname=%host&port=%port',
  'https://yacd.metacubex.one/?hostname=%host&port=%port',
  'https://board.zash.run.place/#/setup?http=true&hostname=%host&port=%port',
]

export function WebUIViewer({ ref }: { ref?: Ref<DialogRef> }) {
  const { t } = useTranslation()

  const { clashInfo } = useClashInfo()
  const { verge, patchVerge, mutateVerge } = useVerge()

  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)

  useImperativeHandle(ref, () => ({
    open: () => setOpen(true),
    close: () => setOpen(false),
  }))

  const webUIList = verge?.web_ui_list || DEFAULT_WEB_UI_LIST

  const webUIEntries = useMemo(() => {
    const counts: Record<string, number> = {}
    return webUIList.map((item, index) => {
      const keyBase = item && item.trim().length > 0 ? item : 'entry'
      const count = counts[keyBase] ?? 0
      counts[keyBase] = count + 1
      return {
        item,
        index,
        key: `${keyBase}-${count}`,
      }
    })
  }, [webUIList])

  const handleAdd = useLockFn(async (value: string) => {
    const newList = [...webUIList, value]
    mutateVerge((old) => (old ? { ...old, web_ui_list: newList } : old), false)
    await patchVerge({ web_ui_list: newList })
  })

  const handleChange = useLockFn(async (index: number, value?: string) => {
    const newList = [...webUIList]
    newList[index] = value ?? ''
    mutateVerge((old) => (old ? { ...old, web_ui_list: newList } : old), false)
    await patchVerge({ web_ui_list: newList })
  })

  const handleDelete = useLockFn(async (index: number) => {
    const newList = [...webUIList]
    newList.splice(index, 1)
    mutateVerge((old) => (old ? { ...old, web_ui_list: newList } : old), false)
    await patchVerge({ web_ui_list: newList })
  })

  const handleOpenUrl = useLockFn(async (value?: string) => {
    if (!value) return
    try {
      const url = buildWebUiUrl(value, clashInfo?.server)
      await openExternalUrl(url)
    } catch (e: any) {
      showNotice.error('settings.modals.webUI.errors.openFailed', e)
    }
  })

  return (
    <BaseDialog
      open={open}
      title={
        <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
          {t('settings.modals.webUI.title')}
          <Button
            variant="contained"
            size="small"
            disabled={editing}
            onClick={() => setEditing(true)}
          >
            {t('shared.actions.new')}
          </Button>
        </Box>
      }
      contentSx={{
        width: 450,
        height: 300,
        pb: 1,
        overflowY: 'auto',
        userSelect: 'text',
      }}
      cancelBtn={t('shared.actions.close')}
      disableOk
      onClose={() => setOpen(false)}
      onCancel={() => setOpen(false)}
    >
      <Alert severity="info">{WEB_UI_CREDENTIAL_NOTICE}</Alert>
      {!editing && webUIList.length === 0 && (
        <BaseEmpty
          extra={
            <Typography sx={{ mt: 2, fontSize: '12px' }}>
              Add a trusted dashboard URL. Supported placeholders: %host, %port.
            </Typography>
          }
        />
      )}

      {webUIEntries.map(({ item, index, key }) => (
        <WebUIItem
          key={key}
          value={item}
          onChange={(v) => handleChange(index, v)}
          onDelete={() => handleDelete(index)}
          onOpenUrl={handleOpenUrl}
        />
      ))}
      {editing && (
        <WebUIItem
          value=""
          onlyEdit
          onChange={(v) => {
            setEditing(false)
            handleAdd(v || '')
          }}
          onCancel={() => setEditing(false)}
        />
      )}
    </BaseDialog>
  )
}

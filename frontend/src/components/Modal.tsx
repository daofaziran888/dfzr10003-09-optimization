import type { ReactNode } from 'react'

type ModalProps = {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  width?: number
}

/** 轻量弹窗：影响分析、影响清单、版本差异共用同一套交互壳。 */
export default function Modal({ open, title, onClose, children, footer, width = 880 }: ModalProps) {
  if (!open) {
    return null
  }
  return (
    <div className="modal-mask" role="presentation" onClick={onClose}>
      <div
        className="modal-panel"
        style={{ maxWidth: width }}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-head">
          <h3>{title}</h3>
          <button className="link" type="button" onClick={onClose}>关闭</button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  )
}

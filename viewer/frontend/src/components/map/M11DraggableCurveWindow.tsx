import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'

const WINDOW_MARGIN_PX = 12
const DESKTOP_PLACEMENT_WIDTH = 900
const MAX_DESKTOP_WIDTH_PX = 704
const DESKTOP_WIDTH_RATIO = 0.42
const ASPECT_RATIO_HEIGHT = 9 / 16

// Pinned NWM 4f8d982637f67956acb788b813006e12c1c93174
// apps/frontend/src/components/map/M11PopupChrome.tsx:16-17 (class string only).
const M11_POPUP_GLASS =
  'rounded-2xl border border-white/10 bg-slate-950/90 text-slate-100 shadow-[0_24px_64px_-16px_rgba(2,8,28,0.65)] ring-1 ring-white/10 backdrop-blur-2xl supports-[backdrop-filter]:bg-slate-950/75'

interface CurveWindowViewport {
  width: number
  height: number
}

interface CurveWindowSize {
  width: number
  height: number
}

interface CurveWindowPosition {
  x: number
  y: number
}

interface ActiveDrag {
  pointerId: number | null
  offsetX: number
  offsetY: number
  ownerWindow: Window
  onMove: (event: PointerEvent) => void
  onStop: (event?: PointerEvent) => void
}

function joinClassNames(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(' ')
}

export function M11DraggableCurveWindow({
  header,
  children,
  className,
}: {
  header: ReactNode
  children: ReactNode
  className?: string
}) {
  const frameRef = useRef<HTMLElement | null>(null)
  const positionRef = useRef<CurveWindowPosition | null>(null)
  const dragRef = useRef<ActiveDrag | null>(null)
  const handleMoveRef = useRef<(event: PointerEvent) => void>(() => {})
  const stopDragRef = useRef<(event?: PointerEvent) => void>(() => {})
  const [position, setPositionState] = useState<CurveWindowPosition | null>(null)
  const [dragging, setDragging] = useState(false)

  const setPosition = useCallback((next: CurveWindowPosition) => {
    positionRef.current = next
    setPositionState(next)
  }, [])

  const clampCurrentPosition = useCallback(() => {
    const frame = frameRef.current
    if (!frame) return
    setPosition(clampPosition(frame, positionRef.current ?? defaultPosition(frame)))
  }, [setPosition])

  useLayoutEffect(() => {
    const frame = frameRef.current
    if (!frame) return
    setPosition(clampPosition(frame, defaultPosition(frame)))
  }, [setPosition])

  useLayoutEffect(() => {
    const ownerWindow = frameRef.current?.ownerDocument.defaultView ?? window
    ownerWindow.addEventListener('resize', clampCurrentPosition)
    return () => ownerWindow.removeEventListener('resize', clampCurrentPosition)
  }, [clampCurrentPosition])

  const handleMove = useCallback(
    (event: PointerEvent) => {
      const frame = frameRef.current
      const drag = dragRef.current
      if (!frame || !drag) return
      const pointerId = getPointerId(event)
      if (drag.pointerId !== null && pointerId !== null && drag.pointerId !== pointerId) return
      setPosition(clampPosition(frame, { x: event.clientX - drag.offsetX, y: event.clientY - drag.offsetY }))
    },
    [setPosition],
  )

  const stopDrag = useCallback((event?: PointerEvent) => {
    const drag = dragRef.current
    if (!drag) return
    if (event) {
      const pointerId = getPointerId(event)
      if (drag.pointerId !== null && pointerId !== null && drag.pointerId !== pointerId) return
    }
    drag.ownerWindow.removeEventListener('pointermove', drag.onMove)
    drag.ownerWindow.removeEventListener('pointerup', drag.onStop)
    drag.ownerWindow.removeEventListener('pointercancel', drag.onStop)
    if (drag.pointerId !== null) {
      try {
        frameRef.current?.releasePointerCapture(drag.pointerId)
      } catch {
        // Pointer capture may already have been released.
      }
    }
    dragRef.current = null
    setDragging(false)
  }, [])

  handleMoveRef.current = handleMove
  stopDragRef.current = stopDrag

  useLayoutEffect(() => {
    return () => {
      const drag = dragRef.current
      if (!drag) return
      drag.ownerWindow.removeEventListener('pointermove', drag.onMove)
      drag.ownerWindow.removeEventListener('pointerup', drag.onStop)
      drag.ownerWindow.removeEventListener('pointercancel', drag.onStop)
      dragRef.current = null
    }
  }, [])

  const startDrag = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0 || shouldIgnoreDragTarget(event.target)) return
      if (dragRef.current) return
      const frame = frameRef.current
      if (!frame) return
      const current = positionRef.current ?? clampPosition(frame, defaultPosition(frame))
      setPosition(current)
      const ownerWindow = frame.ownerDocument.defaultView ?? window
      const pointerId = getPointerId(event.nativeEvent)
      const onMove = handleMoveRef.current
      const onStop = stopDragRef.current
      dragRef.current = {
        pointerId,
        offsetX: event.clientX - current.x,
        offsetY: event.clientY - current.y,
        ownerWindow,
        onMove,
        onStop,
      }
      if (pointerId !== null) {
        try {
          frame.setPointerCapture(pointerId)
        } catch {
          // Pointer capture is progressive enhancement for this interaction.
        }
      }
      ownerWindow.addEventListener('pointermove', onMove)
      ownerWindow.addEventListener('pointerup', onStop)
      ownerWindow.addEventListener('pointercancel', onStop)
      setDragging(true)
      event.preventDefault()
    },
    [setPosition],
  )

  const inlineStyle = position
    ? {
        left: `${position.x}px`,
        top: `${position.y}px`,
        zIndex: 142,
      }
    : {
        left: 0,
        top: 0,
        visibility: 'hidden' as const,
        zIndex: 142,
      }

  return (
    <aside
      ref={frameRef}
      className={joinClassNames(
        'absolute flex aspect-video w-[calc(100%_-_1.5rem)] max-h-[calc(100%_-_1.5rem)] flex-col overflow-hidden md:w-[min(44rem,42vw)]',
        M11_POPUP_GLASS,
        className,
      )}
      style={inlineStyle}
    >
      <div className="h-px shrink-0 bg-gradient-to-r from-transparent via-cyan-400/60 to-transparent" aria-hidden="true" />
      <div
        className={joinClassNames('shrink-0 touch-none select-none cursor-grab', dragging && 'cursor-grabbing')}
        onPointerDown={startDrag}
      >
        {header}
      </div>
      {children}
    </aside>
  )
}

function shouldIgnoreDragTarget(target: EventTarget | null) {
  if (!(target instanceof Element)) return false
  return Boolean(
    target.closest(
      [
        '[data-m11-window-no-drag]',
        'button',
        'a',
        'input',
        'textarea',
        'select',
        '[role="button"]',
        '[role="combobox"]',
        '[role="listbox"]',
        '[role="option"]',
        '[role="tab"]',
      ].join(','),
    ),
  )
}

function getPointerId(event: PointerEvent | MouseEvent) {
  return 'pointerId' in event && Number.isFinite(event.pointerId) ? event.pointerId : null
}

function defaultPosition(frame: HTMLElement): CurveWindowPosition {
  const viewport = curveWindowViewport(frame)
  const size = curveWindowSize(frame, viewport)
  const desktop = viewport.width >= DESKTOP_PLACEMENT_WIDTH
  const x = desktop
    ? viewport.width * 0.28 - size.width / 2
    : viewport.width / 2 - size.width / 2 - 18
  const y = desktop ? 88 : 64
  return { x, y }
}

function clampPosition(frame: HTMLElement, position: CurveWindowPosition): CurveWindowPosition {
  const viewport = curveWindowViewport(frame)
  const size = curveWindowSize(frame, viewport)
  const maxX = Math.max(WINDOW_MARGIN_PX, viewport.width - size.width - WINDOW_MARGIN_PX)
  const maxY = Math.max(WINDOW_MARGIN_PX, viewport.height - size.height - WINDOW_MARGIN_PX)
  return {
    x: Math.min(Math.max(position.x, WINDOW_MARGIN_PX), maxX),
    y: Math.min(Math.max(position.y, WINDOW_MARGIN_PX), maxY),
  }
}

function curveWindowViewport(frame: HTMLElement): CurveWindowViewport {
  const ownerWindow = frame.ownerDocument.defaultView ?? window
  const parentRect = frame.parentElement?.getBoundingClientRect()
  const width = parentRect && parentRect.width > 0 ? parentRect.width : ownerWindow.innerWidth
  const height = parentRect && parentRect.height > 0 ? parentRect.height : ownerWindow.innerHeight
  return {
    width: Math.max(width, WINDOW_MARGIN_PX * 2),
    height: Math.max(height, WINDOW_MARGIN_PX * 2),
  }
}

function curveWindowSize(frame: HTMLElement, viewport: CurveWindowViewport): CurveWindowSize {
  const rect = frame.getBoundingClientRect()
  if (rect.width > 0 && rect.height > 0) return { width: rect.width, height: rect.height }
  const availableWidth = Math.max(WINDOW_MARGIN_PX * 2, viewport.width - WINDOW_MARGIN_PX * 2)
  const width =
    viewport.width >= DESKTOP_PLACEMENT_WIDTH
      ? Math.min(MAX_DESKTOP_WIDTH_PX, viewport.width * DESKTOP_WIDTH_RATIO)
      : availableWidth
  const height = Math.min(
    width * ASPECT_RATIO_HEIGHT,
    Math.max(WINDOW_MARGIN_PX * 2, viewport.height - WINDOW_MARGIN_PX * 2),
  )
  return { width, height }
}

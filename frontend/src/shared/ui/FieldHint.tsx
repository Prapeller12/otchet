import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import "./field-hint.css";

/** A single field's help stays outside the scrolling table, without changing grid focus. */
export function useFieldHint(text: string | undefined) {
  const id = useId();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  const popup = useRef<HTMLDivElement>(null);
  const focused = useRef(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout>>();
  function cancelHide() { clearTimeout(hideTimer.current); }
  function hide() { cancelHide(); setAnchor(null); }
  function show(target: HTMLElement) {
    cancelHide();
    if (!text) return;
    document.dispatchEvent(new CustomEvent("report-field-hint-open", { detail: id }));
    setAnchor(target);
  }
  function leave() {
    cancelHide();
    if (!focused.current) hideTimer.current = setTimeout(() => setAnchor(null), 120);
  }
  useEffect(() => () => clearTimeout(hideTimer.current), []);
  useLayoutEffect(() => {
    if (!anchor || !popup.current) return;
    const field = anchor.getBoundingClientRect();
    const tip = popup.current.getBoundingClientRect();
    const margin = 8;
    const below = field.bottom + margin;
    const top = below + tip.height <= window.innerHeight - margin ? below : field.top - tip.height - margin;
    setPosition({
      left: Math.max(margin, Math.min(field.left, window.innerWidth - tip.width - margin)),
      top: Math.max(margin, Math.min(top, window.innerHeight - tip.height - margin)),
    });
    // An anchored hint must not drift over another row when its viewport moves.
    function scroll(event: Event) {
      if (event.target instanceof Node && popup.current?.contains(event.target)) return;
      setAnchor(null);
    }
    function escape(event: globalThis.KeyboardEvent) { if (event.key === "Escape") setAnchor(null); }
    function otherHint(event: Event) { if ((event as CustomEvent<string>).detail !== id) setAnchor(null); }
    window.addEventListener("resize", hide);
    document.addEventListener("scroll", scroll, true);
    document.addEventListener("keydown", escape);
    document.addEventListener("report-field-hint-open", otherHint);
    return () => {
      window.removeEventListener("resize", hide);
      document.removeEventListener("scroll", scroll, true);
      document.removeEventListener("keydown", escape);
      document.removeEventListener("report-field-hint-open", otherHint);
    };
  }, [anchor, text]);
  const open = !!text && !!anchor;
  return {
    hintProps: {
      "data-field-hint": text || undefined,
      "aria-describedby": open ? id : undefined,
      onMouseEnter: (event: { currentTarget: HTMLElement }) => show(event.currentTarget),
      onMouseLeave: leave,
      onFocus: (event: { currentTarget: HTMLElement }) => { focused.current = true; show(event.currentTarget); },
      onBlur: () => { focused.current = false; hide(); },
      onKeyDown: (event: KeyboardEvent<HTMLElement>) => { if (event.key === "Escape") hide(); },
    },
    hint: open ? createPortal(<div id={id} ref={popup} role="tooltip" className="field-hint-popup" style={position}
      onMouseEnter={cancelHide} onMouseLeave={leave}>{text}</div>, document.body) : null,
  };
}

/** Static values get keyboard help; editable grid cells use the hook on their existing button. */
export function HintValue({ hint, children, className = "" }: { hint: string; children: ReactNode; className?: string }) {
  const help = useFieldHint(hint);
  return <><span className={`field-hint-value ${className}`} tabIndex={0} {...help.hintProps}>{children}</span>{help.hint}</>;
}

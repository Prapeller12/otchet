import { useEffect, useRef } from "react";

/** Keep keyboard entry inside a modal and return focus to its launcher. */
export function useDialogFocus() {
  const ref = useRef<HTMLElement | null>(null);
  const launcher = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    if (!root.contains(document.activeElement)) root.querySelector<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex='0']")?.focus();
    function trap(event: KeyboardEvent) {
      if (event.key !== "Tab" || !root) return;
      const controls = Array.from(root.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex='0']"));
      const first = controls[0]; const last = controls[controls.length - 1];
      if (!first || !last) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    }
    root.addEventListener("keydown", trap);
    return () => { root.removeEventListener("keydown", trap); launcher.current?.focus(); };
  }, []);
  return ref;
}

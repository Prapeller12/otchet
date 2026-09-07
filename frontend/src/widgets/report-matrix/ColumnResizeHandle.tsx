import type { PointerEvent } from "react";

export function ColumnResizeHandle({ label, width, minimum = 48, onResize, onCommit }: {
  label: string; width: number; minimum?: number; onResize(width: number): void; onCommit(width: number): void;
}) {
  const clamp = (value: number) => Math.max(minimum, Math.min(600, Math.round(value)));
  function start(event: PointerEvent<HTMLSpanElement>) {
    event.preventDefault();
    event.stopPropagation();
    const handle = event.currentTarget;
    const origin = event.clientX;
    let current = width;
    handle.setPointerCapture(event.pointerId);
    const move = (next: globalThis.PointerEvent) => {
      current = clamp(width + next.clientX - origin);
      onResize(current);
    };
    const finish = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", finish);
      handle.removeEventListener("pointercancel", finish);
      onCommit(current);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
  }
  return <span className="column-resize-handle" role="separator" tabIndex={0}
    aria-label={`Ширина: ${label}`} aria-orientation="vertical"
    aria-valuemin={minimum} aria-valuemax={600} aria-valuenow={width}
    title="Потяните границу столбца. Стрелки ← → меняют ширину."
    onPointerDown={start} onClick={(event) => event.stopPropagation()}
    onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const next = clamp(event.key === "Home" ? minimum : event.key === "End" ? 600 : width + (event.key === "ArrowRight" ? 10 : -10));
      onResize(next); onCommit(next);
    }} />;
}

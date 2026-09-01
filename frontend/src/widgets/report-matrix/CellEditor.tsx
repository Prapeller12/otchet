import { useEffect, useRef, type KeyboardEvent } from "react";

type CellEditorProps = {
  value: string;
  label: string;
  onChange(value: string): void;
  onCommit(move: "enter" | "tab" | "stay", backwards?: boolean): void;
  onCancel(): void;
};

export function CellEditor({
  value,
  label,
  onChange,
  onCommit,
  onCancel,
}: CellEditorProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const finishedRef = useRef(false);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Escape") {
      event.preventDefault();
      finishedRef.current = true;
      onCancel();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      finishedRef.current = true;
      onCommit("enter");
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      finishedRef.current = true;
      onCommit("tab", event.shiftKey);
    }
  }

  return (
    <input
      ref={inputRef}
      className="matrix-cell-editor"
      value={value}
      aria-label={label}
      inputMode="decimal"
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={() => {
        if (!finishedRef.current) onCommit("stay");
      }}
    />
  );
}

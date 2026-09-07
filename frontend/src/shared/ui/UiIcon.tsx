const paths = {
  calendar: "M4 5h16v15H4z M4 9h16 M8 3v4 M16 3v4 M8 13h2 M14 13h2 M8 17h2 M14 17h2",
  factory: "M3 21V10l6 3V8l6 3V3h4l2 18H3 M7 17h1 M12 17h1 M17 17h1",
  buildings: "M3 21V7h7v14 M10 21V3h7v18 M17 11h4v10 M1 21h22 M6 11h1 M6 15h1 M13 7h1 M13 11h1 M13 15h1",
  settings: "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6",
  import: "M12 3v12 M8 11l4 4 4-4 M4 16v5h16v-5",
  export: "M12 15V3 M8 7l4-4 4 4 M4 16v5h16v-5",
  save: "M5 3h12l4 4v14H3V3h2 M7 3v6h10V3 M7 21v-8h10v8",
  edit: "M4 16v4h4L20 8l-4-4L4 16 M14 6l4 4",
};

/** Decorative local SVG: accessible names remain on the existing controls. */
export function UiIcon({ name }: { name: keyof typeof paths }) {
  return <svg className="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    <path d={paths[name]} />
  </svg>;
}

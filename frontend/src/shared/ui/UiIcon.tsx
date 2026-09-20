import { UI_GLYPHS, type UiIconName } from "../config/action-icons";

/** Decorative local SVG: accessible names remain on the existing controls. */
export function UiIcon({ name }: { name: UiIconName }) {
  const glyph = UI_GLYPHS[name];
  return <svg className="ui-icon" data-icon={name} width="16" height="16" viewBox={glyph.viewBox} fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {glyph.d.map(d => <path key={d} d={d} />)}
  </svg>;
}

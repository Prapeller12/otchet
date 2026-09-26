/** Local SVG geometry. Original glyphs: Frontend Reference Kit v1 Icon.tsx.
 * App extensions keep the same round, currentColor, 2-unit stroke language.
 * Meaningful action icons accompany labels. Numeric cells, month choices,
 * preset names, and generic confirmation/cancel labels intentionally stay text-only.
 */
type Glyph = { viewBox: string; d: string[] };

export const UI_GLYPHS = {
  // ── сетка 16 ──
  clear: { viewBox: '0 0 16 16', d: ['M4.5 4.5l7 7', 'M11.5 4.5l-7 7'] },
  check: { viewBox: '0 0 16 16', d: ['M3.5 8.5l3 3 6-7'] },
  minus: { viewBox: '0 0 16 16', d: ['M4 8h8'] },
  equal: { viewBox: '0 0 16 16', d: ['M4 6h8', 'M4 10h8'] },
  exclamation: { viewBox: '0 0 16 16', d: ['M8 3.5V9', 'M8 12.5v0'] },
  'chevron-down': { viewBox: '0 0 16 16', d: ['M4 6l4 4 4-4'] },
  'chevron-right': { viewBox: '0 0 16 16', d: ['M6 4l4 4-4 4'] },
  // ── сетка 24 ──
  close: { viewBox: '0 0 24 24', d: ['M6 6l12 12', 'M18 6L6 18'] },
  'arrow-left': { viewBox: '0 0 24 24', d: ['M14.5 6l-6 6 6 6'] },
  'arrow-right': { viewBox: '0 0 24 24', d: ['M9.5 6l6 6-6 6'] },
  search: { viewBox: '0 0 24 24', d: ['M16.5 10.5a6 6 0 1 1-12 0 6 6 0 0 1 12 0z', 'M15 15l5 5'] },
  print: {
    viewBox: '0 0 24 24',
    d: [
      'M7 9V4h10v5',
      'M7 17H5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-2',
      'M7 14h10v6H7z',
    ],
  },
  // «Настроить» — ползунки, а не шестерёнка.
  settings: {
    viewBox: '0 0 24 24',
    d: [
      'M4 7h9',
      'M19 7h1',
      'M4 17h1',
      'M11 17h9',
      'M18 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0z',
      'M10 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0z',
    ],
  },
  // «Удалить» — корзина (QA-3 п.9).
  trash: {
    viewBox: '0 0 24 24',
    d: [
      'M4 7h16',
      'M9.5 7V4.5h5V7',
      'M6.5 7l.9 12.1a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4L17.5 7',
      'M10 11v6',
      'M14 11v6',
    ],
  },
  list: { viewBox: '0 0 24 24', d: ['M9 6h11', 'M9 12h11', 'M9 18h11', 'M4.5 6v0', 'M4.5 12v0', 'M4.5 18v0'] },
  // «Связи» — граф из трёх узлов.
  link: {
    viewBox: '0 0 24 24',
    d: [
      'M8.5 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z',
      'M20.5 6a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z',
      'M20.5 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z',
      'M8.2 10.9l7.6-3.8',
      'M8.2 13.1l7.6 3.8',
    ],
  },

  // Дополнения Отчётности: действия, которых нет в исходном реестре.
  "calendar": { viewBox: "0 0 24 24", d: ["M4 5h16v15H4z M4 9h16 M8 3v4 M16 3v4 M8 13h2 M14 13h2 M8 17h2 M14 17h2"] },
  "factory": { viewBox: "0 0 24 24", d: ["M3 21V10l6 3V8l6 3V3h4l2 18H3 M7 17h1 M12 17h1 M17 17h1"] },
  "buildings": { viewBox: "0 0 24 24", d: ["M3 21V7h7v14 M10 21V3h7v18 M17 11h4v10 M1 21h22 M6 11h1 M6 15h1 M13 7h1 M13 11h1 M13 15h1"] },
  "import": { viewBox: "0 0 24 24", d: ["M12 3v12 M8 11l4 4 4-4 M4 16v5h16v-5"] },
  "export": { viewBox: "0 0 24 24", d: ["M12 15V3 M8 7l4-4 4 4 M4 16v5h16v-5"] },
  "save": { viewBox: "0 0 24 24", d: ["M5 3h12l4 4v14H3V3h2 M7 3v6h10V3 M7 21v-8h10v8"] },
  "edit": { viewBox: "0 0 24 24", d: ["M4 16v4h4L20 8l-4-4L4 16 M14 6l4 4"] },
  "chevron-up": { viewBox: "0 0 16 16", d: ["M4 10l4-4 4 4"] },
  "add": { viewBox: "0 0 24 24", d: ["M12 5v14 M5 12h14"] },
  "help": { viewBox: "0 0 24 24", d: ["M9 9a3 3 0 1 1 4 2.8c-1 .3-1 1.2-1 2.2 M12 17v0 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0"] },
  "users": { viewBox: "0 0 24 24", d: ["M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M2 21v-3a7 7 0 0 1 14 0v3 M17 4a4 4 0 0 1 0 7 M22 21v-3a7 7 0 0 0-3-6"] },
  "key": { viewBox: "0 0 24 24", d: ["M10 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M9 11l11 11 M15 17l3-3 M18 20l3-3"] },
  "undo": { viewBox: "0 0 24 24", d: ["M4 10h10a6 6 0 0 1 0 12 M4 10l5-5 M4 10l5 5"] },
  "copy": { viewBox: "0 0 24 24", d: ["M8 8h13v13H8z M4 16H3V3h13v1"] },
  "paste": { viewBox: "0 0 24 24", d: ["M8 4H4v17h16V4h-4 M8 2h8v5H8z M8 12h8 M8 16h6"] },
  "arrow-up": { viewBox: "0 0 24 24", d: ["M12 20V4 M5 11l7-7 7 7"] },
  "arrow-down": { viewBox: "0 0 24 24", d: ["M12 4v16 M5 13l7 7 7-7"] },
} satisfies Record<string, Glyph>;


export type UiIconName = keyof typeof UI_GLYPHS;

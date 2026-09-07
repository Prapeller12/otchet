import { useState } from "react";
import type { FieldIndicator, FieldPreset } from "../../shared/api/application-gateway";

/** Inline reference: opening/copying never closes the settings dialog. */
export function FormulaLibrary({ indicators, presets, onInsert }: {
  indicators: FieldIndicator[]; presets: FieldPreset[];
  onInsert(code: string, formula: string): void;
}) {
  const [message, setMessage] = useState("");
  const [target, setTarget] = useState("");
  const [source, setSource] = useState("");
  const [second, setSecond] = useState("");
  const codes = new Set(indicators.map(item => item.code));
  const ready = presets.filter(p => p.required_codes.every(code => codes.has(code)))
    .flatMap(p => p.indicators.filter(i => i.formula).map(i => ({ ...i, label: `${p.label}: ${i.label}` })));
  const snippets = source && codes.has(source) ? [
    { label: "Сумма внесённых значений с начала года", formula: `=CUMSUM(${source})` },
    { label: "Количество полных комплектов (норма должна быть больше нуля)", formula: `=ROUNDDOWN(${source}/NORM,0)` },
    ...(second && codes.has(second) ? [
      { label: "Разность двух показателей на дату", formula: `=${source}-${second}` },
      { label: "Остаток: начальный + приход − расход", formula: `=BALANCE(${source},${second})` },
    ] : []),
  ] : [];
  function canInsert(formula: string, code: string) {
    const refs = formula.match(/\b[A-Z_][A-Z_0-9]*\b/g) ?? [];
    const functions = new Set(["CUMSUM", "CUM", "BALANCE", "IF", "SUM", "MIN", "MAX", "ROUNDDOWN", "NORM", "OPENING"]);
    return codes.has(code) && refs.every(ref => ref !== code && (functions.has(ref) || codes.has(ref)));
  }
  async function copy(formula: string) {
    try { await navigator.clipboard.writeText(formula); setMessage("Формула скопирована. Вставьте её в поле формулы через Ctrl+V."); }
    catch { setMessage("Буфер обмена недоступен. Выделите текст формулы и нажмите Ctrl+C."); }
  }
  function card(label: string, formula: string, code: string, key: string) {
    return <article className="formula-card" key={key}>
      <strong>{label}</strong>
      <input aria-label={`Текст формулы: ${label}`} readOnly value={formula} onFocus={event => event.currentTarget.select()} />
      <div className="compact-actions">
        <button type="button" className="button secondary" onClick={() => void copy(formula)}>Копировать формулу</button>
        <button type="button" className="button secondary" disabled={!canInsert(formula, code)} onClick={() => {
          onInsert(code, formula); setMessage("Формула вставлена. Для сохранения нажмите «Применить настройки».");
        }}>Вставить в показатель</button>
      </div>
      {!canInsert(formula, code) && <p className="field-help">Выберите отдельный показатель результата и добавьте используемые показатели. Для расчёта из Excel можно применить полный набор кнопкой выше.</p>}
    </article>;
  }
  return <details className="formula-library">
    <summary>Библиотека формул — открыть шпаргалку</summary>
    <p>Окно настроек остаётся открытым. Выделите формулу для копирования или вставьте её кнопкой. Значения рассчитываются после применения настроек.</p>
    <label>Показатель результата<select value={codes.has(target) ? target : ""} onChange={e => setTarget(e.target.value)}>
      <option value="">Выберите, куда вставить формулу</option>
      {indicators.map(i => <option key={i.code} value={i.code}>{i.label} ({i.code})</option>)}
    </select></label>
    <div className="formula-reference-list">
      {ready.map((i, index) => card(i.label, i.formula, target, `preset-${index}`))}
    </div>
    <h4>Составить по своим показателям</h4>
    <label>Первый показатель / приход<select value={codes.has(source) ? source : ""} onChange={e => setSource(e.target.value)}>
      <option value="">Выберите источник</option>{indicators.map(i => <option key={i.code} value={i.code}>{i.label} ({i.code})</option>)}
    </select></label>
    <label>Второй показатель / расход<select value={codes.has(second) ? second : ""} onChange={e => setSecond(e.target.value)}>
      <option value="">Не выбран</option>{indicators.map(i => <option key={i.code} value={i.code}>{i.label} ({i.code})</option>)}
    </select></label>
    {snippets.map((i, index) => card(i.label, i.formula, target, `snippet-${index}`))}
    <p className="field-help">NORM — норма входимости, OPENING — начальный остаток. Пустые первичные значения отличаются от нуля. CUMSUM суммирует внесённые значения; BALANCE учитывает дату начального остатка. Ссылки A1 и межлистовые ссылки не поддерживаются.</p>
    <p role="status">{message}</p>
  </details>;
}

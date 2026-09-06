import { useState } from "react";
import type { FieldConfiguration, FieldIndicator, FieldPreset } from "../../shared/api/application-gateway";

export const CATEGORY_LABELS: Record<string, string> = {
  UNSPECIFIED: "Не задана", PKI: "ПКИ", DSE: "ДСЕ", PART: "Составная часть",
  PRODUCT: "Готовое изделие", ASSEMBLY: "Сборочный комплект",
};

export function PositionFieldsEditor({ value, presets = [], onChange, onBusyChange }: {
  value: FieldConfiguration; onChange(value: FieldConfiguration): void;
  presets?: FieldPreset[];
  onBusyChange?(busy: boolean): void;
}) {
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  function edit(index: number, patch: Partial<FieldIndicator>) {
    onChange({ ...value, indicators: value.indicators.map((item, i) => i === index ? { ...item, ...patch } : item) });
  }
  function preset(preset: FieldPreset) {
    const indicators = [...value.indicators];
    for (const item of preset.indicators) {
      const index = indicators.findIndex((candidate) => candidate.code === item.code);
      if (index >= 0) indicators[index] = { ...indicators[index]!, formula: item.formula };
      else indicators.push({ ...item });
    }
    onChange({ ...value, indicators });
    setError("");
  }
  function upload(file: File | undefined) {
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type) || file.size > 2 * 1024 * 1024) {
      setError("Выберите PNG или JPEG до 2 МБ."); return;
    }
    setReading(true);
    onBusyChange?.(true);
    const reader = new FileReader();
    reader.onload = () => {
      onChange({ ...value, image: String(reader.result) });
      setReading(false); onBusyChange?.(false); setError("");
    };
    reader.onerror = () => { setReading(false); onBusyChange?.(false); setError("Не удалось прочитать изображение."); };
    reader.readAsDataURL(file);
  }
  return <details className="position-fields">
    <summary>Изображение, показатели и формулы ({value.indicators.length})</summary>
    <fieldset disabled={reading}>
      <div className="position-properties">
        <label>Изображение ПКИ / ДСЕ
          <input type="file" accept="image/png,image/jpeg" onChange={(event) => upload(event.target.files?.[0])} />
        </label>
        {value.image && <div className="position-picture"><img src={value.image} alt="Изображение позиции" />
          <button type="button" className="mini-button" onClick={() => onChange({ ...value, image: "" })}>Убрать изображение</button></div>}
        <label>Норма входимости<input inputMode="decimal" value={value.norm} onChange={(event) => onChange({ ...value, norm: event.target.value.replace(",", ".") })} /></label>
        <label>Начальный остаток месяца<input inputMode="decimal" value={value.opening} onChange={(event) => onChange({ ...value, opening: event.target.value.replace(",", ".") })} /></label>
      </div>
      <p className="field-help">Пустая формула — ввод (кроме защищённых исходных расчётных строк). Формулы: + − * /, сравнения, SUM, MIN, MAX, IF, ROUNDDOWN. Ссылки — коды показателей этой позиции; NORM — норма, OPENING — начальный остаток, CUM(код) — сумма с начала месяца. Пустые исходные данные не считаются нулём.</p>
      {value.indicators.map((item, index) => <div className="field-indicator" key={index}>
        <label>Название показателя<input value={item.label} onChange={(event) => edit(index, { label: event.target.value })} /></label>
        <label>Код для формул<input value={item.code} readOnly title="Постоянный код сохраняет связь с ранее введёнными данными" /></label>
        <label className="formula-editor">Формула<input placeholder="Вводимое значение" value={item.formula} onChange={(event) => edit(index, { formula: event.target.value })} /></label>
        <div className="row-actions">
          <button type="button" className="mini-button" aria-label="Показатель выше" disabled={index === 0} onClick={() => {
            const next = [...value.indicators]; [next[index - 1], next[index]] = [next[index]!, next[index - 1]!]; onChange({ ...value, indicators: next });
          }}>↑</button>
          <button type="button" className="mini-button" aria-label="Убрать показатель" onClick={() => onChange({ ...value, indicators: value.indicators.filter((_, i) => i !== index) })}>×</button>
        </div>
      </div>)}
      <div className="compact-actions">
        <button type="button" className="button secondary" disabled={value.indicators.length >= 40} onClick={() => {
          const code = `FIELD_${crypto.randomUUID().replaceAll("-", "").slice(0, 12).toUpperCase()}`;
          onChange({ ...value, indicators: [...value.indicators, { code, label: "Новый показатель", formula: "" }] });
        }}>+ Показатель</button>
        {presets.filter((item) => item.required_codes.every((code) => value.indicators.some((indicator) => indicator.code === code))).map((item) =>
          <button key={item.label} type="button" className="button secondary" onClick={() => preset(item)}>{item.label}</button>)}
      </div>
      <p className="field-help">Комплектность формы — минимум по позициям с показателем READY_SETS. Укажите норму и начальный остаток явно; эти настройки не изменяют утверждённую спецификацию BOM.</p>
      {error && <p role="alert">{error}</p>}
    </fieldset>
  </details>;
}

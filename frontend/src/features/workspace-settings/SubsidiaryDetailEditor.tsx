import type { ReportLayoutRow, SubsidiaryDetail } from "../../shared/api/application-gateway";
import { CATEGORY_LABELS } from "./PositionFieldsEditor";

export function SubsidiaryDetailEditor({ row, onChange, onRemove, onMove, disabled, onBusy }: {
  row: ReportLayoutRow; onChange(row: ReportLayoutRow): void; onRemove(): void; onMove(direction: -1 | 1): void; disabled: boolean; onBusy(busy: boolean): void;
}) {
  const configuration = row.configuration ?? { category: "UNSPECIFIED", image: "", norm: "", opening: "", indicators: [] };
  const detail: SubsidiaryDetail = configuration.subsidiary ?? { number: "", designation: "", suppliers: [{ id: "PRIMARY", name: row.party_name, contract: "", archived: false }] };
  function update(patch: Partial<SubsidiaryDetail>) { onChange({ ...row, configuration: { ...configuration, subsidiary: { ...detail, ...patch } } }); }
  return <fieldset disabled={disabled} className="layout-row-editor subsidiary-detail-editor">
    <legend>{row.position_name}</legend>
    <div className="subsidiary-detail-fields">
      <label>№ п/п<input value={detail.number} onChange={e => update({ number: e.target.value })} /></label>
      <label>Обозначение (код детали)<input value={detail.designation} onChange={e => update({ designation: e.target.value })} /></label>
      <label>Наименование<input value={row.position_name} onChange={e => onChange({ ...row, position_name: e.target.value })} /></label>
      <label>Категория<select value={configuration.category} onChange={e => onChange({ ...row, configuration: { ...configuration, subsidiary: detail, category: e.target.value } })}>{Object.entries(CATEGORY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>Входимость, шт.<input inputMode="decimal" value={configuration.norm} onChange={e => onChange({ ...row, configuration: { ...configuration, subsidiary: detail, norm: e.target.value.replace(",", ".") } })} /></label>
      <label>Изображение детали<input type="file" accept="image/png,image/jpeg" onChange={e => {
        const file = e.target.files?.[0]; if (!file) return;
        if (file.size > 2 * 1024 * 1024) { window.alert("Выберите изображение до 2 МБ"); return; }
        onBusy(true); const reader = new FileReader();
        reader.onload = () => { onChange({ ...row, configuration: { ...configuration, subsidiary: detail, image: String(reader.result) } }); onBusy(false); };
        reader.onerror = () => { window.alert("Не удалось прочитать изображение"); onBusy(false); };
        reader.onabort = () => onBusy(false); reader.readAsDataURL(file);
      }} /></label>
      {configuration.image && <><img width={64} src={configuration.image} alt={row.position_name} /><button type="button" onClick={() => onChange({ ...row, configuration: { ...configuration, subsidiary: detail, image: "" } })}>Убрать изображение</button></>}
    </div>
    <h4>Производители этой детали</h4>
    {detail.suppliers.map((supplier, index) => <div className="subsidiary-supplier" key={supplier.id}>
      <label>Производитель<input disabled={supplier.archived} value={supplier.name} onChange={e => update({ suppliers: detail.suppliers.map((s, i) => i === index ? { ...s, name: e.target.value } : s) })} /></label>
      <label>Объём поставок по договору<input disabled={supplier.archived} inputMode="decimal" value={supplier.contract} onChange={e => update({ suppliers: detail.suppliers.map((s, i) => i === index ? { ...s, contract: e.target.value.replace(",", ".") } : s) })} /></label>
      <button type="button" onClick={() => update({ suppliers: detail.suppliers.map((s, i) => i === index ? { ...s, archived: !s.archived } : s) })}>{supplier.archived ? "Восстановить производителя" : "Убрать производителя"}</button>
    </div>)}
    <div className="row-actions">
      <button type="button" onClick={() => update({ suppliers: [...detail.suppliers, { id: crypto.randomUUID().replaceAll("-", "").toUpperCase(), name: "Новый производитель", contract: "", archived: false }] })}>+ Добавить производителя</button>
      <button type="button" onClick={() => onMove(-1)}>↑ Деталь выше</button><button type="button" onClick={() => onMove(1)}>↓ Деталь ниже</button>
      <button type="button" onClick={onRemove}>Убрать деталь целиком</button>
    </div>
    <p>Начальный остаток и поступления вводятся в матрице за каждый месяц. Производители с сохранёнными значениями остаются видны в соответствующем году с отметкой «архив».</p>
  </fieldset>;
}

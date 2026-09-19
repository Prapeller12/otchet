import type { FieldIndicator } from "../../shared/api/application-gateway";

/** Append explicit user codes; never relabel or replace recorded aggregate rows. */
export function addDailyCodeFields(indicators: FieldIndicator[], rawCodes: string, prefix: "ASSEMBLY" | "PRODUCT"): FieldIndicator[] {
  const codes = rawCodes.split(/[,;\n]/).map(code => code.trim()).filter(Boolean);
  if (!codes.length || codes.some(code => code.length > 30)) throw new Error("Введите коды через запятую, не более 30 символов в каждом.");
  if (new Set(codes).size !== codes.length) throw new Error("Коды не должны повторяться.");
  const labels = ["План выпуска", "Выпущено", "Прибытие"];
  const metrics = ["PLAN", "RELEASED", "ARRIVAL"];
  const existingLabels = new Set(indicators.map(item => item.label));
  if (codes.some(code => labels.some(label => existingLabels.has(`${label} (${code})`)))) throw new Error("Для одного из кодов строки уже добавлены. Проверьте существующие показатели.");
  if (indicators.length + codes.length * 3 > 40) throw new Error("Не более 40 показателей в одной позиции. Добавьте меньше кодов.");
  const ids = codes.map(() => crypto.randomUUID().replaceAll("-", "").slice(0, 12).toUpperCase());
  return [...indicators, ...labels.flatMap((label, index) => codes.map((code, codeIndex) => ({
    code: `CODE_${prefix}_${metrics[index]}_${ids[codeIndex]}`,
    label: `${label} (${code})`, formula: "",
  })))];
}

import { UiIcon } from "../../shared/ui/UiIcon";
import { useState } from "react";
import { useDialogFocus } from "./useDialogFocus";
const steps = [
  { title: "Выберите отчёт", text: "Вверху выберите нужную вкладку и свою организацию. Затем укажите год и месяц отчёта." },
  { title: "Заполните таблицу", text: "Дважды нажмите на нужную ячейку, введите число и нажмите Enter. Расчётные ячейки программа заполняет сама. Наведите на такую ячейку указатель: подсказка объяснит, что нужно заполнить для расчёта. Включить или выключить её можно флажком «Подсказки при наведении» вверху окна. Ноль означает, что количества нет. Пустая ячейка — данных пока нет." },
  { title: "Сохраните с проверкой", text: "Нажмите «Сохранить» или расположенную рядом «Печать / PDF A4». Проверьте данные и введите личный код руководителя проекта, проверяющего или администратора. До сохранения изменения остаются только на экране. Планы и сведения об изделии открываются кнопкой «План и сведения»." },
];
export function Onboarding({ onClose }: { onClose(): void }) {
  const dialogRef = useDialogFocus();
  const [step, setStep] = useState(0);
  return <div className="excel-dialog-backdrop"><section ref={node => { dialogRef.current = node; }} className="access-card onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
    <p className="access-eyebrow">Как заполнить отчёт · {step + 1} из {steps.length}</p>
    <h2 id="onboarding-title">{steps[step]!.title}</h2><p>{steps[step]!.text}</p>
    <div className="access-actions"><button className="button secondary" onClick={onClose}>Пропустить подсказки</button>{step > 0 && <button className="button secondary" onClick={() => setStep(step - 1)}><UiIcon name="arrow-left" />Назад</button>}<button autoFocus className="button primary" onClick={() => step === steps.length - 1 ? onClose() : setStep(step + 1)}>{step === steps.length - 1 ? "Начать заполнение" : "Далее"}<UiIcon name="arrow-right" /></button></div>
    <p className="access-footnote">Эти подсказки всегда доступны по кнопке «Как заполнить».</p>
  </section></div>;
}

import { useState } from "react";
import { useDialogFocus } from "./useDialogFocus";
const steps = [
  { title: "Выберите отчёт", text: "Вверху выберите нужную вкладку и свою организацию. Затем укажите год и месяц отчёта." },
  { title: "Заполните таблицу", text: "Дважды нажмите на нужную ячейку, введите число и нажмите Enter. Расчётные ячейки программа заполняет сама. Ноль означает, что количества нет. Пустая ячейка — данных пока нет." },
  { title: "Сохраните с проверкой", text: "Нажмите «Сохранить». Проверяющий или администратор должен проверить изменения и ввести свой код. До подтверждения изменения остаются только на экране. Экспорт и проверка отчёта находятся в меню «Ещё»." },
];
export function Onboarding({ onClose }: { onClose(): void }) {
  const dialogRef = useDialogFocus();
  const [step, setStep] = useState(0);
  return <div className="excel-dialog-backdrop"><section ref={node => { dialogRef.current = node; }} className="access-card onboarding-dialog" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
    <p className="access-eyebrow">Как заполнить отчёт · {step + 1} из {steps.length}</p>
    <h2 id="onboarding-title">{steps[step]!.title}</h2><p>{steps[step]!.text}</p>
    <div className="access-actions"><button className="button secondary" onClick={onClose}>Пропустить подсказки</button>{step > 0 && <button className="button secondary" onClick={() => setStep(step - 1)}>Назад</button>}<button autoFocus className="button primary" onClick={() => step === steps.length - 1 ? onClose() : setStep(step + 1)}>{step === steps.length - 1 ? "Начать заполнение" : "Далее"}</button></div>
    <p className="access-footnote">Эти подсказки всегда доступны по кнопке «Как заполнить».</p>
  </section></div>;
}

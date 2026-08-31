# Frontend tests

`contracts/report-cell-contract.test.mjs` автономно проверяет JSON Schema, примеры и
согласованность основных TypeScript-дискриминаторов без npm-зависимостей.

Строгая проверка всех текущих frontend `.ts` выполняется закреплённой версией TypeScript:

```text
npm ci --ignore-scripts --no-audit --no-fund
npm run typecheck
node frontend/tests/contracts/report-cell-contract.test.mjs
```

После утверждения форм здесь должны появиться тесты структуры матриц, клавиатурной навигации,
вставки диапазона, блокировки расчётных ячеек и сохранения состояния.

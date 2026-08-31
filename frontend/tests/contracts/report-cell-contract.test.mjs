import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(testDirectory, "../../..");

const schemaPath = resolve(
  repositoryRoot,
  "resources/schemas/report-cell/report-cell.schema.json",
);
const validFixturePath = resolve(
  testDirectory,
  "fixtures/report-cell.valid.json",
);
const invalidFixturePath = resolve(
  testDirectory,
  "fixtures/report-cell.invalid.json",
);
const typesPath = resolve(
  repositoryRoot,
  "frontend/src/shared/api/report-cell-contract.ts",
);

const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const schema = readJson(schemaPath);
const validFixtures = readJson(validFixturePath);
const invalidFixtures = readJson(invalidFixturePath);

function resolveInternalReference(reference) {
  assert.match(reference, /^#\//, `Only internal schema references are allowed: ${reference}`);

  return reference
    .slice(2)
    .split("/")
    .map((token) => token.replaceAll("~1", "/").replaceAll("~0", "~"))
    .reduce((current, token) => current?.[token], schema);
}

function collectReferences(value, references = []) {
  if (Array.isArray(value)) {
    value.forEach((item) => collectReferences(item, references));
    return references;
  }

  if (value && typeof value === "object") {
    if (typeof value.$ref === "string") {
      references.push(value.$ref);
    }
    Object.values(value).forEach((item) => collectReferences(item, references));
  }

  return references;
}

function isIsoCalendarDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;

  const [, yearText, monthText, dayText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const date = new Date(Date.UTC(year, month - 1, day));

  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

function validate(instance, currentSchema, instancePath = "$") {
  if (currentSchema.$ref) {
    return validate(
      instance,
      resolveInternalReference(currentSchema.$ref),
      instancePath,
    );
  }

  const errors = [];

  if (Object.hasOwn(currentSchema, "const") && instance !== currentSchema.const) {
    errors.push(`${instancePath}: value must equal ${JSON.stringify(currentSchema.const)}`);
  }

  if (currentSchema.enum && !currentSchema.enum.includes(instance)) {
    errors.push(`${instancePath}: value is not in the allowed enum`);
  }

  const instanceIsObject =
    instance !== null && typeof instance === "object" && !Array.isArray(instance);

  if (currentSchema.type === "object" && !instanceIsObject) {
    errors.push(`${instancePath}: expected object`);
    return errors;
  }

  // JSON Schema object keywords also apply when a subschema omits an explicit
  // `type`. This matters for the mutually-exclusive coordinate branches.
  if (instanceIsObject && (currentSchema.required || currentSchema.properties)) {
    for (const requiredProperty of currentSchema.required ?? []) {
      if (!Object.hasOwn(instance, requiredProperty)) {
        errors.push(`${instancePath}.${requiredProperty}: required property is missing`);
      }
    }

    for (const [property, propertyValue] of Object.entries(instance)) {
      const propertySchema = currentSchema.properties?.[property];
      if (propertySchema) {
        errors.push(
          ...validate(propertyValue, propertySchema, `${instancePath}.${property}`),
        );
      } else if (currentSchema.additionalProperties === false) {
        errors.push(`${instancePath}.${property}: additional property is forbidden`);
      }
    }
  }

  if (currentSchema.type === "string") {
    if (typeof instance !== "string") {
      errors.push(`${instancePath}: expected string`);
      return errors;
    }

    if (currentSchema.minLength !== undefined && instance.length < currentSchema.minLength) {
      errors.push(`${instancePath}: string is shorter than minLength`);
    }
    if (currentSchema.pattern && !new RegExp(currentSchema.pattern, "u").test(instance)) {
      errors.push(`${instancePath}: string does not match pattern`);
    }
    if (currentSchema.format === "date" && !isIsoCalendarDate(instance)) {
      errors.push(`${instancePath}: invalid ISO calendar date`);
    }
  }

  for (const subSchema of currentSchema.allOf ?? []) {
    errors.push(...validate(instance, subSchema, instancePath));
  }

  if (currentSchema.oneOf) {
    const branchResults = currentSchema.oneOf.map((subSchema) =>
      validate(instance, subSchema, instancePath),
    );
    const matchingBranches = branchResults.filter(
      (branchErrors) => branchErrors.length === 0,
    ).length;
    if (matchingBranches !== 1) {
      errors.push(
        `${instancePath}: expected exactly one oneOf branch, got ${matchingBranches}`,
      );
    }
  }

  if (currentSchema.not && validate(instance, currentSchema.not, instancePath).length === 0) {
    errors.push(`${instancePath}: forbidden schema matched`);
  }

  return errors;
}

assert.equal(
  schema.$schema,
  "https://json-schema.org/draft/2020-12/schema",
  "The contract must use JSON Schema draft 2020-12",
);
assert.equal(schema.additionalProperties, false);
assert.equal(schema.$defs.ReportCellCoordinate.additionalProperties, false);
assert.equal(schema.$defs.ReportCellCoordinate.allOf.length, 3);
assert.equal(schema.$defs.ReportCellValue.oneOf.length, 2);
assert.deepEqual(schema.$defs.ReportType.enum, [
  "DAILY_MOVEMENT",
  "HEAD_SITE",
  "SUBSIDIARY",
]);
assert.deepEqual(schema.$defs.ReportCellState.properties.access.enum, [
  "editable",
  "calculated",
  "locked",
]);
assert.deepEqual(schema.$defs.ReportCellState.properties.persistence.enum, [
  "error",
  "dirty",
  "saving",
  "saved",
]);

for (const reference of collectReferences(schema)) {
  assert.ok(resolveInternalReference(reference), `Unresolved schema reference: ${reference}`);
}

for (const fixture of validFixtures) {
  assert.deepEqual(
    validate(fixture.cell, schema),
    [],
    `Valid fixture failed: ${fixture.name}`,
  );
}

for (const fixture of invalidFixtures) {
  assert.ok(
    validate(fixture.cell, schema).length > 0,
    `Invalid fixture unexpectedly passed: ${fixture.name}`,
  );
}

const typesSource = readFileSync(typesPath, "utf8");
for (const requiredToken of [
  "report_type",
  "REPORT_TYPES",
  "DAILY_MOVEMENT",
  "HEAD_SITE",
  "SUBSIDIARY",
  "organization_id",
  "product_id",
  "component_id",
  "metric_code",
  "operation_type",
  "operation_date",
  "period_start",
  "bom_version_id",
  'kind: "DATA_NOT_PROVIDED"',
  'kind: "QUANTITY"',
  "component_id?: never",
  "product_id?: never",
  "operation_type?: never",
  "metric_code?: never",
  "period_start?: never",
  "operation_date?: never",
  ...schema.$defs.ReportCellState.properties.access.enum.map(
    (state) => `"${state}"`,
  ),
  ...schema.$defs.ReportCellState.properties.persistence.enum.map(
    (state) => `"${state}"`,
  ),
]) {
  assert.ok(
    typesSource.includes(requiredToken),
    `TypeScript contract is missing required token: ${requiredToken}`,
  );
}

console.log(
  `report-cell contract: ${validFixtures.length} valid and ${invalidFixtures.length} invalid fixtures passed`,
);

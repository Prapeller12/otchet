import type {
  DataNotProvidedValue,
  QuantityValue,
  ReportCellAccessState,
  ReportCellContract,
  ReportCellCoordinate,
  ReportCellPersistenceState,
  ReportCellState,
  ReportCellValue,
} from "../../../shared/api/report-cell-contract";

/**
 * Frontend entity view of the application contract. No business calculation
 * or database access belongs in this layer.
 */
export type ReportCell = ReportCellContract;

export type {
  DataNotProvidedValue,
  QuantityValue,
  ReportCellAccessState,
  ReportCellCoordinate,
  ReportCellPersistenceState,
  ReportCellState,
  ReportCellValue,
};

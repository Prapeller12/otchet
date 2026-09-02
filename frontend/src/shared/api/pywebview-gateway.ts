import type {
  ApplicationGateway,
  CommitImportRequest,
  CommitImportResult,
  ExportRequest,
  ExportResult,
  ImportPreview,
  ImportRequest,
  OrganizationList,
  OrganizationResult,
  ReportLayoutContract,
  ReportLayoutQuery,
  ReportMatrixContract,
  ReportMatrixQuery,
  SaveReportLayoutRequest,
  SaveReportCellsRequest,
  SaveReportCellsResponse,
} from "./application-gateway";
import { parseReportMatrix, parseSaveResponse } from "./runtime-guards";

type BridgeEnvelope =
  | { ok: true; data: unknown }
  | { ok: false; error: { code: string; message: string } };

type PyWebViewApi = {
  get_report_matrix(query: ReportMatrixQuery): Promise<BridgeEnvelope>;
  save_report_cells(request: SaveReportCellsRequest): Promise<BridgeEnvelope>;
  validate_import(request: ImportRequest): Promise<BridgeEnvelope>;
  commit_import(request: CommitImportRequest): Promise<BridgeEnvelope>;
  export_report(request: ExportRequest): Promise<BridgeEnvelope>;
  list_organizations(request: Record<string, never>): Promise<BridgeEnvelope>;
  create_organization(request: { name: string }): Promise<BridgeEnvelope>;
  rename_organization(request: {
    organization_id: string;
    name: string;
  }): Promise<BridgeEnvelope>;
  archive_organization(request: {
    organization_id: string;
  }): Promise<BridgeEnvelope>;
  get_report_layout(request: ReportLayoutQuery): Promise<BridgeEnvelope>;
  save_report_layout(request: SaveReportLayoutRequest): Promise<BridgeEnvelope>;
};

declare global {
  interface Window {
    pywebview?: { api?: Partial<PyWebViewApi> };
  }
}

function unwrap(envelope: BridgeEnvelope): unknown {
  if (!envelope.ok) {
    throw new Error(`${envelope.error.code}: ${envelope.error.message}`);
  }
  return envelope.data;
}

export function hasPyWebViewBridge(): boolean {
  return typeof window.pywebview?.api?.get_report_matrix === "function";
}

export class PyWebViewGateway implements ApplicationGateway {
  readonly mode = "pywebview" as const;
  readonly #api: PyWebViewApi;

  constructor(api: PyWebViewApi) {
    this.#api = api;
  }

  static fromWindow(): PyWebViewGateway {
    const api = window.pywebview?.api;
    if (
      typeof api?.get_report_matrix !== "function" ||
      typeof api.save_report_cells !== "function" ||
      typeof api.validate_import !== "function" ||
      typeof api.commit_import !== "function" ||
      typeof api.export_report !== "function" ||
      typeof api.list_organizations !== "function" ||
      typeof api.create_organization !== "function" ||
      typeof api.rename_organization !== "function" ||
      typeof api.archive_organization !== "function" ||
      typeof api.get_report_layout !== "function" ||
      typeof api.save_report_layout !== "function"
    ) {
      throw new Error("PyWebView application bridge is incomplete");
    }
    return new PyWebViewGateway(api as PyWebViewApi);
  }

  async getReportMatrix(
    query: ReportMatrixQuery,
  ): Promise<ReportMatrixContract> {
    return parseReportMatrix(unwrap(await this.#api.get_report_matrix(query)));
  }

  async saveReportCells(
    request: SaveReportCellsRequest,
  ): Promise<SaveReportCellsResponse> {
    return parseSaveResponse(unwrap(await this.#api.save_report_cells(request)));
  }

  async validateImport(request: ImportRequest): Promise<ImportPreview> {
    return unwrap(await this.#api.validate_import(request)) as ImportPreview;
  }

  async commitImport(request: CommitImportRequest): Promise<CommitImportResult> {
    return unwrap(await this.#api.commit_import(request)) as CommitImportResult;
  }

  async exportReport(request: ExportRequest): Promise<ExportResult> {
    return unwrap(await this.#api.export_report(request)) as ExportResult;
  }

  async listOrganizations(): Promise<OrganizationList> {
    return unwrap(await this.#api.list_organizations({})) as OrganizationList;
  }

  async createOrganization(name: string): Promise<OrganizationResult> {
    return unwrap(await this.#api.create_organization({ name })) as OrganizationResult;
  }

  async renameOrganization(
    organizationId: string,
    name: string,
  ): Promise<OrganizationResult> {
    return unwrap(
      await this.#api.rename_organization({ organization_id: organizationId, name }),
    ) as OrganizationResult;
  }

  async archiveOrganization(organizationId: string): Promise<OrganizationList> {
    return unwrap(
      await this.#api.archive_organization({ organization_id: organizationId }),
    ) as OrganizationList;
  }

  async getReportLayout(query: ReportLayoutQuery): Promise<ReportLayoutContract> {
    return unwrap(await this.#api.get_report_layout(query)) as ReportLayoutContract;
  }

  async saveReportLayout(
    request: SaveReportLayoutRequest,
  ): Promise<ReportLayoutContract> {
    return unwrap(await this.#api.save_report_layout(request)) as ReportLayoutContract;
  }
}

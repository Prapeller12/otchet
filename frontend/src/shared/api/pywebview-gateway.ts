import type {
  ApplicationGateway, ReferenceRequest, ReportSigner, CreateReportSignerRequest,
  AccessStatus, AccessUser, WriteAuthorization, AuthorizationHandler,
  MonthlyReportQuery, ReportVerification, VerifyReportRequest,
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
  SaveReportPresentationRequest,
  ReportPresentation,
} from "./application-gateway";
import { parseReportMatrix, parseSaveResponse } from "./runtime-guards";

type BridgeEnvelope =
  | { ok: true; data: unknown }
  | { ok: false; error: { code: string; message: string } };

type PyWebViewApi = {
  get_access_status(request: Record<string, never>): Promise<BridgeEnvelope>;
  setup_access(request: { display_name?: string; pin: string; signer_id?: string }): Promise<BridgeEnvelope>;
  unlock_access(request: WriteAuthorization): Promise<BridgeEnvelope>;
  enroll_access(request: WriteAuthorization): Promise<BridgeEnvelope>;
  authenticate_access(request: WriteAuthorization): Promise<BridgeEnvelope>;
  reference_report(request: ReferenceRequest): Promise<BridgeEnvelope>;
  export_pdf(request: MonthlyReportQuery): Promise<BridgeEnvelope>;
  get_report_verification(request: MonthlyReportQuery): Promise<BridgeEnvelope>;
  list_report_signers(request: Record<string, never>): Promise<BridgeEnvelope>;
  create_report_signer(request: CreateReportSignerRequest): Promise<BridgeEnvelope>;
  verify_report(request: VerifyReportRequest): Promise<BridgeEnvelope>;
  save_report_presentation(request: SaveReportPresentationRequest): Promise<BridgeEnvelope>;
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
  #authorizationHandler: AuthorizationHandler | null = null;

  setAuthorizationHandler(handler: AuthorizationHandler | null): void { this.#authorizationHandler = handler; }
  async getAccessStatus(): Promise<AccessStatus> { return unwrap(await this.#api.get_access_status({})) as AccessStatus; }
  async setupAccess(request: { display_name?: string; pin: string; signer_id?: string }): Promise<AccessStatus> { return unwrap(await this.#api.setup_access(request)) as AccessStatus; }
  async unlockAccess(request: WriteAuthorization): Promise<AccessStatus> { return unwrap(await this.#api.unlock_access(request)) as AccessStatus; }
  async enrollAccess(request: WriteAuthorization): Promise<AccessUser> { return await this.#write("Разрешить вход пользователю", true, request, value => this.#api.enroll_access(value)) as AccessUser; }
  async authenticateAccess(request: WriteAuthorization): Promise<AccessUser> { return unwrap(await this.#api.authenticate_access(request)) as AccessUser; }
  async #write<T>(title: string, adminOnly: boolean, request: T, execute: (request: T & { authorization: WriteAuthorization }) => Promise<BridgeEnvelope>): Promise<unknown> {
    if (!this.#authorizationHandler) throw new Error("Подтверждение записи недоступно. Вернитесь к рабочему экрану.");
    return this.#authorizationHandler({ title, adminOnly }, async authorization => unwrap(await execute({ ...request, authorization })));
  }

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

  async referenceReport(request: ReferenceRequest): Promise<unknown> {
    if (request.action === "save" || request.action === "transfer") {
      return this.#write("Сохранить данные Excel", true, request, value => this.#api.reference_report(value));
    }
    return unwrap(await this.#api.reference_report(request));
  }

  async exportPdf(request: MonthlyReportQuery): Promise<ExportResult> {
    return unwrap(await this.#api.export_pdf(request)) as ExportResult;
  }
  async getReportVerification(request: MonthlyReportQuery): Promise<ReportVerification> {
    return unwrap(await this.#api.get_report_verification(request)) as ReportVerification;
  }
  async listReportSigners(): Promise<ReportSigner[]> {
    return unwrap(await this.#api.list_report_signers({})) as ReportSigner[];
  }
  async createReportSigner(request: CreateReportSignerRequest): Promise<ReportSigner> {
    return await this.#write("Создать ключ ответственного лица", true, request, value => this.#api.create_report_signer(value)) as ReportSigner;
  }
  async verifyReport(request: VerifyReportRequest): Promise<ReportVerification> {
    return unwrap(await this.#api.verify_report({ ...request, authorization: { signer_id: request.signer_id, pin: request.pin } } as VerifyReportRequest)) as ReportVerification;
  }

  async getReportMatrix(
    query: ReportMatrixQuery,
  ): Promise<ReportMatrixContract> {
    return parseReportMatrix(unwrap(await this.#api.get_report_matrix(query)));
  }

  async saveReportPresentation(request: SaveReportPresentationRequest): Promise<ReportPresentation> {
    const adminOnly = Object.keys(request).some(key => ["title", "header", "plans", "production_codes", "widths"].includes(key));
    return await this.#write("Сохранить сведения отчёта", adminOnly, request, value => this.#api.save_report_presentation(value)) as ReportPresentation;
  }

  async saveReportCells(
    request: SaveReportCellsRequest,
  ): Promise<SaveReportCellsResponse> {
    const adminOnly = request.changes.some(change => (change.coordinate.metric_code ?? "").split("_").includes("PLAN"));
    return parseSaveResponse(await this.#write("Сохранить изменения отчёта", adminOnly, request, value => this.#api.save_report_cells(value)));
  }

  async validateImport(request: ImportRequest): Promise<ImportPreview> {
    return await this.#write("Подготовить импорт Excel", true, request, value => this.#api.validate_import(value)) as ImportPreview;
  }

  async commitImport(request: CommitImportRequest): Promise<CommitImportResult> {
    return await this.#write("Применить импорт Excel", true, request, value => this.#api.commit_import(value)) as CommitImportResult;
  }

  async exportReport(request: ExportRequest): Promise<ExportResult> {
    return unwrap(await this.#api.export_report(request)) as ExportResult;
  }

  async listOrganizations(): Promise<OrganizationList> {
    return unwrap(await this.#api.list_organizations({})) as OrganizationList;
  }

  async createOrganization(name: string): Promise<OrganizationResult> {
    return await this.#write("Добавить организацию", true, { name }, value => this.#api.create_organization(value)) as OrganizationResult;
  }

  async renameOrganization(
    organizationId: string,
    name: string,
  ): Promise<OrganizationResult> {
    return await this.#write("Переименовать организацию", true, { organization_id: organizationId, name }, value => this.#api.rename_organization(value)) as OrganizationResult;
  }

  async archiveOrganization(organizationId: string): Promise<OrganizationList> {
    return await this.#write("Архивировать организацию", true, { organization_id: organizationId }, value => this.#api.archive_organization(value)) as OrganizationList;
  }

  async getReportLayout(query: ReportLayoutQuery): Promise<ReportLayoutContract> {
    return unwrap(await this.#api.get_report_layout(query)) as ReportLayoutContract;
  }

  async saveReportLayout(
    request: SaveReportLayoutRequest,
  ): Promise<ReportLayoutContract> {
    return await this.#write("Применить настройки формы", true, request, value => this.#api.save_report_layout(value)) as ReportLayoutContract;
  }
}

import type { RecoveryBackup, RecoveryResult } from "./application-gateway";
import type { ManageReportSignerRequest, ManageReportSignerResult } from "./application-gateway";
import type { UiPreferences } from "./application-gateway";
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
  SaveReportDraftRequest, SaveReportDraftResponse, ReportDraftResultQuery, ReportDraftResult,
  BlankReportProfiles, BlankReportProfilesResult,
  NetworkStatus, NetworkDataRefresh, NetworkWriteContext, NetworkOperationResult,
} from "./application-gateway";
import { isRecord, parseDraftSaveResponse, parseReportMatrix, parseSaveResponse, parseNetworkStatus, parseNetworkOperationResult } from "./runtime-guards";

type BridgeEnvelope =
  | { ok: true; data: unknown; network?: NetworkStatus }
  | { ok: false; error: { code: string; message: string; global_revision?: string; operation_id?: string }; network?: NetworkStatus };

type PyWebViewApi = {
  get_network_status?(request: Record<string, never>): Promise<BridgeEnvelope>;
  refresh_network_data?(request: NetworkDataRefresh): Promise<BridgeEnvelope>;
  get_network_operation_result?(request: { operation_id: string; authorization: WriteAuthorization | { pin: string } }): Promise<BridgeEnvelope>;
  get_network_recovery_users?(request: Record<string, never>): Promise<BridgeEnvelope>;
  unlock_network_recovery?(request: { authorization: WriteAuthorization }): Promise<BridgeEnvelope>;
  import_local_store?(request: NetworkWriteContext & { authorization: { pin: string } }): Promise<BridgeEnvelope>;
  list_blank_report_profiles(request: Record<string, never>): Promise<BridgeEnvelope>;
  create_blank_report_profiles(request: { profile_set_id: string }): Promise<BridgeEnvelope>;
  save_report_draft(request: SaveReportDraftRequest): Promise<BridgeEnvelope>;
  get_report_draft_save_result(request: ReportDraftResultQuery): Promise<BridgeEnvelope>;
  list_recovery_backups(request: Record<string, never>): Promise<BridgeEnvelope>;
  verify_recovery_backup(request: { backup_id: string }): Promise<BridgeEnvelope>;
  restore_recovery_backup(request: { backup_id: string }): Promise<BridgeEnvelope>;
  get_ui_preferences(request: Record<string, never>): Promise<BridgeEnvelope>;
  save_ui_preferences(request: UiPreferences): Promise<BridgeEnvelope>;
  get_access_status(request: Record<string, never>): Promise<BridgeEnvelope>;
  setup_access(request: { display_name?: string; pin: string; signer_id?: string }): Promise<BridgeEnvelope>;
  unlock_access(request: WriteAuthorization): Promise<BridgeEnvelope>;
  enroll_access(request: WriteAuthorization): Promise<BridgeEnvelope>;
  authenticate_access(request: WriteAuthorization): Promise<BridgeEnvelope>;
  end_administration(request: Record<string, never>): Promise<BridgeEnvelope>;
  reference_report(request: ReferenceRequest): Promise<BridgeEnvelope>;
  export_pdf(request: MonthlyReportQuery): Promise<BridgeEnvelope>;
  get_report_verification(request: MonthlyReportQuery): Promise<BridgeEnvelope>;
  list_report_signers(request: Record<string, never>): Promise<BridgeEnvelope>;
  manage_report_signer(request: ManageReportSignerRequest): Promise<BridgeEnvelope>;
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
    throw Object.assign(new Error(`${envelope.error.code}: ${envelope.error.message}`), { code: envelope.error.code, ...(["SAVE_OUTCOME_UNKNOWN", "NETWORK_LOCAL_RECOVERY_REQUIRED"].includes(envelope.error.code) ? { outcomeUnknown: true } : { outcomeKnown: true }) });
  }
  if (envelope.data && typeof envelope.data === "object") {
    const result = envelope.data as { backup_warning?: unknown; backup_complete?: unknown };
    if (typeof result.backup_warning === "string") window.dispatchEvent(new CustomEvent("backup-warning", { detail: result.backup_warning }));
    else if (result.backup_complete === true) window.dispatchEvent(new Event("backup-success"));
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
  #networkStatus: NetworkStatus | null = null;
  #unknownOperationId: string | null = null;
  #lastNetworkStatus: NetworkStatus | null = null;
  #pendingRetry: { operationId: string; title: string; adminOnly: boolean; request: Record<string, unknown>; execute: (request: Record<string, unknown> & { authorization: WriteAuthorization }) => Promise<BridgeEnvelope> } | null = null;

  #observeNetwork(status: NetworkStatus): NetworkStatus {
    this.#lastNetworkStatus = status;
    this.#networkStatus = this.#unknownOperationId ? { ...status, state: "outcome_unknown", can_write: false, pending_operation_id: this.#unknownOperationId } : status;
    window.dispatchEvent(new CustomEvent("network-status-changed", { detail: this.#networkStatus }));
    return this.#networkStatus;
  }
  #clearUnknown() {
    this.#unknownOperationId = null;
    this.#pendingRetry = null;
    if (this.#lastNetworkStatus) this.#observeNetwork(this.#lastNetworkStatus);
  }

  #unwrap(envelope: BridgeEnvelope): unknown {
    if (envelope.network) {
      this.#observeNetwork(parseNetworkStatus(envelope.network));
    }
    if (envelope.ok && isRecord(envelope.data) && typeof envelope.data.local_activation_warning === "string") window.dispatchEvent(new CustomEvent("network-activation-warning", { detail: envelope.data.local_activation_warning }));
    return unwrap(envelope);
  }

  async getNetworkStatus(): Promise<NetworkStatus> {
    if (!this.#api.get_network_status) return { mode: "local", state: "ready", loaded_global_revision: null, current_global_revision: null, snapshot_changed: false, can_write: true, message: null, pending_operation_id: null };
    const status = parseNetworkStatus(this.#unwrap(await this.#api.get_network_status({})));
    return this.#observeNetwork(status);
  }

  async refreshNetworkData(request: NetworkDataRefresh): Promise<NetworkStatus> {
    if (!this.#api.refresh_network_data) throw new Error("Обновление общего хранилища недоступно в этой сборке.");
    if (request.has_unsaved_changes || request.has_unknown_outcome || this.#unknownOperationId || this.#networkStatus?.state === "outcome_unknown") throw Object.assign(new Error("Завершите действие с черновиком перед обновлением общих данных. Введённые значения сохранены в форме."), { code: "NETWORK_REFRESH_BLOCKED", outcomeKnown: true });
    const status = parseNetworkStatus(this.#unwrap(await this.#api.refresh_network_data(request)));
    return this.#observeNetwork(status);
  }

  async getNetworkOperationResult(operationId: string): Promise<NetworkOperationResult> {
    const query = this.#api.get_network_operation_result?.bind(this.#api);
    if (!query) throw new Error("Проверка общей записи недоступна в этой сборке.");
    if (!this.#authorizationHandler) throw new Error("Подтверждение проверки недоступно.");
    const result = parseNetworkOperationResult(await this.#authorizationHandler({ title: "Проверить результат общей записи", adminOnly: false, confirmLabel: "Проверить результат" }, async authorization => this.#unwrap(await query({ operation_id: operationId, authorization }))), operationId);
    if (result.found && result.data_state !== "unknown") {
      this.#clearUnknown();
    }
    return result;
  }
  async getNetworkRecoveryUsers(): Promise<{ users: AccessUser[] }> {
    const query = this.#api.get_network_recovery_users?.bind(this.#api);
    if (!query) throw new Error("Список владельцев незавершённой записи недоступен в этой сборке.");
    const result = this.#unwrap(await query({}));
    if (!isRecord(result) || !Array.isArray(result.users) || !result.users.every(user => isRecord(user) && typeof user.id === "string" && typeof user.display_name === "string" && ["admin", "reviewer", "project_manager"].includes(String(user.role)))) throw new Error("Не удалось распознать владельцев незавершённой записи.");
    return result as { users: AccessUser[] };
  }
  async unlockNetworkRecovery(authorization: WriteAuthorization): Promise<{ unlocked: true }> {
    const unlock = this.#api.unlock_network_recovery?.bind(this.#api);
    if (!unlock) throw new Error("Открытие снимка для проверки недоступно в этой сборке.");
    const result = this.#unwrap(await unlock({ authorization }));
    if (!isRecord(result) || result.unlocked !== true) throw new Error("Не удалось открыть снимок для проверки результата.");
    return { unlocked: true };
  }
  canRetryNetworkOperation(operationId: string): boolean { return this.#unknownOperationId === operationId && this.#pendingRetry?.operationId === operationId; }
  async retryNetworkOperation(operationId: string): Promise<unknown> {
    const pending = this.#pendingRetry;
    if (!pending || !this.canRetryNetworkOperation(operationId)) throw new Error("Точный пакет предыдущей записи недоступен. Сначала проверьте результат записи.");
    return this.#write(pending.title, pending.adminOnly, pending.request, pending.execute, "Повторить тот же пакет");
  }
  async importLocalStore(pin: string): Promise<AccessStatus | { cancelled: true }> {
    const execute = this.#api.import_local_store?.bind(this.#api);
    if (!execute) throw new Error("Перенос локальных отчётов недоступен в этой сборке.");
    const request = await this.#networkRequest({ base_global_revision: null, authorization: { pin } });
    return await this.#executeWrite(request, execute) as AccessStatus | { cancelled: true };
  }
  async recoverNetworkSetupOperation(operationId: string, pin: string, signerId?: string): Promise<NetworkOperationResult> {
    const query = this.#api.get_network_operation_result?.bind(this.#api);
    if (!query) throw new Error("Проверка общей записи недоступна в этой сборке.");
    const result = parseNetworkOperationResult(this.#unwrap(await query({ operation_id: operationId, authorization: { pin, ...(signerId ? { signer_id: signerId } : {}) } })), operationId);
    if (result.found) this.#clearUnknown();
    return result;
  }

  async #networkRequest<T>(request: T): Promise<T & NetworkWriteContext> {
    if (!this.#networkStatus && this.#api.get_network_status) await this.getNetworkStatus();
    if (this.#networkStatus?.mode !== "network") return request as T & NetworkWriteContext;
    const context = request as NetworkWriteContext & { idempotency_key?: string };
    const operationId = context.operation_id ?? context.idempotency_key ?? (typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `network-${Date.now()}-${Math.random().toString(16).slice(2)}`);
    if (this.#networkStatus.state === "outcome_unknown" && this.#networkStatus.pending_operation_id !== operationId) throw Object.assign(new Error("Сначала проверьте результат предыдущей общей записи. Новый пакет не отправлен."), { code: "NETWORK_OPERATION_PENDING", outcomeKnown: true });
    return { ...request, base_global_revision: context.base_global_revision === undefined ? this.#networkStatus.loaded_global_revision : context.base_global_revision, operation_id: operationId };
  }

  #markUnknown(request: unknown, reason: unknown): Error {
    if (this.#networkStatus?.mode === "network") {
      this.#unknownOperationId = (request as NetworkWriteContext & { idempotency_key?: string }).operation_id ?? (request as { idempotency_key?: string }).idempotency_key ?? null;
      this.#observeNetwork({ ...this.#networkStatus, state: "outcome_unknown", can_write: false, pending_operation_id: this.#unknownOperationId, message: "Ответ на запись не получен. Сначала проверьте её результат." });
    }
    return Object.assign(new Error(reason instanceof Error ? reason.message : "Ответ программы не получен"), { outcomeUnknown: true, ...(reason instanceof Error && "code" in reason ? { code: reason.code } : {}) });
  }

  async #executeWrite<T>(request: T, execute: (request: T) => Promise<BridgeEnvelope>): Promise<unknown> {
    let envelope: BridgeEnvelope;
    try {
      envelope = await execute(request);
      if (!isRecord(envelope) || typeof envelope.ok !== "boolean" || (envelope.ok && !("data" in envelope))
        || (!envelope.ok && (!isRecord(envelope.error) || typeof envelope.error.code !== "string" || typeof envelope.error.message !== "string"))) throw new Error("Программа вернула неполный ответ на запись");
      if (this.#networkStatus?.mode === "network" && !envelope.network) throw new Error("Состояние общей публикации не получено.");
    } catch (reason) {
      if (this.#networkStatus?.mode !== "network") throw reason;
      throw this.#markUnknown(request, reason);
    }
    try {
      const result = this.#unwrap(envelope);
      if ((request as NetworkWriteContext).operation_id === this.#unknownOperationId) this.#unknownOperationId = null;
      if (envelope.network) this.#observeNetwork(parseNetworkStatus(envelope.network));
      return result;
    } catch (reason) {
      if (reason instanceof Error && "outcomeKnown" in reason) throw reason;
      throw this.#markUnknown(request, reason);
    }
  }

  setAuthorizationHandler(handler: AuthorizationHandler | null): void { this.#authorizationHandler = handler; }
  async listBlankReportProfiles(): Promise<BlankReportProfiles> { return this.#unwrap(await this.#api.list_blank_report_profiles({})) as BlankReportProfiles; }
  async createBlankReportProfiles(request: { profile_set_id: string }): Promise<BlankReportProfilesResult> { return await this.#write("Создать пустые отчёты с сентября 2027 года", true, request, value => this.#api.create_blank_report_profiles(value)) as BlankReportProfilesResult; }
  async listRecoveryBackups(): Promise<{ backups: RecoveryBackup[] }> { return this.#unwrap(await this.#api.list_recovery_backups({})) as { backups: RecoveryBackup[] }; }
  async verifyRecoveryBackup(backupId: string): Promise<{ valid: boolean; created_at: string }> { return this.#unwrap(await this.#api.verify_recovery_backup({ backup_id: backupId })) as { valid: boolean; created_at: string }; }
  async restoreRecoveryBackup(backupId: string): Promise<RecoveryResult> { return this.#unwrap(await this.#api.restore_recovery_backup({ backup_id: backupId })) as RecoveryResult; }
  async getUiPreferences(): Promise<UiPreferences> { return this.#unwrap(await this.#api.get_ui_preferences({})) as UiPreferences; }
  async saveUiPreferences(request: UiPreferences): Promise<UiPreferences> { return this.#unwrap(await this.#api.save_ui_preferences(request)) as UiPreferences; }
  async getAccessStatus(): Promise<AccessStatus> { return this.#unwrap(await this.#api.get_access_status({})) as AccessStatus; }
  async setupAccess(request: { display_name?: string; pin: string; signer_id?: string }): Promise<AccessStatus> { return await this.#executeWrite(await this.#networkRequest(request), value => this.#api.setup_access(value)) as AccessStatus; }
  async unlockAccess(request: WriteAuthorization): Promise<AccessStatus> { return await this.#executeWrite(await this.#networkRequest(request), value => this.#api.unlock_access(value)) as AccessStatus; }
  async enrollAccess(request: WriteAuthorization): Promise<AccessUser> { return await this.#write("Разрешить вход пользователю", true, request, value => this.#api.enroll_access(value)) as AccessUser; }
  async authenticateAccess(request: WriteAuthorization): Promise<AccessUser> { return this.#unwrap(await this.#api.authenticate_access(request)) as AccessUser; }
  async endAdministration(): Promise<void> { this.#unwrap(await this.#api.end_administration({})); }
  async #write<T>(title: string, adminOnly: boolean, request: T, execute: (request: T & { authorization: WriteAuthorization }) => Promise<BridgeEnvelope>, confirmLabel?: string): Promise<unknown> {
    if (!this.#authorizationHandler) throw new Error("Подтверждение записи недоступно. Вернитесь к рабочему экрану.");
    const prepared = await this.#networkRequest(request);
    return this.#authorizationHandler({ title, adminOnly, ...(confirmLabel ? { confirmLabel } : {}) }, async authorization => {
      try { return await this.#executeWrite({ ...prepared, authorization }, execute); }
      catch (reason) {
        if (prepared.operation_id && reason instanceof Error && "outcomeUnknown" in reason) {
          // Keep only packets without credentials. Access-key changes need fresh form input.
          const containsCode = (value: unknown): boolean => Array.isArray(value) ? value.some(containsCode) : isRecord(value) && Object.entries(value).some(([key, field]) => /pin$/i.test(key) || key === "authorization" || containsCode(field));
          if (!containsCode(prepared)) this.#pendingRetry = { operationId: prepared.operation_id, title, adminOnly, request: structuredClone(prepared) as Record<string, unknown>, execute: execute as (request: Record<string, unknown> & { authorization: WriteAuthorization }) => Promise<BridgeEnvelope> };
        }
        throw reason;
      }
    });
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
    return this.#unwrap(await this.#api.reference_report(request));
  }

  async exportPdf(request: MonthlyReportQuery): Promise<ExportResult> {
    return await this.#write("Проверить отчёт перед печатью", false, request, value => this.#api.export_pdf(value), "Подтвердить и печатать") as ExportResult;
  }
  async getReportVerification(request: MonthlyReportQuery): Promise<ReportVerification> {
    return this.#unwrap(await this.#api.get_report_verification(request)) as ReportVerification;
  }
  async listReportSigners(): Promise<ReportSigner[]> {
    return this.#unwrap(await this.#api.list_report_signers({})) as ReportSigner[];
  }
  async manageReportSigner(request: ManageReportSignerRequest): Promise<ManageReportSignerResult> {
    return await this.#write("Подтвердить изменение доступа", true, request, value => this.#api.manage_report_signer(value)) as ManageReportSignerResult;
  }
  async createReportSigner(request: CreateReportSignerRequest): Promise<ReportSigner> {
    return await this.#executeWrite(await this.#networkRequest(request), value => this.#api.create_report_signer(value)) as ReportSigner;
  }
  async verifyReport(request: VerifyReportRequest): Promise<ReportVerification> {
    return await this.#executeWrite(await this.#networkRequest({ ...request, authorization: { signer_id: request.signer_id, pin: request.pin } }), value => this.#api.verify_report(value)) as ReportVerification;
  }

  async getReportMatrix(
    query: ReportMatrixQuery,
  ): Promise<ReportMatrixContract> {
    const matrix = parseReportMatrix(this.#unwrap(await this.#api.get_report_matrix(query)));
    if (this.#networkStatus?.mode === "network" && !matrix.global_revision) throw new Error("Общая форма не содержит версию принятого снимка.");
    return matrix;
  }

  async saveReportPresentation(request: SaveReportPresentationRequest): Promise<ReportPresentation> {
    return await this.#write("Проверить и сохранить сведения отчёта", false, request, value => this.#api.save_report_presentation(value), "Сохранить и подтвердить") as ReportPresentation;
  }

  async saveReportCells(
    request: SaveReportCellsRequest,
  ): Promise<SaveReportCellsResponse> {
    return parseSaveResponse(await this.#write("Проверить и сохранить изменения отчёта", false, request, value => this.#api.save_report_cells(value), "Сохранить и подтвердить"));
  }

  async saveReportDraft(request: SaveReportDraftRequest): Promise<SaveReportDraftResponse> {
    if (!this.#api.save_report_draft) throw Object.assign(new Error("Эта сборка не поддерживает единый черновик."), { outcomeKnown: true });
    const result = await this.#write("Сохранить и подтвердить весь черновик отчёта", false, request, async value => {
      try {
        const envelope = await this.#api.save_report_draft(value);
        if (!isRecord(envelope) || typeof envelope.ok !== "boolean" || (envelope.ok && !("data" in envelope))
          || (!envelope.ok && (!isRecord(envelope.error) || typeof envelope.error.code !== "string" || typeof envelope.error.message !== "string"))) {
          throw new Error("Программа вернула неполный ответ на сохранение");
        }
        return envelope as BridgeEnvelope;
      }
      catch (reason) { throw Object.assign(new Error(reason instanceof Error ? reason.message : "Ответ программы не получен"), { outcomeUnknown: true }); }
    }, "Сохранить весь черновик");
    try {
      const saved = parseDraftSaveResponse(result);
      if (this.#networkStatus?.mode === "network" && (!saved.global_revision || saved.operation_id !== (request.operation_id ?? request.idempotency_key))) throw new Error("Подтверждение общей публикации не получено.");
      return saved;
    }
    catch (reason) { throw this.#markUnknown(request, reason); }
  }

  async getReportDraftSaveResult(request: ReportDraftResultQuery): Promise<ReportDraftResult> {
    if (!this.#authorizationHandler) throw new Error("Подтверждение проверки недоступно.");
    const result = await this.#authorizationHandler({ title: "Проверить результат предыдущего сохранения", adminOnly: false, confirmLabel: "Проверить результат" }, async authorization => this.#unwrap(await this.#api.get_report_draft_save_result({ ...request, authorization } as ReportDraftResultQuery)));
    if (isRecord(result) && result.data_state === "unknown") return { data_state: "unknown" };
    if (isRecord(result) && result.data_state === "not_committed") {
      if (!isRecord(result.error) || typeof result.error.code !== "string" || typeof result.error.message !== "string" || typeof result.global_revision !== "string" || result.operation_id !== (request.operation_id ?? request.idempotency_key)) throw new Error("Не удалось распознать результат прежней записи.");
      this.#clearUnknown();
      return result as ReportDraftResult;
    }
    const saved = parseDraftSaveResponse(result);
    if (this.#networkStatus?.mode === "network" && (!saved.global_revision || saved.operation_id !== (request.operation_id ?? request.idempotency_key))) throw new Error("Подтверждение общей публикации не получено.");
    this.#clearUnknown();
    return saved;
  }

  async validateImport(request: ImportRequest): Promise<ImportPreview> {
    return await this.#write("Подготовить импорт Excel", true, request, value => this.#api.validate_import(value)) as ImportPreview;
  }

  async commitImport(request: CommitImportRequest): Promise<CommitImportResult> {
    return await this.#write("Применить импорт Excel", true, request, value => this.#api.commit_import(value)) as CommitImportResult;
  }

  async exportReport(request: ExportRequest): Promise<ExportResult> {
    return this.#unwrap(await this.#api.export_report(request)) as ExportResult;
  }

  async listOrganizations(): Promise<OrganizationList> {
    return this.#unwrap(await this.#api.list_organizations({})) as OrganizationList;
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
    return this.#unwrap(await this.#api.get_report_layout(query)) as ReportLayoutContract;
  }

  async saveReportLayout(
    request: SaveReportLayoutRequest,
  ): Promise<ReportLayoutContract> {
    return await this.#write("Применить настройки формы", true, request, value => this.#api.save_report_layout(value)) as ReportLayoutContract;
  }
}

"""Repository contract for configurable report workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

WorkspaceKind = Literal["HEAD", "SUBSIDIARY"]
SubjectKind = Literal["product", "component"]


@dataclass(frozen=True, slots=True)
class WorkspaceOrganization:
    id: int
    name: str
    kind: WorkspaceKind


@dataclass(frozen=True, slots=True)
class WorkspaceGroupTemplate:
    template_group_id: str
    group_kind: str
    default_party_name: str
    default_position_name: str
    subject_kind: SubjectKind
    repeatable: bool


@dataclass(frozen=True, slots=True)
class WorkspaceGroup:
    id: int
    organization_id: int
    report_type: str
    template_group_id: str
    party_name: str
    position_name: str
    subject_kind: SubjectKind
    subject_id: int
    sort_order: int


@dataclass(frozen=True, slots=True)
class WorkspaceGroupDraft:
    id: int | None
    template_group_id: str
    party_name: str
    position_name: str


class ReportWorkspaceRepository(Protocol):
    def ensure_default_organization(self) -> WorkspaceOrganization: ...

    def list_organizations(self) -> tuple[WorkspaceOrganization, ...]: ...

    def create_organization(self, name: str) -> WorkspaceOrganization: ...

    def rename_organization(self, organization_id: int, name: str) -> WorkspaceOrganization: ...

    def archive_organization(self, organization_id: int) -> None: ...

    def ensure_groups(
        self,
        organization_id: int,
        report_type: str,
        templates: tuple[WorkspaceGroupTemplate, ...],
    ) -> tuple[WorkspaceGroup, ...]: ...

    def save_groups(
        self,
        organization_id: int,
        report_type: str,
        templates: tuple[WorkspaceGroupTemplate, ...],
        drafts: tuple[WorkspaceGroupDraft, ...],
    ) -> tuple[WorkspaceGroup, ...]: ...

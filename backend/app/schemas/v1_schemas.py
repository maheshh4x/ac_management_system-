"""Pydantic request schemas (Plan #23). Validation before business logic."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.auth.permissions import ROLES

ASSET_STATUSES = {
    "PLANNED",
    "PROCURED",
    "INSTALLED",
    "ACTIVE",
    "UNDER_MAINTENANCE",
    "OUT_OF_SERVICE",
    "DECOMMISSIONED",
    "DISPOSED",
    # legacy values accepted for backward compatibility
    "working",
    "not working",
    "under maintenance",
    "inactive",
    "damaged",
}
MAINT_STATUSES = {"SCHEDULED", "ASSIGNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"}
MAINT_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
MOVEMENT_STATUSES = {"PENDING", "APPROVED", "REJECTED", "CANCELLED", "COMPLETED"}


class AssetCreate(BaseModel):
    """Full UI field set (Phase 3 task 2). Ranges mirror the DB CHECKs and
    the import validators so every write path accepts the same data."""

    ac_id: str = Field(min_length=1, max_length=128)
    room_id: str | None = None
    ac_make_company: str | None = None
    ac_model_number: str | None = None
    ac_serial_number: str | None = None
    ac_capacity_ton: Decimal | None = None
    ac_type: str | None = None
    current_status: str | None = None
    installation_date: date | None = None
    last_maintenance_date: date | None = None
    manufacturing_year: int | None = Field(default=None, ge=1900, le=2100)
    installation_year: int | None = Field(default=None, ge=1900, le=2100)
    ac_star_rating: int | None = Field(default=None, ge=1, le=5)
    power_rating_kw: Decimal | None = Field(default=None, ge=0)
    power_factor: Decimal | None = Field(default=None, ge=0, le=1)
    next_service_date: date | None = None
    technician: str | None = Field(default=None, max_length=200)
    remarks: str | None = Field(default=None, max_length=2000)

    @field_validator("ac_capacity_ton")
    @classmethod
    def _positive_capacity(cls, v):
        if v is not None and v <= 0:
            raise ValueError("ac_capacity_ton must be > 0")
        return v

    @field_validator("ac_serial_number", "ac_make_company", "ac_type", "technician")
    @classmethod
    def _strip_blank_strings(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v


class AssetUpdate(BaseModel):
    room_id: str | None = None
    ac_make_company: str | None = None
    ac_model_number: str | None = None
    ac_serial_number: str | None = None
    ac_capacity_ton: Decimal | None = None
    ac_type: str | None = None
    current_status: str | None = None
    lifecycle_status: str | None = None
    installation_date: date | None = None
    last_maintenance_date: date | None = None
    remarks: str | None = None
    manufacturing_year: int | None = Field(default=None, ge=1900, le=2100)
    installation_year: int | None = Field(default=None, ge=1900, le=2100)
    ac_star_rating: int | None = Field(default=None, ge=1, le=5)
    power_rating_kw: Decimal | None = Field(default=None, ge=0)
    power_factor: Decimal | None = Field(default=None, ge=0, le=1)
    next_service_date: date | None = None
    technician: str | None = Field(default=None, max_length=200)

    @field_validator("ac_capacity_ton")
    @classmethod
    def _positive_capacity(cls, v):
        if v is not None and v <= 0:
            raise ValueError("ac_capacity_ton must be > 0")
        return v

    @field_validator("ac_serial_number", "ac_make_company", "ac_type", "technician")
    @classmethod
    def _strip_blank_strings(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v


class MaintenanceCreate(BaseModel):
    ac_id: str = Field(min_length=1)
    maintenance_type: str = Field(min_length=1)
    status: str | None = "SCHEDULED"
    priority: str | None = "MEDIUM"
    scheduled_date: date | None = None
    maintenance_date: date | None = None
    problem_description: str | None = None
    remarks: str | None = None
    # Optional assignment at creation: presence moves the job straight to
    # ASSIGNED (self-report for technicians, manager assignment otherwise).
    technician_id: str | None = None

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, v):
        if v is None:
            return v
        v = v.strip().upper()
        if v not in MAINT_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(MAINT_PRIORITIES)}")
        return v


class MaintenanceUpdate(BaseModel):
    """Content-only update — status changes go through the lifecycle
    endpoints (assign/accept/start/complete/escalate/cancel)."""

    priority: str | None = None
    work_performed: str | None = None
    remarks: str | None = None
    scheduled_date: date | None = None

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, v):
        if v is None:
            return v
        v = v.strip().upper()
        if v not in MAINT_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(MAINT_PRIORITIES)}")
        return v


class MaintenanceCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    photos: list[str] | None = None

    @field_validator("photos")
    @classmethod
    def _cap_photos(cls, v):
        if v is None:
            return v
        if len(v) > 10:
            raise ValueError("At most 10 photos per comment.")
        return [str(p) for p in v]


class MovementCreate(BaseModel):
    ac_id: str | None = None
    asset_id: str | None = None
    to_room_id: str = Field(min_length=1)
    reason: str | None = None

    @field_validator("to_room_id")
    @classmethod
    def _non_empty(cls, v):
        if not (v or "").strip():
            raise ValueError("to_room_id is required")
        return v.strip()


class MovementCommentCreate(BaseModel):
    """Phase 5: discussion on a PENDING movement request."""

    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def _strip(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("body is required")
        return v


# ── users / approvals (Phase 1.3) ────────────────────────────────────────────

PROFILE_STATUSES = {"PENDING", "APPROVED", "REJECTED"}


class ProfilePatch(BaseModel):
    """Self-service profile edit. role/status/is_active are NEVER here —
    those are approval/admin decisions only."""

    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=500)
    designation: str | None = Field(default=None, max_length=120)
    certification_no: str | None = Field(default=None, max_length=120)
    years_experience: int | None = Field(default=None, ge=0, le=70)


class RegisterProfile(BaseModel):
    """Signup self-registration. ``role`` is never accepted: the server
    assigns VIEWER; the requested role is only recorded for the approver."""

    name: str | None = Field(default=None, max_length=200)
    requested_role: str | None = None
    staff_id: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    designation: str | None = Field(default=None, max_length=120)
    reporting_manager_id: UUID | None = None

    @field_validator("requested_role")
    @classmethod
    def _valid_requested_role(cls, v):
        if v is not None and v not in ROLES:
            raise ValueError("requested_role must be a known role")
        return v


class ApprovalDecision(BaseModel):
    """Admin approver may override the final role; a manager may only ever
    grant TECHNICIAN/VIEWER (enforced in the endpoint, not just here)."""

    role: str | None = None

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v):
        if v is not None and v not in ROLES:
            raise ValueError("role must be a known role")
        return v


class RejectionDecision(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class UserAdminPatch(BaseModel):
    """Admin-only user management. Never self-applied (checked in endpoint)."""

    role: str | None = None
    is_active: bool | None = None
    reporting_manager_id: UUID | None = None

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v):
        if v is not None and v not in ROLES:
            raise ValueError("role must be a known role")
        return v


# ── campus CRUD (Phase 3.6) ─────────────────────────────────────────────────


class BlockCreate(BaseModel):
    block_name: str = Field(min_length=1, max_length=200)
    block_id: str | None = Field(default=None, max_length=64)
    college_id: str | None = Field(default=None, max_length=64)


class BlockPatch(BaseModel):
    block_name: str | None = Field(default=None, min_length=1, max_length=200)
    college_id: str | None = Field(default=None, max_length=64)


class FloorCreate(BaseModel):
    floor_name: str = Field(min_length=1, max_length=200)
    block_id: str = Field(min_length=1, max_length=64)
    floor_id: str | None = Field(default=None, max_length=64)
    floor_number: int | None = Field(default=None, ge=-5, le=200)


class FloorPatch(BaseModel):
    floor_name: str | None = Field(default=None, min_length=1, max_length=200)
    block_id: str | None = Field(default=None, max_length=64)
    floor_number: int | None = Field(default=None, ge=-5, le=200)


class DepartmentCreate(BaseModel):
    department_name: str = Field(min_length=1, max_length=200)
    department_id: str | None = Field(default=None, max_length=64)


class DepartmentPatch(BaseModel):
    department_name: str = Field(min_length=1, max_length=200)


class RoomCreate(BaseModel):
    room_name: str = Field(min_length=1, max_length=200)
    floor_id: str = Field(min_length=1, max_length=64)
    room_id: str | None = Field(default=None, max_length=64)
    department_id: str | None = Field(default=None, max_length=64)
    room_type: str | None = Field(default=None, max_length=64)


class RoomPatch(BaseModel):
    room_name: str | None = Field(default=None, min_length=1, max_length=200)
    floor_id: str | None = Field(default=None, max_length=64)
    department_id: str | None = Field(default=None, max_length=64)
    room_type: str | None = Field(default=None, max_length=64)


# ── permanent location architecture (source vs confirmed) ────────────────────


class LocationAliasCreate(BaseModel):
    """One alias: a text variant -> exactly one canonical campus entity (§6.1)."""

    entity_type: str = Field(min_length=1, max_length=32)
    entity_id: str = Field(min_length=1, max_length=64)
    alias: str = Field(min_length=1, max_length=200)

    @field_validator("entity_type")
    @classmethod
    def _known_entity_type(cls, value: str) -> str:
        allowed = ("college", "block", "floor", "department", "room")
        if value not in allowed:
            raise ValueError(f"entity_type must be one of: {', '.join(allowed)}")
        return value


class AssetLocationPatch(BaseModel):
    """Manual, per-level location confirmation (§10).

    A provided id pins that level as manually confirmed (imports can never
    auto-change it afterwards); an explicit null un-pins and clears it so the
    stored source may re-resolve; an omitted key is untouched. ``note`` is
    free-text provenance recorded in resolution_notes.
    """

    college_id: str | None = Field(default=None, max_length=64)
    block_id: str | None = Field(default=None, max_length=64)
    floor_id: str | None = Field(default=None, max_length=64)
    department_id: str | None = Field(default=None, max_length=64)
    room_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)

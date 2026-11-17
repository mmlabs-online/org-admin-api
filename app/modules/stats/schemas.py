from datetime import datetime

from pydantic import BaseModel


class UserStatusCounts(BaseModel):
    active: int
    disabled: int


class UserStats(BaseModel):
    total: int
    by_status: UserStatusCounts
    unverified: int


class OrganizationStatusCounts(BaseModel):
    active: int
    suspended: int


class OrganizationStats(BaseModel):
    total: int
    by_status: OrganizationStatusCounts


class AppStatusCounts(BaseModel):
    active: int
    disabled: int


class AppStats(BaseModel):
    total: int
    by_status: AppStatusCounts


class SignIns24h(BaseModel):
    window_start: datetime
    window_end: datetime
    succeeded: int
    failed: int


class MonthCount(BaseModel):
    month: str
    count: int


class SignUps(BaseModel):
    time_zone: str
    by_month: list[MonthCount]


class AppMembers(BaseModel):
    app_key: str
    name: str
    member_count: int


class DashboardStats(BaseModel):
    generated_at: datetime
    users: UserStats
    organizations: OrganizationStats
    apps: AppStats
    sign_ins_24h: SignIns24h
    sign_ups: SignUps
    members_per_app: list[AppMembers]

"""Domain models: what the core hands to both adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EventStatus = Literal["confirmed", "tentative", "cancelled"]


@dataclass
class Attendee:
    email: str
    name: str | None = None
    status: str = "needs-action"


@dataclass
class Event:
    uid: str
    title: str
    start_time: datetime  # always tz-aware UTC
    end_time: datetime  # always tz-aware UTC
    description: str | None = None
    all_day: bool = False
    location: str | None = None
    attendees: list[Attendee] = field(default_factory=list)
    recurrence_rule: str | None = None
    recurrence_id: datetime | None = None  # set on an expanded occurrence of a recurring event
    last_modified: datetime | None = None
    status: str = "confirmed"
    transparent: bool = False  # TRANSP:TRANSPARENT, doesn't block time
    original_timezone: str | None = None

    @property
    def recurring(self) -> bool:
        return self.recurrence_rule is not None or self.recurrence_id is not None


@dataclass(frozen=True)
class CalendarInfo:
    id: str
    name: str
    writable: bool
    timezone: str


class AttendeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(min_length=3, max_length=320, description="Attendee email address")]
    name: str | None = Field(None, max_length=200, description="Attendee display name")


class EventCreate(BaseModel):
    """Body for creating an event."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200, description="Event title, max 200 characters")
    description: str | None = Field(None, max_length=10_000, description="Free-text event description")
    start_time: datetime = Field(..., description="ISO 8601 timestamp with a timezone offset")
    end_time: datetime = Field(..., description="ISO 8601 timestamp with a timezone offset, after start_time")
    all_day: bool = Field(False, description="If true, only the dates are used")
    location: str | None = Field(None, max_length=500, description="Free-text event location")
    attendees: list[AttendeeIn] = Field(default_factory=list, max_length=100)
    recurrence_rule: str | None = Field(
        None, description="RFC 5545 RRULE value, e.g. 'FREQ=WEEKLY;BYDAY=MO,WE,FR'. Omit for a one-off event."
    )
    timezone: str | None = Field(
        None,
        description="IANA zone to store the event in (e.g. 'America/New_York'). "
        "Defaults to the calendar's configured timezone.",
    )
    status: EventStatus = "confirmed"


class EventPatch(BaseModel):
    """Body for updating an event. Every field is optional; omitted fields stay as they are."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=10_000)
    start_time: datetime | None = None
    end_time: datetime | None = None
    all_day: bool | None = None
    location: str | None = Field(None, max_length=500)
    attendees: list[AttendeeIn] | None = Field(
        None, max_length=100, description="Replaces the full attendee list if provided"
    )
    status: EventStatus | None = None

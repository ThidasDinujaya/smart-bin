# request models, validation happens here
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

ID_PATTERN = r"^[A-Za-z0-9_.-]{1,40}$"


class ReadingIn(BaseModel):
    bin_id: str = Field(pattern=ID_PATTERN, examples=["Bin-01"])
    distance_cm: float = Field(ge=0, le=1000, description="Sensor-to-waste distance")
    trigger: Literal["lid_close", "refresh", "heartbeat"] = "lid_close"
    lid_opens: int = Field(default=0, ge=0, le=10000, description="Lid openings since the last report")
    battery_pct: Optional[float] = Field(default=None, ge=0, le=100)
    command_id: Optional[int] = Field(default=None, description="Set when answering a refresh request")
    timestamp: Optional[datetime] = None


class BinIn(BaseModel):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=60)
    location: str = Field(default="", max_length=100)
    depth_cm: float = Field(default=80.0, ge=20, le=300)


class BinPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    location: Optional[str] = Field(default=None, max_length=100)
    depth_cm: Optional[float] = Field(default=None, ge=20, le=300)


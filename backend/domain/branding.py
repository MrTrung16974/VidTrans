"""Per-account branding contract: the logo a user stamps on their videos and how.

The same model validates the settings page, the snapshot copied into a logo job
when it is queued (later edits never change a running job) and the API payloads.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LOGO_POSITIONS = ("top-right", "top-left", "bottom-right", "bottom-left")
LogoPosition = Literal["top-right", "top-left", "bottom-right", "bottom-left"]
# What to do when detection is not confident: stamp the default corner, or stamp it
# and flag the job so the user confirms (and can re-run with another placement).
LowConfidenceAction = Literal["default", "review"]
# Which detected logo to replace when a video carries several at once.
MultiLogoStrategy = Literal["nearest-default", "largest", "most-confident"]
# How to remove the old logo: reconstruct the background under its strokes (OpenCV
# inpainting, follows moving logos) or blur its rectangle (FFmpeg delogo, static only).
CoverMethod = Literal["inpaint", "blur"]
# A re-run may force one placement instead of what detection found.
PlacementOverride = Literal["auto", "top-right", "top-left", "bottom-right", "bottom-left"]


class BrandingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_detect: bool = True
    default_position: LogoPosition = "top-right"
    # Logo width as a share of the video width; the height follows the logo's aspect.
    logo_size: float = Field(default=12.0, ge=3.0, le=40.0)
    opacity: float = Field(default=1.0, ge=0.1, le=1.0)
    # Gap to the frame edge as a share of the shorter video side.
    margin: float = Field(default=3.0, ge=0.0, le=15.0)
    # Remove the detected logo before stamping the new one. Only for videos the user
    # has the right to edit; the upload asks them to confirm that.
    cover_original: bool = False
    cover_method: CoverMethod = "inpaint"
    multi_logo_strategy: MultiLogoStrategy = "nearest-default"
    low_confidence_action: LowConfidenceAction = "default"
    # Detections scoring below this are treated as "not found".
    min_confidence: float = Field(default=0.55, ge=0.2, le=0.95)

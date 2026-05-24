import base64
import binascii
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    language: str = Field(default="Auto", min_length=1, max_length=50)
    code: str = Field(..., min_length=5, max_length=50000)
    focus: str = Field(default="bugs, security, performance, readability", max_length=200)
    code_encoding: Optional[str] = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def decode_code_payload(self):
        if self.code_encoding and self.code_encoding.lower() == "base64":
            try:
                self.code = base64.b64decode(self.code, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise ValueError("code must be valid UTF-8 base64 when code_encoding is base64") from exc
            self.code_encoding = None

        if len(self.code) < 5:
            raise ValueError("code must contain at least 5 characters")
        if len(self.code) > 20000:
            raise ValueError("code must contain 20000 characters or fewer after decoding")
        return self


class BugFinding(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str
    severity: str
    explanation: str
    suggested_fix: str


class ReviewResponse(BaseModel):
    summary: str
    risk_score: int = Field(..., ge=0, le=100)
    bugs: List[BugFinding]
    improvements: List[str]
    test_cases: List[str]
    fixed_code: Optional[str] = None
    used_ai: bool
    review_source: str = "fallback"
    selected_language: Optional[str] = None
    detected_language: Optional[str] = None
    reviewed_language: Optional[str] = None
    language_detection_source: Optional[str] = None
    language_detection_confidence: Optional[int] = Field(default=None, ge=0, le=100)
    language_detection_evidence: Optional[str] = None
    cache_hit: bool = False
    similarity_used: Optional[float] = None

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict, List


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRead(BaseModel):
    id: str
    email: str


class AnalyzeAcceptedResponse(BaseModel):
    analysis_id: str
    status: str
    message: str


class AnalysisStatusResponse(BaseModel):
    analysis_id: str
    status: str
    authenticity_score: Optional[float] = None
    verdict: Optional[str] = None
    ocr_text: Optional[str] = None
    extracted_fields: Optional[Dict[str, str]] = None
    suspicious_signals: Optional[List[str]] = None
    confidence: Optional[float] = None

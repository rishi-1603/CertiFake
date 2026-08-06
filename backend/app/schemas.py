from pydantic import BaseModel, Field
from typing import Optional, Dict, List

class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class AnalyzeResponse(BaseModel):
    file_name: str
    content_type: str
    authenticity_score: float
    verdict: str
    ocr_text: str
    extracted_fields: Dict[str, str]
    suspicious_signals: List[str]
    confidence: float
    report_id: Optional[str] = None
    preview_path: Optional[str] = None

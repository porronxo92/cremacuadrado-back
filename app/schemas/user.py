"""User-related schemas."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field, field_validator
import re


# =============================================================================
# Token schemas
# =============================================================================

class Token(BaseModel):
    """JWT token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """JWT token payload."""
    sub: str  # user_id
    type: str  # access or refresh
    exp: datetime


class RefreshToken(BaseModel):
    """Refresh token request."""
    refresh_token: str


# =============================================================================
# User schemas
# =============================================================================

class UserBase(BaseModel):
    """Base user schema."""
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)


class UserCreate(UserBase):
    """User registration schema."""
    password: str = Field(..., min_length=8, max_length=100)
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError('La contraseña debe tener al menos 8 caracteres')
        if not re.search(r'[A-Za-z]', v):
            raise ValueError('La contraseña debe contener al menos una letra')
        if not re.search(r'\d', v):
            raise ValueError('La contraseña debe contener al menos un número')
        return v


class UserLogin(BaseModel):
    """User login schema."""
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    """User profile update schema."""
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    marketing_opt_in: Optional[bool] = None


class UserResponse(BaseModel):
    """User response schema."""
    id: int
    email: str
    first_name: str
    last_name: str
    phone: Optional[str]
    role: str
    is_active: bool
    email_verified: bool
    marketing_opt_in: bool
    created_at: datetime
    
    model_config = {"from_attributes": True}
    
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class PasswordChange(BaseModel):
    """Password change schema."""
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=100)
    
    @field_validator('new_password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('La contraseña debe tener al menos 8 caracteres')
        return v


class ForgotPassword(BaseModel):
    """Forgot password request schema."""
    email: EmailStr


class ResetPassword(BaseModel):
    """Reset password schema."""
    token: str
    new_password: str = Field(..., min_length=8, max_length=100)


# =============================================================================
# Address schemas
# =============================================================================

class AddressBase(BaseModel):
    """Base address schema."""
    label: Optional[str] = Field(None, max_length=50)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    street: str = Field(..., min_length=1, max_length=255)
    street_2: Optional[str] = Field(None, max_length=255)
    city: str = Field(..., min_length=1, max_length=100)
    province: str = Field(..., min_length=1, max_length=100)
    postal_code: str = Field(..., min_length=4, max_length=10)
    country: str = Field(default="España", max_length=100)
    phone: str = Field(..., min_length=9, max_length=20)


class AddressCreate(AddressBase):
    """Address creation schema."""
    is_default: bool = False


class AddressUpdate(BaseModel):
    """Address update schema."""
    label: Optional[str] = Field(None, max_length=50)
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    street: Optional[str] = Field(None, min_length=1, max_length=255)
    street_2: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, min_length=1, max_length=100)
    province: Optional[str] = Field(None, min_length=1, max_length=100)
    postal_code: Optional[str] = Field(None, min_length=4, max_length=10)
    country: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, min_length=9, max_length=20)
    is_default: Optional[bool] = None


class AddressResponse(AddressBase):
    """Address response schema."""
    id: int
    is_default: bool
    created_at: datetime
    
    model_config = {"from_attributes": True}

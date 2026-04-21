"""
Authentication API endpoints.
"""
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from app.api.deps import DbSession, CurrentUser
from app.models.user import User, PasswordResetToken
from app.schemas.user import (
    UserCreate, UserLogin, UserResponse, Token, 
    ForgotPassword, ResetPassword, RefreshToken
)
from app.schemas.common import Message
from app.utils.security import (
    get_password_hash, verify_password, 
    create_access_token, create_refresh_token,
    generate_reset_token
)
from app.services.email import EmailService
from app.config import settings

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: DbSession):
    """Register a new user account."""
    # Check if email already exists
    existing_user = db.query(User).filter(User.email == user_data.email.lower()).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una cuenta con este email"
        )
    
    # Create user
    user = User(
        email=user_data.email.lower(),
        password_hash=get_password_hash(user_data.password),
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        phone=user_data.phone,
        role="customer",
    )
    
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Send welcome email
    EmailService.send_welcome_email(user.email, user.first_name)
    
    return user


@router.post("/login", response_model=Token)
async def login(credentials: UserLogin, db: DbSession):
    """Login with email and password."""
    # Find user
    user = db.query(User).filter(User.email == credentials.email.lower()).first()
    
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta desactivada"
        )
    
    # Create tokens
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)
    
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer"
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(token_data: RefreshToken, db: DbSession):
    """Refresh access token using refresh token."""
    try:
        payload = jwt.decode(
            token_data.refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        
        if user_id is None or token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de refresco inválido"
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de refresco inválido o expirado"
        )
    
    # Verify user exists and is active
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o desactivado"
        )
    
    # Create new tokens
    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)
    
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer"
    )


@router.post("/logout", response_model=Message)
async def logout():
    """
    Logout user.
    
    Note: With JWT, logout is handled client-side by removing the token.
    This endpoint is for API consistency and could be used for token blacklisting.
    """
    return Message(message="Sesión cerrada correctamente")


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: CurrentUser):
    """Get current authenticated user's profile."""
    return current_user


@router.post("/forgot-password", response_model=Message)
async def forgot_password(data: ForgotPassword, db: DbSession):
    """Request password reset email."""
    # Find user (don't reveal if email exists)
    user = db.query(User).filter(User.email == data.email.lower()).first()
    
    if user and user.is_active:
        # Generate reset token
        token = generate_reset_token()
        expires_at = datetime.utcnow() + timedelta(hours=1)
        
        # Save token
        reset_token = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=expires_at
        )
        db.add(reset_token)
        db.commit()
        
        # Send email
        EmailService.send_password_reset_email(user.email, token)
    
    # Always return success to prevent email enumeration
    return Message(message="Si el email existe, recibirás un enlace para restablecer tu contraseña")


@router.post("/reset-password", response_model=Message)
async def reset_password(data: ResetPassword, db: DbSession):
    """Reset password using token from email."""
    # Find token
    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == data.token
    ).first()
    
    if not reset_token or not reset_token.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token inválido o expirado"
        )
    
    # Update password
    user = reset_token.user
    user.password_hash = get_password_hash(data.new_password)
    
    # Mark token as used
    reset_token.used = True
    
    db.commit()
    
    return Message(message="Contraseña actualizada correctamente")

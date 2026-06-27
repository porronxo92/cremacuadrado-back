"""
Authentication API endpoints.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from jose import JWTError, jwt

from app.api.deps import DbSession, CurrentUser
from app.limiter import limiter

logger = logging.getLogger("cremacuadrado.auth")
from app.models.user import User, PasswordResetToken, EmailVerificationToken
from app.schemas.user import (
    UserCreate, UserLogin, UserResponse, Token,
    ForgotPassword, ResetPassword, RefreshToken, GoogleAuthRequest
)
from app.schemas.common import Message
from app.utils.security import (
    get_password_hash, verify_password,
    create_access_token, create_refresh_token,
    generate_reset_token
)
from app.services.email import EmailService
from app.config import settings

_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, user_data: UserCreate, db: DbSession):
    """Register a new user account."""
    existing_user = db.query(User).filter(User.email == user_data.email.lower()).first()
    if existing_user:
        logger.warning("Register rejected — email already exists: %s", user_data.email.lower())
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe una cuenta con este email"
        )

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

    # Create email verification token
    ev_token = EmailVerificationToken(
        user_id=user.id,
        token=generate_reset_token(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(ev_token)
    db.commit()

    logger.info("New user registered: email=%s", user.email)
    EmailService.send_welcome_email(user.email, user.first_name)
    EmailService.send_email_verification(user.email, user.first_name, ev_token.token)

    return user


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
async def login(request: Request, credentials: UserLogin, db: DbSession):
    """Login with email and password."""
    user = db.query(User).filter(User.email == credentials.email.lower()).first()

    if not user or not user.password_hash:
        logger.warning("Login failed — bad credentials: email=%s", credentials.email.lower())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos"
        )

    if not user.is_active:
        logger.warning("Login rejected — inactive account: email=%s", user.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta desactivada"
        )

    # Check account lockout
    locked_until = getattr(user, "locked_until", None)
    if locked_until and datetime.now(timezone.utc) < locked_until.replace(tzinfo=timezone.utc):
        remaining = int((locked_until.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 60) + 1
        logger.warning("Login rejected — account locked: email=%s", user.email)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Cuenta bloqueada temporalmente. Inténtalo de nuevo en {remaining} minutos."
        )

    if not verify_password(credentials.password, user.password_hash):
        # Increment failed attempts and possibly lock the account
        attempts = getattr(user, "failed_login_attempts", 0) + 1
        user.failed_login_attempts = attempts
        if attempts >= _MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=_LOCKOUT_MINUTES)
            user.failed_login_attempts = 0
            logger.warning("Account locked after %d attempts: email=%s", _MAX_LOGIN_ATTEMPTS, user.email)
        db.commit()
        logger.warning("Login failed — bad password: email=%s attempts=%d", user.email, attempts)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos"
        )

    # Successful login — reset lockout counters
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    logger.info("Login success: email=%s role=%s", user.email, user.role)
    token_version = getattr(user, "token_version", 0)
    access_token = create_access_token(user.id, token_version)
    refresh_token = create_refresh_token(user.id, token_version)

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer"
    )


@router.post("/google", response_model=Token)
@limiter.limit("10/minute")
async def google_auth(request: Request, data: GoogleAuthRequest, db: DbSession):
    """
    Google Identity Services login.
    Frontend sends the ID token credential received from Google.
    Backend verifies it, then creates or authenticates the user.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El login con Google no está configurado"
        )

    try:
        from app.services.google_auth import verify_google_id_token
        idinfo = verify_google_id_token(data.id_token, settings.GOOGLE_CLIENT_ID)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token de Google inválido: {exc}"
        )

    email = idinfo["email"].lower()
    google_sub = idinfo["sub"]

    # Find existing user by Google ID or email
    user = db.query(User).filter(User.google_id == google_sub).first()
    if not user:
        user = db.query(User).filter(User.email == email).first()

    if user:
        # Link Google ID if not already linked
        if not user.google_id:
            user.google_id = google_sub
            user.email_verified = True
            db.commit()
    else:
        # Create new user from Google profile
        user = User(
            email=email,
            password_hash=None,
            first_name=idinfo.get("given_name", ""),
            last_name=idinfo.get("family_name", ""),
            google_id=google_sub,
            email_verified=True,
            role="customer",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        EmailService.send_welcome_email(user.email, user.first_name)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta desactivada"
        )

    token_version = getattr(user, "token_version", 0)
    access_token = create_access_token(user.id, token_version)
    refresh_token = create_refresh_token(user.id, token_version)

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer"
    )


@router.post("/refresh", response_model=Token)
@limiter.limit("10/minute")
async def refresh_token(request: Request, token_data: RefreshToken, db: DbSession):
    """Refresh access token using refresh token."""
    try:
        payload = jwt.decode(
            token_data.refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        token_ver: int = payload.get("ver", 0)

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

    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o desactivado"
        )

    # Validate token version (rejects refresh tokens issued before last logout)
    if getattr(user, "token_version", 0) != token_ver:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión inválida. Inicia sesión de nuevo."
        )

    token_version = getattr(user, "token_version", 0)
    access_token = create_access_token(user.id, token_version)
    refresh_token_new = create_refresh_token(user.id, token_version)

    return Token(
        access_token=access_token,
        refresh_token=refresh_token_new,
        token_type="bearer"
    )


@router.post("/logout", response_model=Message)
async def logout(current_user: CurrentUser, db: DbSession):
    """
    Logout user and invalidate all existing tokens.
    Increments token_version so any previously issued JWT is rejected on next request.
    """
    current_user.token_version = getattr(current_user, "token_version", 0) + 1
    db.commit()
    return Message(message="Sesión cerrada correctamente")


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: CurrentUser):
    """Get current authenticated user's profile."""
    return current_user


@router.post("/forgot-password", response_model=Message)
@limiter.limit("5/minute")
async def forgot_password(request: Request, data: ForgotPassword, db: DbSession):
    """Request password reset email."""
    user = db.query(User).filter(User.email == data.email.lower()).first()

    if user and user.is_active:
        token = generate_reset_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        reset_token = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=expires_at
        )
        db.add(reset_token)
        db.commit()

        EmailService.send_password_reset_email(user.email, token)

    # Always return success to prevent email enumeration
    return Message(message="Si el email existe, recibirás un enlace para restablecer tu contraseña")


@router.post("/reset-password", response_model=Message)
@limiter.limit("5/minute")
async def reset_password(request: Request, data: ResetPassword, db: DbSession):
    """Reset password using token from email."""
    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == data.token
    ).first()

    if not reset_token or not reset_token.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token inválido o expirado"
        )

    user = reset_token.user
    user.password_hash = get_password_hash(data.new_password)
    # Invalidate all existing sessions after password reset
    user.token_version = getattr(user, "token_version", 0) + 1
    user.failed_login_attempts = 0
    user.locked_until = None

    reset_token.used = True
    db.commit()

    EmailService.send_security_notification(user.email, user.first_name, "restablecimiento de contraseña")
    return Message(message="Contraseña actualizada correctamente")


@router.post("/verify-email", response_model=Message)
async def verify_email(token: str, db: DbSession):
    """Verify email address using token from email."""
    ev_token = db.query(EmailVerificationToken).filter(
        EmailVerificationToken.token == token
    ).first()

    if not ev_token or not ev_token.is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enlace de verificación inválido o expirado"
        )

    ev_token.user.email_verified = True
    ev_token.used = True
    db.commit()

    return Message(message="Email verificado correctamente")

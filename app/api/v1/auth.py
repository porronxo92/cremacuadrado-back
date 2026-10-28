"""
Authentication API endpoints.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
import jwt
from jwt import PyJWTError as JWTError

from app.api.deps import DbSession, CurrentUser
from app.limiter import limiter, _get_real_ip

logger = logging.getLogger("cremacuadrado.auth")
from app.models.user import User, PasswordResetToken, EmailVerificationToken
from app.models.lead import NewsletterLead
from app.schemas.user import (
    UserCreate, UserLogin, UserResponse, Token,
    ForgotPassword, ResetPassword, RefreshToken, GoogleAuthRequest,
    EmailStatusRequest, EmailStatusResponse,
)
from app.schemas.common import Message
from app.utils.security import (
    get_password_hash, verify_password,
    create_access_token, create_refresh_token,
    generate_reset_token
)
from app.services import consents, login_throttle
from app.services.user_accounts import link_guest_orders
from app.services.legacy_customers import find_legacy_customer, grant_legacy_benefit
from app.services.email import EmailService
from app.config import settings

router = APIRouter()


def _record_login(user: User) -> None:
    """Track last access for the admin panel. Caller commits."""
    user.last_login_at = datetime.now(timezone.utc)
    user.login_count = (user.login_count or 0) + 1


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, user_data: UserCreate, db: DbSession):
    """Register a new user account."""
    if not user_data.accept_terms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debes aceptar las condiciones de uso y la política de privacidad",
        )
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
        marketing_opt_in=user_data.marketing_opt_in,
    )

    db.add(user)
    db.flush()
    consents.record_consent(db, email=user.email, purpose=consents.TERMS_REGISTER, granted=True,
                            source="register", request=request, user_id=user.id)
    if user_data.marketing_opt_in:
        consents.record_consent(db, email=user.email, purpose=consents.MARKETING, granted=True,
                                source="register", request=request, user_id=user.id)
    db.commit()
    db.refresh(user)

    # Mark any newsletter lead for this email as converted
    lead = db.query(NewsletterLead).filter(NewsletterLead.email == user.email).first()
    if lead and not lead.converted_at:
        lead.converted_at = datetime.now(timezone.utc)
        db.commit()

    # Create email verification token
    ev_token = EmailVerificationToken(
        user_id=user.id,
        token=generate_reset_token(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(ev_token)
    db.commit()

    logger.info("New user registered: email=%s", user.email)
    # Cliente de la web antigua: su bienvenida (con el cupón) llega al verificar el email
    is_legacy = find_legacy_customer(db, user.email) is not None
    if not is_legacy:
        if not EmailService.send_welcome_email(user.email, user.first_name):
            logger.error("Welcome email failed: email=%s", user.email)
        else:
            logger.info("Welcome email sent: email=%s", user.email)
    if not EmailService.send_email_verification(user.email, user.first_name, ev_token.token, legacy_benefit=is_legacy):
        logger.error("Verification email failed: email=%s", user.email)
    else:
        logger.info("Verification email sent: email=%s", user.email)

    return user


@router.post("/email-status", response_model=EmailStatusResponse)
@limiter.limit("10/minute")
async def email_status(request: Request, data: EmailStatusRequest, db: DbSession):
    """
    Checkout de invitado: indica si el email ya tiene una cuenta activa, para
    pedir al cliente que inicie sesión y el pedido quede asociado a su cuenta.
    No revela más que /register (que ya responde «Ya existe una cuenta»);
    limitado por IP para frenar la enumeración de emails.
    """
    exists = db.query(User.id).filter(
        User.email == data.email.lower(), User.is_active.is_(True)
    ).first() is not None
    return EmailStatusResponse(registered=exists)


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
async def login(request: Request, credentials: UserLogin, db: DbSession):
    """Login with email and password."""
    email = credentials.email.lower()
    ip = _get_real_ip(request)[:45]
    user = db.query(User).filter(User.email == email).first()

    # Bloqueo por (email, IP) y freno distribuido por cuenta: ver services/login_throttle.py.
    # Se comprueba antes de mirar la contraseña y también para emails inexistentes.
    remaining = login_throttle.minutes_locked(db, email, ip, user)
    if remaining:
        logger.warning("Login rejected — throttled: email=%s", email)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos. Inténtalo de nuevo en {remaining} minutos o restablece tu contraseña."
        )

    if not user or not user.password_hash or not user.is_active or not verify_password(
        credentials.password, user.password_hash
    ):
        inactive_ok = bool(user and user.password_hash and not user.is_active
                           and verify_password(credentials.password, user.password_hash))
        login_throttle.record_failure(db, email, ip, user)
        db.commit()
        if inactive_ok:
            logger.warning("Login rejected — inactive account: email=%s", email)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cuenta desactivada"
            )
        logger.warning("Login failed — bad credentials: email=%s", email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos"
        )

    # Successful login — reset throttle counters and record activity
    login_throttle.record_success(db, email, ip, user)
    _record_login(user)
    # Cuentas que verificaron su email antes de existir la vinculación
    link_guest_orders(db, user)
    db.commit()
    # Cuentas verificadas antes de cargar la lista de clientes históricos (o email fallido)
    grant_legacy_benefit(db, user)

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
            if user.password_hash and not user.email_verified:
                # Nadie demostró ser dueño de este email al registrarse: quien
                # lo hizo podría conocer la contraseña. Se anula y se cierran
                # sus sesiones (el cliente puede crear otra con «olvidé mi contraseña»).
                user.password_hash = None
                user.token_version = getattr(user, "token_version", 0) + 1
                logger.warning("Google link dropped unverified password: email=%s", user.email)
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

        lead = db.query(NewsletterLead).filter(NewsletterLead.email == user.email).first()
        if lead and not lead.converted_at:
            lead.converted_at = datetime.now(timezone.utc)
            db.commit()

        # Cliente de la web antigua: su bienvenida especial la envía grant_legacy_benefit más abajo
        if not find_legacy_customer(db, user.email):
            if not EmailService.send_welcome_email(user.email, user.first_name):
                logger.error("Google welcome email failed: email=%s", user.email)
            else:
                logger.info("Google welcome email sent: email=%s", user.email)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cuenta desactivada"
        )

    _record_login(user)
    # Google garantiza el email verificado (se marcó arriba): vincula pedidos de invitado
    link_guest_orders(db, user)
    db.commit()
    grant_legacy_benefit(db, user)

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
    return Message(message="Sesión cerrada")


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: CurrentUser):
    """Get current authenticated user's profile."""
    return current_user


@router.post("/forgot-password", response_model=Message)
@limiter.limit("5/minute")
async def forgot_password(request: Request, data: ForgotPassword, db: DbSession):
    """Request password reset email."""
    user = db.query(User).filter(User.email == data.email.lower()).first()

    recent = user and db.query(PasswordResetToken.id).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.created_at > datetime.utcnow() - timedelta(minutes=2),
    ).first()
    if user and user.is_active and not recent:
        token = generate_reset_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        reset_token = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=expires_at
        )
        db.add(reset_token)
        db.commit()

        if not EmailService.send_password_reset_email(user.email, token):
            logger.error("Password reset email failed: email=%s", user.email)
        else:
            logger.info("Password reset email sent: email=%s", user.email)

    # Always return success to prevent email enumeration
    return Message(message="Si ese email tiene cuenta, te enviaremos un enlace para restablecer la contraseña.")


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
    login_throttle.clear(db, user.email)

    reset_token.used = True
    db.commit()

    if not EmailService.send_security_notification(user.email, user.first_name, "restablecimiento de contraseña"):
        logger.error("Security notification email failed: email=%s event=password_reset", user.email)
    else:
        logger.info("Security notification sent: email=%s event=password_reset", user.email)
    return Message(message="Contraseña actualizada")


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
    # Email demostrado: ahora sí se vinculan los pedidos de invitado con ese email
    link_guest_orders(db, ev_token.user)
    db.commit()
    grant_legacy_benefit(db, ev_token.user)

    return Message(message="Email verificado")

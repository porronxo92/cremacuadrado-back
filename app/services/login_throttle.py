"""
Freno a la fuerza bruta en el login.

Diseño (sustituye al bloqueo «5 fallos → cuenta bloqueada 15 min» de cualquiera):
- Bloqueo por pareja (email, IP): 5 fallos seguidos desde una IP bloquean solo esa
  IP para ese email durante 15 min. Un tercero ya no puede dejar fuera al titular
  desde su propia IP.
- Freno a la fuerza bruta distribuida: `users.failed_login_attempts` cuenta los
  fallos de todas las IPs; al llegar a ACCOUNT_THRESHOLD la cuenta se bloquea
  ACCOUNT_LOCK_MINUTES para IPs desconocidas. Una IP donde el titular ya inició
  sesión (`last_success_at`) sigue pudiendo entrar con la contraseña correcta,
  y «olvidé mi contraseña» (que requiere el correo) lo levanta siempre.
- Se aplica también a emails que no existen, para no revelar qué cuentas existen.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.user import LoginAttempt, User

PAIR_MAX_FAILURES = 5
PAIR_LOCK_MINUTES = 15
ACCOUNT_THRESHOLD = 25
ACCOUNT_LOCK_MINUTES = 15


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row(db: Session, email: str, ip: str, create: bool = False) -> LoginAttempt | None:
    row = db.get(LoginAttempt, (email, ip))
    if row is None and create:
        row = LoginAttempt(email=email, ip=ip, failures=0, updated_at=_now())
        db.add(row)
    return row


def minutes_locked(db: Session, email: str, ip: str, user: User | None) -> int:
    """Minutos restantes de bloqueo para este intento (0 = puede intentarlo)."""
    now = _now()
    row = _row(db, email, ip)
    if row and row.locked_until and row.locked_until > now:
        return int((row.locked_until - now).total_seconds() / 60) + 1
    locked = getattr(user, "locked_until", None)
    if locked and locked > now and not (row and row.last_success_at):
        return int((locked - now).total_seconds() / 60) + 1
    return 0


def record_failure(db: Session, email: str, ip: str, user: User | None) -> None:
    """Registra un fallo. No hace commit."""
    now = _now()
    row = _row(db, email, ip, create=True)
    row.failures = (row.failures or 0) + 1
    row.updated_at = now
    if row.failures >= PAIR_MAX_FAILURES:
        row.locked_until = now + timedelta(minutes=PAIR_LOCK_MINUTES)
        row.failures = 0
    if user is not None:
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= ACCOUNT_THRESHOLD:
            user.locked_until = now + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
            user.failed_login_attempts = 0


def record_success(db: Session, email: str, ip: str, user: User) -> None:
    """Limpia contadores y marca la IP como conocida. No hace commit."""
    row = _row(db, email, ip, create=True)
    row.failures = 0
    row.locked_until = None
    row.last_success_at = row.updated_at = _now()
    user.failed_login_attempts = 0
    user.locked_until = None


def clear(db: Session, email: str) -> None:
    """Levanta los bloqueos de un email (restablecer contraseña, desbloqueo por el admin). No hace commit."""
    db.query(LoginAttempt).filter(LoginAttempt.email == email.lower()).delete(synchronize_session=False)


def client_ip(request) -> str:
    """IP del cliente (X-Forwarded-For lo fija Vercel) recortada al ancho de la columna."""
    from app.limiter import _get_real_ip
    return _get_real_ip(request)[:45]

"""
Google Identity Services token verification.
Verifies the ID token received from the frontend after the user authenticates with Google.
"""
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests


def verify_google_id_token(token: str, client_id: str) -> dict:
    """
    Verify a Google ID token and return the decoded claims.

    Returns a dict with keys: sub, email, email_verified, given_name, family_name, picture.
    Raises ValueError if the token is invalid or issued for a different client.
    """
    request = google_requests.Request()
    idinfo = google_id_token.verify_oauth2_token(token, request, client_id)

    if idinfo.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("Token de Google con emisor no válido")

    if not idinfo.get("email_verified", False):
        raise ValueError("El email de Google no está verificado")

    return idinfo

"""
Correos Labels API — generates shipping label PDFs for preregistered shipments.

API spec: POST {CORREOS_API_BASE}/support/labels/api/v1/labels/print
Swagger: backend/docs/correos-api/labels_pub.json

Returns a PDF in base64. The caller (admin endpoint) can decode it and serve
it directly or upload it to Vercel Blob for persistent storage.
"""
import base64
import logging

import httpx

from app.config import settings
from app.services.correos.auth import get_auth_headers

logger = logging.getLogger("cremacuadrado.correos")

_TIMEOUT = httpx.Timeout(30.0)


def _labels_url() -> str:
    return f"{settings.CORREOS_API_BASE}/support/labels/api/v1/labels/print"


def get_label_pdf(package_code: str) -> bytes:
    """
    Request a shipping label PDF from Correos for one package.

    Args:
        package_code: The packageCode returned by preregister (e.g. "PQ...").

    Returns:
        Raw PDF bytes ready to serve or store.

    Raises:
        ValueError: If Correos returns an error or no PDF.
        httpx.HTTPStatusError: On HTTP-level errors.
    """
    if not settings.CORREOS_ENABLED:
        logger.info("Correos mock label: %s", package_code)
        # Return a minimal valid PDF for testing
        return _mock_pdf(package_code)

    payload = {
        "application": "CREMACUADRADO",
        "documentationType": 0,  # 0 = all (label + customs if applicable)
        "print": {
            "shipments": [package_code],
            "labelFormat": 2,           # 2 = PDF
            "labelPrintMode": 2,        # 2 = labeler (single label)
            "labelPrintInitialPosition": 1,
        },
    }

    headers = get_auth_headers()
    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        resp = client.post(_labels_url(), json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    error = data.get("error", "")
    if error:
        raise ValueError(f"Correos labels API error: {error}")

    pdf_b64 = data.get("pdf")
    if not pdf_b64:
        raise ValueError(f"Correos labels API returned no PDF for {package_code}")

    pdf_bytes = base64.b64decode(pdf_b64)
    logger.info("Correos label PDF generated: %s (%d bytes)", package_code, len(pdf_bytes))
    return pdf_bytes


def get_labels_pdf_multi(package_codes: list[str]) -> bytes:
    """
    Request a single combined PDF with labels for multiple packages.

    All labels are unified in one PDF document.
    """
    if not settings.CORREOS_ENABLED:
        logger.info("Correos mock multi-label: %s", package_codes)
        return _mock_pdf(",".join(package_codes))

    payload = {
        "application": "CREMACUADRADO",
        "documentationType": 0,
        "print": {
            "shipments": package_codes,
            "labelFormat": 2,
            "labelPrintMode": 1,        # 1 = A4 format for multi
            "labelPrintInitialPosition": 1,
        },
    }

    headers = get_auth_headers()
    with httpx.Client(timeout=_TIMEOUT, verify=settings.CORREOS_VERIFY_SSL) as client:
        resp = client.post(_labels_url(), json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    error = data.get("error", "")
    if error:
        raise ValueError(f"Correos labels API error: {error}")

    pdf_b64 = data.get("pdf")
    if not pdf_b64:
        raise ValueError(f"Correos labels API returned no PDF for {package_codes}")

    pdf_bytes = base64.b64decode(pdf_b64)
    logger.info("Correos multi-label PDF generated: %d packages (%d bytes)", len(package_codes), len(pdf_bytes))
    return pdf_bytes


def _mock_pdf(reference: str) -> bytes:
    """Generate a minimal mock PDF for testing without Correos connection."""
    # Minimal valid PDF with a text showing the reference
    content = f"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 283 425]/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 120>>stream
BT /F1 14 Tf 20 380 Td (ETIQUETA MOCK) Tj 0 -30 Td /F1 10 Tf ({reference}) Tj 0 -20 Td (CremaCuadrado - Prueba) Tj ET
endstream endobj
xref 0 6
trailer<</Size 6/Root 1 0 R>>
startxref 0
%%EOF"""
    return content.encode("latin-1")

"""Vercel Cron llama a la URL única del despliegue: debe aceptarse sin abrir el filtro de hosts."""
from app.config import trusted_hosts

PROD = ["cremacuadrado-back.vercel.app", "api.cremacuadrado.com"]
DEPLOY = "cremacuadrado-back-chd9d5144-porronxo92s-projects.vercel.app"


def test_adds_deployment_url():
    assert DEPLOY in trusted_hosts(PROD, DEPLOY)


def test_keeps_configured_hosts_and_skips_empty_or_duplicate():
    assert trusted_hosts(PROD, "", "") == PROD
    assert trusted_hosts(PROD, PROD[0]) == PROD


def test_wildcard_is_left_alone():
    assert trusted_hosts(["*"], DEPLOY) == ["*"]

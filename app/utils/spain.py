"""
Provincias de España (código INE = dos primeros dígitos del código postal) y
validación de teléfonos con prefijo. Debe coincidir con
frontend/src/app/core/data/spain.ts.
"""
import re
from typing import Optional

# código INE → nombre oficial (forma usada por Correos)
PROVINCES: dict[str, str] = {
    "01": "Álava", "02": "Albacete", "03": "Alicante", "04": "Almería", "05": "Ávila",
    "06": "Badajoz", "07": "Baleares", "08": "Barcelona", "09": "Burgos", "10": "Cáceres",
    "11": "Cádiz", "12": "Castellón", "13": "Ciudad Real", "14": "Córdoba", "15": "A Coruña",
    "16": "Cuenca", "17": "Girona", "18": "Granada", "19": "Guadalajara", "20": "Gipuzkoa",
    "21": "Huelva", "22": "Huesca", "23": "Jaén", "24": "León", "25": "Lleida",
    "26": "La Rioja", "27": "Lugo", "28": "Madrid", "29": "Málaga", "30": "Murcia",
    "31": "Navarra", "32": "Ourense", "33": "Asturias", "34": "Palencia", "35": "Las Palmas",
    "36": "Pontevedra", "37": "Salamanca", "38": "Santa Cruz de Tenerife", "39": "Cantabria",
    "40": "Segovia", "41": "Sevilla", "42": "Soria", "43": "Tarragona", "44": "Teruel",
    "45": "Toledo", "46": "Valencia", "47": "Valladolid", "48": "Bizkaia", "49": "Zamora",
    "50": "Zaragoza", "51": "Ceuta", "52": "Melilla",
}
NAME_TO_CODE = {name.lower(): code for code, name in PROVINCES.items()}

def province_code(name: str) -> Optional[str]:
    return NAME_TO_CODE.get((name or "").strip().lower())


def validate_province_postcode(province: str, postal_code: str) -> None:
    """Lanza ValueError si la provincia no existe o no casa con el código postal."""
    code = province_code(province)
    if code is None:
        raise ValueError("Selecciona una provincia de la lista")
    cp = (postal_code or "").strip()
    if not re.fullmatch(r"\d{5}", cp):
        raise ValueError("El código postal debe tener 5 dígitos")
    if cp[:2] != code:
        raise ValueError(f"El código postal {cp} no corresponde a la provincia de {PROVINCES[code]}")


def normalize_phone(value: str) -> str:
    """
    Teléfono con prefijo internacional, formato "+34 600123456".
    El frontend envía "<prefijo> <número>"; sin prefijo se asume +34.
    Para España exige 9 dígitos empezando por 6, 7, 8 o 9.
    """
    text = (value or "").strip()
    if text.startswith("00"):
        text = "+" + text[2:]
    match = re.fullmatch(r"\+(\d{1,3})\s+([\d\s\-]+)", text)
    if match:
        prefix, number = match.group(1), re.sub(r"\D", "", match.group(2))
    else:
        digits = re.sub(r"\D", "", text)
        if text.startswith("+34") or not text.startswith("+"):
            prefix, number = "34", digits[2:] if text.startswith("+34") else digits
        else:
            raise ValueError("Indica el prefijo y el número por separado")
    if prefix == "34":
        if not re.fullmatch(r"[6789]\d{8}", number):
            raise ValueError("Introduce un teléfono español válido de 9 dígitos")
    elif not re.fullmatch(r"\d{6,14}", number):
        raise ValueError("Introduce un teléfono válido")
    return f"+{prefix} {number}"


def canonical_province(province: str, postal_code: str) -> str:
    """Valida provincia + CP y devuelve el nombre oficial de la provincia."""
    validate_province_postcode(province, postal_code)
    return PROVINCES[province_code(province)]


def optional_phone(value: Optional[str]) -> Optional[str]:
    """Para campos de teléfono opcionales: vacío → None; si hay valor, se normaliza."""
    if value is None or not str(value).strip():
        return None
    return normalize_phone(value)

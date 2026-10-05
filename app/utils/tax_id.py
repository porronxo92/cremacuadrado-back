"""Validación de identificadores fiscales españoles (NIF, NIE y CIF)."""
import re

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIF_RE = re.compile(r"^(\d{8})([A-Z])$")
_NIE_RE = re.compile(r"^([XYZ])(\d{7})([A-Z])$")
_CIF_RE = re.compile(r"^([ABCDEFGHJNPQRSUVW])(\d{7})([0-9A-J])$")


def normalize_tax_id(value: str) -> str:
    return re.sub(r"[\s\-.]", "", value or "").upper()


def _valid_cif(letter: str, digits: str, control: str) -> bool:
    even = sum(int(d) for d in digits[1::2])
    odd = sum(sum(divmod(int(d) * 2, 10)) for d in digits[0::2])
    check = (10 - (even + odd) % 10) % 10
    check_letter = "JABCDEFGHI"[check]
    if letter in "PQRSNW":        # siempre letra
        return control == check_letter
    if letter in "ABEH":          # siempre número
        return control == str(check)
    return control in (str(check), check_letter)


def is_valid_tax_id(value: str) -> bool:
    """True si *value* es un NIF, NIE o CIF español con dígito de control correcto."""
    tid = normalize_tax_id(value)
    if m := _NIF_RE.match(tid):
        return _DNI_LETTERS[int(m.group(1)) % 23] == m.group(2)
    if m := _NIE_RE.match(tid):
        number = "XYZ".index(m.group(1)) * 10_000_000 + int(m.group(2))
        return _DNI_LETTERS[number % 23] == m.group(3)
    if m := _CIF_RE.match(tid):
        return _valid_cif(*m.groups())
    return False

"""Journaux JSON structurés avec identifiant de requête et masquage des secrets.

Conventions techniques §6 et prompt F-00 point 11 :
- Masquage obligatoire : mot de passe, jeton, cookie, signature, code de remise,
  téléphone complet (+2376XXXXXXXX -> +2376XXXXX12).
- Identifiant de requête généré et propagé via contexte et en-tête X-Requete-Id.
"""

import logging
import re
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from django.http import HttpRequest, HttpResponse

try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover
    from pythonjsonlogger.jsonlogger import JsonFormatter

__all__ = [
    "masquer",
    "FiltreMasquage",
    "FormateurJson",
    "FormateurJsonMasque",
    "MiddlewareIdentifiantRequete",
    "requete_id_courant",
]

requete_id_courant: ContextVar[str] = ContextVar("requete_id", default="-")

_CLE_SENSIBLE = (
    r"(?:mot[\s_-]?de[\s_-]?passe|password|jwt[\s_-]?secret|refresh[\s_-]?token|"
    r"access[\s_-]?token|token|jeton(?:[\s_-]?(?:d[\s_-]?acces|acces|de[\s_-]?rafraichissement))?|"
    r"cookie|authorization|signature|code[\s_-]?remise|secret)"
)

_RE_QUOTE = re.compile(rf'("{_CLE_SENSIBLE}"\s*:\s*["\'])[^"\']*(["\'])', re.IGNORECASE)
_RE_UNQUOTED_KEY_QUOTE_VAL = re.compile(
    rf"((?<![\w-]){_CLE_SENSIBLE}\s*[:=]\s*[\"'])[^\"']*(['\"])", re.IGNORECASE
)
_RE_AFFECTION = re.compile(
    rf"((?<![\w-]){_CLE_SENSIBLE}\s*[:=]\s*)((?!Bearer\s)[^\s,\"';&}}]+)", re.IGNORECASE
)
_RE_BEARER = re.compile(r"(?i)\b(bearer\s+)[a-z0-9._\-]+")
_RE_TELEPHONE = re.compile(r"(?<!\d)(\+237\d{9})(?!\d)")


def _masquer_telephone(correspondance: re.Match[str]) -> str:
    """+2376XXXXX33 : premier chiffre et deux derniers visibles, 5 du milieu masqués."""
    numero = correspondance.group(1)
    return f"{numero[:5]}XXXXX{numero[-2:]}"


def masquer(texte: str) -> str:
    """Remplace secrets et téléphones complets par des formes masquées."""
    texte = _RE_QUOTE.sub(r"\1***\2", texte)
    texte = _RE_UNQUOTED_KEY_QUOTE_VAL.sub(r"\1***\2", texte)
    texte = _RE_AFFECTION.sub(r"\1***", texte)
    texte = _RE_BEARER.sub(r"\1***", texte)
    texte = _RE_TELEPHONE.sub(_masquer_telephone, texte)
    return texte


class FiltreMasquage(logging.Filter):
    """Filtre de logging masquant le message et les arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = masquer(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (masquer(v) if isinstance(v, str) else v) for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(masquer(a) if isinstance(a, str) else a for a in record.args)
        if record.exc_text:
            record.exc_text = masquer(record.exc_text)
        return True


class FormateurJson(JsonFormatter):
    """Formateur JSON structuré sur une seule ligne."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            json_ensure_ascii=False,
            reserved_attrs=(
                "asctime",
                "levelname",
                "name",
                "module",
                "funcName",
                "lineno",
                "pathname",
                "filename",
                "exc_info",
                "exc_text",
                "stack_info",
                "taskName",
                "msecs",
                "msg",
                "args",
            ),
        )

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["niveau"] = record.levelname
        log_record["horloge"] = (
            self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs * 1000):06d}Z"
        )
        log_record["logger"] = record.name
        log_record["requete_id"] = requete_id_courant.get()
        for cle_anglaise in ("asctime", "levelname", "name"):
            log_record.pop(cle_anglaise, None)


class FormateurJsonMasque(FormateurJson):
    """Deuxième ligne de défense : masque le JSON final produit."""

    def format(self, record: logging.LogRecord) -> str:
        return masquer(super().format(record))


class MiddlewareIdentifiantRequete:
    """Middleware Django pour associer un identifiant de requête à chaque appel."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        identifiant = request.headers.get("X-Requete-Id") or str(uuid.uuid4())
        request.requete_id = identifiant  # type: ignore[attr-defined]
        jeton = requete_id_courant.set(identifiant)
        try:
            response = self.get_response(request)
        finally:
            requete_id_courant.reset(jeton)
        response["X-Requete-Id"] = identifiant
        return response

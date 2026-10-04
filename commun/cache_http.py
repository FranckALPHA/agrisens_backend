"""Mixin de cache HTTP pour les endpoints publics (F-15, SPEC-12).

Applique un `Cache-Control` court et un `ETag` basé sur le contenu sérialisé.
`ConditionalGetMiddleware` (activé dans settings) gère automatiquement
`If-None-Match` → `304 Not Modified`.
"""

import hashlib
import json

from rest_framework.response import Response


class CachePublicMixin:
    """Mixin pour APIView : ajoute ETag + Cache-Control sur GET 200 public.

    Arguments de classe :
        cache_max_age (int) : secondes, défaut 60.
    """

    cache_max_age: int = 60

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if (
            request.method == "GET"
            and response.status_code == 200
            and isinstance(response, Response)
            and not response.has_header("ETag")
        ):
            contenu = self._serialiser_contenu(response)
            if contenu:
                etag = '"' + hashlib.md5(contenu).hexdigest() + '"'
                response["ETag"] = etag
                response["Cache-Control"] = f"public, max-age={self.cache_max_age}, must-revalidate"
        return response

    @staticmethod
    def _serialiser_contenu(response: Response) -> bytes | None:
        """Retourne le corps encodé en bytes pour hashing, ou None."""
        try:
            return json.dumps(response.data, sort_keys=True, default=str).encode()
        except (TypeError, ValueError):
            return None

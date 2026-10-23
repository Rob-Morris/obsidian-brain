"""Versioned navigation values, bound to a result and its authenticated session.

These tokens carry coordinates, not disclosure authority. A credential holder can
construct them; the delivery owner must still check storage and current access.
"""
import base64
import hashlib
import hmac
import json
import re


_FIELDS = ('view_id', 'page', 'origin_page', 'start_page', 'max_pages',
           'page_size', 'page_unit', 'detail')
_RECOVERY = ('Continuation is invalid for this result/session. Pass the returned token unchanged, '
             'or omit continuation and restart this result with page 0. Do not replay a mutation.')


class ResultCursor:
    def __init__(self, credential: str):
        self._key = hashlib.sha256(b'brain-result-cursor-v1\0' + credential.encode()).digest()

    def _mac(self, result_id: str, payload: bytes) -> bytes:
        return hmac.new(self._key, result_id.encode() + b'\0' + payload, hashlib.sha256).digest()[:16]

    def encode(self, result_id: str, coordinates: dict) -> str:
        if coordinates.get('result_id') != result_id or set(coordinates) - {'result_id', 'selection_id', *_FIELDS}:
            raise ValueError('Internal continuation coordinates are inconsistent.')
        mask = sum(1 << index for index, field in enumerate(_FIELDS) if field in coordinates)
        payload = json.dumps([mask, [coordinates[field] for field in _FIELDS if field in coordinates]],
                             ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        return 't1_' + base64.urlsafe_b64encode(self._mac(result_id, payload) + payload).decode().rstrip('=')

    def decode(self, result_id: str, token: str | None) -> dict:
        if token is None:
            return {}
        if not isinstance(token, str) or not re.fullmatch(r't1_[A-Za-z0-9_-]{24,4096}', token):
            raise ValueError(_RECOVERY)
        try:
            encoded = token[3:]
            blob = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
            payload = blob[16:]
            if (base64.urlsafe_b64encode(blob).decode().rstrip('=') != encoded
                    or not hmac.compare_digest(blob[:16], self._mac(result_id, payload))):
                raise ValueError('binding')
            mask, values = json.loads(payload)
            if (type(mask) is not int or not 0 < mask < 1 << len(_FIELDS)
                    or not isinstance(values, list) or len(values) != mask.bit_count()):
                raise ValueError('shape')
            fields = [field for index, field in enumerate(_FIELDS) if mask & (1 << index)]
            return {'result_id': result_id, **dict(zip(fields, values))}
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError(_RECOVERY) from None

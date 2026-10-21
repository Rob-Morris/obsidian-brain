"""Pure source-warning preservation for bounded outcome projections."""

from hashlib import sha256
import json


def source_outcome_warnings(warnings):
    """Keep small warnings intact; summarise oversized values without echoing them."""
    warnings = warnings if isinstance(warnings, list) else []
    canonical = json.dumps(warnings, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(canonical) <= 1600 and len(json.dumps(warnings).encode("utf-8")) <= 5000:
        return warnings
    return [{"code": "source_warnings_retained", "message": (
        "Source warnings could not fit this response. Where retained, read the original "
        "warnings using result.read with result_id and detail=@outcome. "
        "Do not replay the command to recover output."),
        "count": len(warnings), "sha256": sha256(canonical).hexdigest()}]

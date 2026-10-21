"""Fixed capacity recovery text shared by result writers without application imports."""


def capacity_recovery(limits):
    """Accept only exact bounded byte-limit primitives; never copy upstream text."""
    names = ('result_bytes', 'owner_bytes', 'brain_bytes')
    valid = (isinstance(limits, dict) and set(limits) == set(names)
             and all(type(limits[name]) is int and 1 <= limits[name] <= 2**63 - 1 for name in names))
    values = {name: limits[name] for name in names} if valid else None
    description = (f"Configured byte limits: result={values['result_bytes']}, "
                   f"owner={values['owner_bytes']}, Brain={values['brain_bytes']}. "
                   if values is not None else 'Configured byte limits are unavailable in this response. ')
    return values, ('Private result capacity exceeded. ' + description
        + 'Recover needed retained output before clearing: where available, use result.read with result_id '
        'and detail=@outcome. Clearing permanently removes that output. '
        'To discard only the owning namespace, run result.clear with its existing result-session credential '
        '(CLI: brain result clear --result-session-file <owning-private-file>). '
        'Clearing cannot make an intrinsically oversized result fit its per-result limit; '
        'that may require an explicitly authorised capacity change. '
        'Other owners can clear their own namespaces. For retired bindings or lost credentials, '
        'stop affected clients and ask an authorised local operator to approve cleanup of the exact '
        'retired or lost namespace. Do not automatically clear the sole retained outcome.')


def representation_recovery():
    """Describe bounded outcome recovery without treating layout size as quota exhaustion."""
    return ('The result page or metadata exceeds its representation limit. Where retained, '
            'call result.read with result_id and detail=@outcome to recover the source outcome. '
            'Clearing retained output does not fix a representation limit. '
            'Do not rerun a mutation to recover output.')

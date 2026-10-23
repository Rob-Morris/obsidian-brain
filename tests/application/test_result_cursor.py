"""Opaque navigation is stateless, versioned and bound without granting access."""
import base64
import hashlib
import hmac
import json

import pytest

from _command_interface.result_cursor import ResultCursor


@pytest.mark.parametrize('coordinates', [
    {'view_id': 'bytes:4000', 'page': 2, 'origin_page': 1, 'start_page': 1,
     'max_pages': None, 'page_size': 4000, 'page_unit': 'bytes'},
    {'detail': 'child:2', 'page_unit': 'fields', 'page_size': 25},
    {'view_id': 'detailview:abc', 'page': 3, 'origin_page': 2, 'start_page': 1,
     'max_pages': 10, 'page_size': 100, 'page_unit': 'lines', 'detail': '@outcome'},
])
def test_cursor_round_trip_across_instances_without_persistence(coordinates):
    cursor = ResultCursor('credential')
    expected = {'result_id': 'result', **coordinates}
    token = cursor.encode('result', {**expected, 'selection_id': 'derived-not-needed'})
    assert token.startswith('t1_')
    assert ResultCursor('credential').decode('result', token) == expected
    assert cursor.encode('result', expected) == token


def test_token_binding_and_invalid_recovery():
    codec = ResultCursor('owner-one')
    token = codec.encode('result-one', {'result_id': 'result-one', 'page': 2})
    for reader, result, value in [
        (codec, 'result-two', token),
        (ResultCursor('owner-two'), 'result-one', token),
        (codec, 'result-one', token[:-1] + ('A' if token[-1] != 'A' else 'B')),
        (codec, 'result-one', token + '='),
        (codec, 'result-one', token.replace('t1_', 't2_')),
        (codec, 'result-one', 't1_' + 'x' * 4097),
        (codec, 'result-one', {}),
        (codec, 'result-one', ''),
    ]:
        with pytest.raises(ValueError, match='Pass the returned token unchanged'):
            reader.decode(result, value)
    assert codec.decode('result-one', None) == {}


@pytest.mark.parametrize('payload', [[], [0, []], [True, [1]], [256, [1]],
                                     [1, []], [1, {}], [1, [1], 'extra']])
def test_authenticated_invalid_shape_is_rejected(payload):
    # The caller knows its credential: MAC binding never replaces validation.
    raw = json.dumps(payload).encode()
    key = hashlib.sha256(b'brain-result-cursor-v1\0credential').digest()
    tag = hmac.new(key, b'result\0' + raw, hashlib.sha256).digest()[:16]
    token = 't1_' + base64.urlsafe_b64encode(tag + raw).decode().rstrip('=')
    with pytest.raises(ValueError, match='Continuation is invalid'):
        ResultCursor('credential').decode('result', token)


def test_internal_coordinates_cannot_silently_disappear():
    codec = ResultCursor('credential')
    for coordinates in ({'result_id': 'other', 'page': 1},
                        {'result_id': 'result', 'new_coordinate': 1}):
        with pytest.raises(ValueError, match='Internal continuation'):
            codec.encode('result', coordinates)

"""Retained literal search progresses through every matching bounded unit."""
from types import SimpleNamespace

import pytest

from _application.consent import ConsentError
from _application.result.search import ResultSearchRequest, decode, execute
from _application.result_navigation import body_fits, build_view
from _command_interface.result_delivery import ResultDeliverySession
from _command_interface.result_store import FileResultStore, ResultStoreError


@pytest.fixture
def retained(tmp_path, monkeypatch):
    root = tmp_path / 'Brain'
    root.mkdir()
    store = FileResultStore(root, 'brain', 'principal')
    session = ResultDeliverySession(store, store.issue())
    service = SimpleNamespace(check_result_delivery=lambda *args: None)
    context = SimpleNamespace(authorisation=SimpleNamespace(service=service), result_delivery=session)
    def no_full_body(*args):
        pytest.fail('search loaded the full retained envelope')
    monkeypatch.setattr(store, 'load', no_full_body)
    counter = 0
    def create(payload, *, page_size=3, query=None, command='test.read', unit=None):
        nonlocal counter
        counter += 1
        frame = {'schema': 'brain.command-result/1', 'command': command,
                 'command_version': 1, 'status': 'ok', 'warnings': [], 'result': payload}
        view = build_view(frame, page_size=page_size,
                          unit=unit or ('lines' if isinstance(payload, dict) and isinstance(payload.get('content'), str) else None))
        authority = {'command_id': command, 'stamp': None, 'default_view': 'default'}
        ticket = store.admit(session.owner, 'test.read-' + str(counter), str(counter))
        result_id = store.publish(session.owner, ticket, frame, authority, query)
        session._save(result_id, 'default', view, None)
        return result_id, view
    return store, session, context, create


def test_default_byte_crlf_boundary_does_not_omit_matches(retained):
    _, session, context, create = retained
    text = 'a' * 3931 + '\r\nneedle' + 'b' * 9000 + '\nlast needle\n'
    result_id, _ = create({'content': text}, page_size=4000, unit='bytes')
    matches, replies = collect(session, context, result_id, 'needle', scan_pages=50)
    assert [(item['line'], item['column']) for item in matches] == [(2, 0), (3, 5)]
    assert replies[-1]['complete']


def test_abandoned_compact_root_does_not_falsely_report_skipped_search_details(retained):
    _, session, context, create = retained
    result_id, _ = create({f'field{i}': 'x' * 600 for i in range(35)}, page_size=None)
    matches, replies = collect(session, context, result_id, 'not-present', scan_pages=50)
    assert not matches
    assert replies[-1]['scan_complete'] and replies[-1]['complete']
    assert not replies[-1]['details_skipped']


@pytest.mark.parametrize('size', [1, 2, 5, 9])
def test_crlf_search_coordinates_across_arbitrary_byte_boundaries(retained, size):
    _, session, context, create = retained
    text = 'hit\r\nhit\r\n\r\nhit\rhit\nhit\n'
    result_id, _ = create({'content': text}, page_size=size, unit='bytes')
    matches, replies = collect(session, context, result_id, 'hit', limit=1, scan_pages=50)
    assert [(item['line'], item['column']) for item in matches] == [(1, 0), (2, 0), (4, 0), (5, 0), (6, 0)]
    assert replies[-1]['complete']


def collect(session, context, result_id, literal, **options):
    page, offset, matches, replies = 1, 0, [], []
    for _ in range(100):
        reply = session.search(context, ResultSearchRequest(
            result_id, literal, start_page=page, start_offset=offset,
            **options))
        replies.append(reply)
        matches.extend(reply['matches'])
        if reply['scan_complete']:
            assert reply['next_page'] is None and reply['next_offset'] is None
            return matches, replies
        position = reply['next_page'], reply['next_offset']
        assert position > (page, offset), 'continuation failed to progress'
        page, offset = position
    pytest.fail('search never reached completion')


def test_all_matching_lines_resume_mid_page_without_skipping(retained):
    _, session, context, create = retained
    result_id, _ = create({'content': 'hit hit\nmiss\nhit two\nhit three\nhit four\n'})
    matches, replies = collect(session, context, result_id, 'hit', limit=1)
    assert [m['line'] for m in matches] == [1, 3, 4, 5]
    assert [(m['page'], m['offset']) for m in matches] == [(1, 0), (1, 2), (2, 0), (2, 1)]
    assert replies[0]['next_page'] == 1 and replies[0]['next_offset'] == 1
    assert matches[0]['snippet'] == 'hit hit'
    assert all(m['column'] == 0 for m in matches)


def test_all_matching_records_resume_with_sparse_nonmatches(retained):
    _, session, context, create = retained
    result_id, _ = create({'items': [{'value': value} for value in
        ['match-a', 'none', 'match-b', 'match-c', 'none', 'match-d']]})
    matches, _ = collect(session, context, result_id, 'match', limit=2)
    assert [m['record'] for m in matches] == [1, 3, 4, 6]
    assert [(m['page'], m['offset']) for m in matches] == [(1, 0), (1, 2), (2, 0), (2, 2)]


@pytest.mark.parametrize('command,record_field', [
    ('command.list', 'entries'), ('relationship.query', 'records'), ('history.query', 'records'),
])
def test_declared_record_search_matches_each_unit_and_resumes_within_page(retained, command, record_field):
    _, session, context, create = retained
    entries = [{'command_id': name} for name in
               ['needle.one', 'other.command', 'needle.three', 'needle.four', 'needle.five']]
    result_id, view = create({record_field: entries, 'context': 'needle-context'},
                             command=command, page_size=3)
    assert view.metadata()['record_field'] == record_field
    matches, replies = collect(session, context, result_id, 'needle', limit=1)
    assert [match['record'] for match in matches] == [1, 3, 4, 5]
    assert [(match['page'], match['offset']) for match in matches] == [(1, 0), (1, 2), (2, 0), (2, 1)]
    assert (replies[0]['next_page'], replies[0]['next_offset']) == (1, 1)
    assert all('needle-context' not in match['snippet'] for match in matches)
    assert replies[-1]['complete'] and replies[-1]['scan_complete']


def test_response_byte_budget_preserves_every_unreturned_match(retained):
    _, session, context, create = retained
    result_id, _ = create({'content': ('雪' * 200 + '\n') * 40}, page_size=100)
    matches, replies = collect(session, context, result_id, '雪', limit=25, scan_pages=50)
    assert len(matches) == 40
    assert [m['line'] for m in matches] == list(range(1, 41))
    assert len(replies[0]['matches']) < 25
    assert all(body_fits(reply) for reply in replies)
    assert all(len(m['snippet']) <= 121 for m in matches)


@pytest.mark.parametrize('payload', [
    {'content': 'visible body', 'context': 'metadata-only-needle'},
    {'items': [{'value': 'visible body'}], 'context': 'metadata-only-needle'},
])
def test_body_coverage_excludes_canonical_metadata_and_context_extras(retained, payload):
    store, session, context, create = retained
    result_id, view = create(payload)
    frames = [{**frame, 'warnings': [{'message': 'metadata-only-needle'}],
               'error': {'message': 'metadata-only-needle'},
               'command': 'metadata-only-needle'} for frame in view.pages]
    store.save_view(session.owner, result_id, 'default', frames, view.metadata())
    reply = session.search(context, ResultSearchRequest(result_id, 'metadata-only-needle'))
    assert reply['matches'] == ()
    assert reply['coverage'] == 'retained-body-units'
    assert reply['scan_complete'] and reply['complete']
    assert not reply['details_skipped']


def test_scan_work_is_limited_to_requested_pages_and_resumes_at_next_page(retained, monkeypatch):
    store, session, context, create = retained
    result_id, view = create({'content': 'no match\n' * 15})
    read = store.read_view_page
    seen = []
    def bounded(*args):
        seen.append(args[-1])
        return read(*args)
    monkeypatch.setattr(store, 'read_view_page', bounded)
    matches, replies = collect(session, context, result_id, 'absent', scan_pages=2)
    assert matches == [] and seen == list(range(1, len(view.pages) + 1))
    assert all(reply['scanned_pages'] <= 2 for reply in replies)
    assert (replies[0]['next_page'], replies[0]['next_offset']) == (3, 0)


def test_permission_checked_before_any_body_and_again_between_pages(retained, monkeypatch):
    store, session, context, create = retained
    result_id, _ = create({'content': 'hit\n' * 6})
    read = store.read_view_page
    seen = []
    def tracked(*args):
        seen.append(args[-1])
        return read(*args)
    monkeypatch.setattr(store, 'read_view_page', tracked)
    def denied(*args):
        raise ConsentError('authorisation_required', 'Current disclosure is denied.')
    context.authorisation.service.check_result_delivery = denied
    with pytest.raises(ConsentError):
        session.search(context, ResultSearchRequest(result_id, 'hit'))
    assert seen == []
    def revoked_after_first(*args):
        if seen:
            denied()
    context.authorisation.service.check_result_delivery = revoked_after_first
    with pytest.raises(ConsentError):
        session.search(context, ResultSearchRequest(result_id, 'hit'))
    assert seen == [1]


def test_final_disclosure_check_blocks_accumulated_matches(retained, monkeypatch):
    store, session, context, create = retained
    result_id, _ = create({'items': [{'value': 'hit'}]})
    read = store.read_view_page
    def revoke_after_read(*args):
        frame = read(*args)
        def denied(*args):
            raise ConsentError('authorisation_required', 'Revoked during read.')
        context.authorisation.service.check_result_delivery = denied
        return frame
    monkeypatch.setattr(store, 'read_view_page', revoke_after_read)
    with pytest.raises(ConsentError):
        session.search(context, ResultSearchRequest(result_id, 'hit'))


def test_store_errors_preserve_typed_sanitised_navigation_failure(retained, monkeypatch):
    store, session, context, create = retained
    result_id, _ = create({'content': 'hit\n'})
    def fail(*args):
        raise ResultStoreError('unavailable', 'Retained output is unavailable.')
    monkeypatch.setattr(store, 'read_view_page', fail)
    from _application.delivery_failure import DeliveryFailure, NavigationDeliveryError
    with pytest.raises(DeliveryFailure, match='Private result storage is unavailable'):
        session.search(context, ResultSearchRequest(result_id, 'hit'))
    result = execute(context, ResultSearchRequest(result_id, 'hit'))
    assert isinstance(result, NavigationDeliveryError)
    assert result.failure.reason == 'result_unavailable'
    assert result.result_id == result_id


def test_dynamic_query_is_rejected_without_body_read(retained, monkeypatch):
    store, session, context, create = retained
    result_id, _ = create({'content': 'hit\n'}, query={'command_id': 'artefact.list'})
    def forbidden(*args):
        pytest.fail('search read a dynamic page')
    monkeypatch.setattr(store, 'read_view_page', forbidden)
    with pytest.raises(ValueError, match='retained output'):
        session.search(context, ResultSearchRequest(result_id, 'hit'))


def test_invalid_offset_cannot_silently_skip_page_and_empty_search_completes(retained):
    _, session, context, create = retained
    result_id, _ = create({'content': 'hit\n'})
    with pytest.raises(ValueError, match='start_offset'):
        session.search(context, ResultSearchRequest(result_id, 'hit', start_offset=2))
    end = session.search(context, ResultSearchRequest(result_id, 'hit', start_offset=1))
    assert end['complete'] and end['matches'] == ()
    empty, _ = create({'items': []})
    reply = session.search(context, ResultSearchRequest(empty, 'hit'))
    assert reply['complete'] and reply['scanned_pages'] == 0


def test_request_decode_validates_resumable_offsets_and_limits():
    request = decode({'result_id': 'result', 'literal': '雪', 'start_page': 2, 'start_offset': 3})
    assert request.start_offset == 3 and request.start_page == 2
    for field, value in [('start_offset', -1), ('start_offset', True), ('start_page', 0),
                         ('limit', 26), ('scan_pages', 51), ('literal', '')]:
        with pytest.raises(ValueError):
            decode({'result_id': 'result', 'literal': 'hit', field: value})
    with pytest.raises(ValueError):
        decode({'result_id': 'result', 'literal': 'hit', 'unknown': True})


def test_literal_across_three_text_pages_survives_page_resume(retained):
    store, session, context, create = retained
    result_id, view = create({'content': 'abcNEEDLEdef'})
    texts = ['abcNE', 'ED', 'LEdef']
    frames = []
    start = 0
    for index, text in enumerate(texts):
        frames.append({'result': {'content': text,
            'content_range': {'start': start, 'end': start + len(text), 'total': 12}},
            '_text_column': start, 'delivery': {'first_item': 1, 'last_item': 1}})
        start += len(text)
    store.save_view(session.owner, result_id, 'default', frames,
                    {**view.metadata(), 'total_pages': len(frames)})
    matches, replies = collect(session, context, result_id, 'NEEDLE', scan_pages=1)
    assert len(matches) == 1 and matches[0]['column'] == 3
    assert 'NEEDLE' in matches[0]['snippet']
    assert matches[0]['page'] == 3
    assert replies[-1]['complete']
    assert replies[-1]['overlap_pages'] == 2
    assert all(reply['scanned_pages'] <= 1 and reply['overlap_pages'] <= 256 for reply in replies)


def test_text_page_overlap_and_mid_page_resume(retained):
    store, session, context, create = retained
    original = 'abcNEEDLE hit\nNEEDLE'
    result_id, view = create({'content': original})
    store.save_view(session.owner, result_id, 'default', [
        {'result': {'content': original[:5],
                    'content_range': {'start': 0, 'end': 5, 'total': len(original)}},
         '_text_column': 0, 'delivery': {'first_item': 1, 'last_item': 1}},
        {'result': {'content': original[5:],
                    'content_range': {'start': 5, 'end': len(original), 'total': len(original)}},
         '_text_column': 5, 'delivery': {'first_item': 1, 'last_item': 2}},
    ], {**view.metadata(), 'total_pages': 2})
    matches, replies = collect(session, context, result_id, 'NEEDLE', limit=1)
    assert [(m['line'], m['column']) for m in matches] == [(1, 3), (2, 0)]
    assert [(m['page'], m['offset']) for m in matches] == [(2, 0), (2, 1)]
    assert (replies[0]['next_page'], replies[0]['next_offset']) == (2, 1)
    resumed = session.search(context, ResultSearchRequest(
        result_id, 'NEEDLE', start_page=2, start_offset=0))
    assert [(m['line'], m['column']) for m in resumed['matches']] == [(1, 3), (2, 0)]
    assert resumed['overlap_pages'] == 1


def test_skipped_detail_content_never_claims_complete_across_resume(retained):
    _, session, context, create = retained
    result_id, view = create({'items': [
        {'id': 'large', 'body': 'hidden needle ' * 1000},
        {'id': 'small', 'body': 'visible needle'},
    ]}, page_size=1)
    assert view.details
    matches, replies = collect(session, context, result_id, 'needle', scan_pages=1)
    assert len(matches) == 1 and 'visible needle' in matches[0]['snippet']
    assert all(reply['details_skipped'] and not reply['complete'] for reply in replies)
    assert replies[-1]['scan_complete'] and replies[-1]['next_page'] is None
    assert replies[-1]['coverage'] == 'retained-body-units'
    assert all(body_fits(reply) for reply in replies)


@pytest.mark.parametrize('detail_first', [True, False])
def test_coverage_comes_from_whole_view_without_scanning_other_pages(retained, monkeypatch, detail_first):
    store, session, context, create = retained
    large = {'id': 'large', 'body': 'hidden needle ' * 1000}
    small = {'id': 'small', 'body': 'visible needle'}
    result_id, view = create({'items': [large, small] if detail_first else [small, large]}, page_size=1)
    assert view.metadata()['has_details'] is True
    assert store.view_metadata(session.owner, result_id, 'default')['has_details'] is True
    visible_page = 2 if detail_first else 1
    original = store.read_view_page
    seen = []

    def read_visible_page_only(*args):
        seen.append(args[-1])
        assert args[-1] == visible_page
        return original(*args)

    monkeypatch.setattr(store, 'read_view_page', read_visible_page_only)
    reply = session.search(context, ResultSearchRequest(
        result_id, 'needle', start_page=visible_page, scan_pages=1))
    assert seen == [visible_page]
    assert len(reply['matches']) == 1
    assert reply['details_skipped'] and not reply['complete']
    assert reply['coverage'] == 'retained-body-units'
    assert reply['scan_complete'] is detail_first


@pytest.mark.parametrize('payload', [{'items': []}, {'content': ''}, {'items': [{'body': 'needle'}]}])
def test_view_without_details_can_report_complete(retained, payload):
    store, session, context, create = retained
    result_id, view = create(payload)
    assert view.metadata()['has_details'] is False
    assert store.view_metadata(session.owner, result_id, 'default')['has_details'] is False
    reply = session.search(context, ResultSearchRequest(result_id, 'needle'))
    assert reply['complete'] and reply['scan_complete']
    assert not reply['details_skipped']
    assert reply['coverage'] == 'retained-body-units'


def test_empty_text_with_context_details_never_claims_full_coverage(retained):
    _, session, context, create = retained
    result_id, view = create({'content': '', 'context': 'hidden needle ' * 1000})
    assert view.pages == () and view.metadata()['has_details'] is True
    reply = session.search(context, ResultSearchRequest(result_id, 'needle'))
    assert reply['scanned_pages'] == 0 and reply['scan_complete']
    assert reply['details_skipped'] and not reply['complete']
    assert reply['coverage'] == 'retained-body-units'


def test_incomplete_text_page_sequence_fails_instead_of_claiming_complete(retained):
    store, session, context, create = retained
    result_id, view = create({'content': 'needle'})
    store.save_view(session.owner, result_id, 'default', [
        {'result': {'content': 'le', 'content_range': {'start': 4, 'end': 6, 'total': 6}},
         '_text_column': 4, 'delivery': {'first_item': 1, 'last_item': 1}},
    ], view.metadata())
    with pytest.raises(ValueError, match='overlap is unavailable'):
        session.search(context, ResultSearchRequest(result_id, 'needle'))


def test_successful_command_payload_exposes_offsets_and_coverage(retained):
    _, _, context, create = retained
    result_id, _ = create({'content': 'hit first\nhit second\n'})
    result = execute(context, ResultSearchRequest(result_id, 'hit', limit=1))
    from _application.results import Ok
    assert isinstance(result, Ok)
    assert result.result.next_page == 1 and result.result.next_offset == 1
    assert result.result.coverage == 'retained-body-units'
    assert not result.result.complete and not result.result.scan_complete
    assert not result.result.details_skipped
    for removed_value in (False, True):
        with pytest.raises(ValueError, match='details_skipped'):
            decode({'result_id': result_id, 'literal': 'hit', 'details_skipped': removed_value})

"""BDD coverage for the attachment-upload boundary."""

from pytest_bdd import given, parsers, scenarios, then, when

import upload_attachment


scenarios("features/attachment_upload.feature")


@given("an empty Brain vault for attachment upload", target_fixture="attachment_vault")
def empty_attachment_vault(tmp_path):
    return tmp_path


@when(
    parsers.parse(
        'I upload attachment "{name}" to "{destination_key}" with content "{content}"'
    ),
    target_fixture="attachment_result",
)
def upload_attachment_step(attachment_vault, name, destination_key, content):
    return upload_attachment.upload_attachment(
        attachment_vault,
        {"artefact_index": {}},
        destination_key=destination_key,
        name=name,
        content=content.encode("utf-8"),
    )


@then(parsers.parse('the attachment path is "{expected_path}"'))
def assert_attachment_path(attachment_result, expected_path):
    assert attachment_result["path"] == expected_path


@then(parsers.parse('the attachment bytes are "{expected_content}"'))
def assert_attachment_bytes(attachment_vault, attachment_result, expected_content):
    assert (
        attachment_vault / attachment_result["path"]
    ).read_bytes() == expected_content.encode("utf-8")


@then(parsers.parse('the attachment embed is "{expected_embed}"'))
def assert_attachment_embed(attachment_result, expected_embed):
    assert attachment_result["embed"] == expected_embed

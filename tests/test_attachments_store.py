"""The attachment store: what it accepts, where it puts it, what takes it away.

FEAT-098. The store is the one place an attachment path is built, so these are
the rules nothing else gets to restate: the size cap, the type allowlist read off
the *bytes*, the refusal to be the thing that mints a conversation, and the
traversal that is refused rather than sanitized.

The lifetime assertion is the load-bearing one. Nothing in this module deletes
anything; deletion is inherited from ``conversations.delete_conversation``, and
that inheritance is the reason there is no retention job to own.
"""

import pytest

from condor import paths
from condor.runtime import attachments, conversations

USER = 4242

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 64


@pytest.fixture
def conv():
    """A real conversation, because the store refuses to write without one."""
    return conversations.new_conversation(USER, surface="web").id


def test_a_png_round_trips(conv):
    stored = attachments.save(USER, conv, PNG)
    assert stored.mime == "image/png"
    assert stored.bytes == len(PNG)
    assert stored.id.endswith(".png")
    assert attachments.load(USER, conv, stored.id) == (PNG, "image/png")


@pytest.mark.parametrize(
    "data,mime",
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (GIF, "image/gif"),
        (WEBP, "image/webp"),
    ],
)
def test_every_allowed_type_is_recognised_from_its_bytes(conv, data, mime):
    assert attachments.save(USER, conv, data).mime == mime


def test_it_lands_inside_the_conversation_it_belongs_to(conv):
    stored = attachments.save(USER, conv, PNG)
    path = paths.conversation_dir(USER, conv) / "attachments" / stored.id
    assert path.is_file(), "the store must write under the conversation directory"


def test_the_name_the_client_gave_is_nowhere_on_disk(conv):
    """No filename is recorded, so there is none to leak into a share."""
    stored = attachments.save(USER, conv, PNG)
    assert stored.id.split(".")[0].isalnum()
    assert not hasattr(stored, "name")


def test_a_file_over_the_cap_is_refused(conv):
    with pytest.raises(attachments.TooLargeError):
        attachments.save(USER, conv, PNG + b"\x00" * attachments.MAX_BYTES)


def test_the_claimed_type_is_never_believed(conv):
    """A zip does not become a PNG by being uploaded as one."""
    with pytest.raises(attachments.UnsupportedTypeError):
        attachments.save(USER, conv, b"PK\x03\x04 this is a zip")


def test_an_empty_file_is_refused(conv):
    with pytest.raises(attachments.UnsupportedTypeError):
        attachments.save(USER, conv, b"")


def test_a_conversation_that_does_not_exist_is_not_created_by_uploading(conv):
    with pytest.raises(attachments.NoConversationError):
        attachments.save(USER, "deadbeefcafe", PNG)
    assert not paths.conversation_dir(USER, "deadbeefcafe").exists()


def test_a_traversal_is_refused_rather_than_sanitized(conv):
    attachments.save(USER, conv, PNG)
    for att_id in ("../../meta.json", "..%2Fmeta.json", "a/b.png"):
        with pytest.raises(attachments.NotFoundError):
            attachments.load(USER, conv, att_id)


def test_a_name_this_module_never_wrote_is_not_served(conv):
    """The extension is checked too, so the directory cannot become a download."""
    directory = paths.conversation_dir(USER, conv) / "attachments"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "secrets.env").write_bytes(b"KEY=1")
    with pytest.raises(attachments.NotFoundError):
        attachments.load(USER, conv, "secrets.env")


def test_another_user_cannot_name_the_same_file(conv):
    """Ownership is a path, not a check the route could forget to make."""
    stored = attachments.save(USER, conv, PNG)
    with pytest.raises(attachments.NotFoundError):
        attachments.load(USER + 1, conv, stored.id)


def test_deleting_the_conversation_takes_the_images_with_it(conv):
    stored = attachments.save(USER, conv, PNG)
    assert conversations.delete_conversation(USER, conv)
    assert not paths.conversation_dir(USER, conv).exists()
    with pytest.raises(attachments.NotFoundError):
        attachments.load(USER, conv, stored.id)

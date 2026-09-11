"""What a user handed over with a message, and where it is kept.

The only place an attachment path is built, in the spirit of :mod:`condor.paths`
— one module owns the layout, the limits and the type allowlist, so a second
surface (Telegram's ``filters.PHOTO``, when someone builds it) reaches the same
rules by calling :func:`save` rather than by remembering them.

Files land **inside the conversation they belong to**::

    <runtime>/users/{user_id}/conversations/{conv_id}/attachments/{id}

which buys three properties without any code here:

* **Deletion is inherited.** ``conversations.delete_conversation`` already
  ``rmtree``s the conversation directory, so forgetting a chat forgets its
  pictures. There is no retention job to own and no orphan to sweep — a file is
  only ever written for a message that is being sent.
* **Ownership is a path, not a check.** The user id is the first segment, so
  reading someone else's attachment is not a permission a route could forget to
  make; it is a path the caller cannot name.
* **The id cannot escape.** Every segment goes through :func:`paths.safe_id`,
  which *refuses* rather than sanitizes.

The id **is** the filename, extension included (``a1b2….png``), so resolving one
is a single join with no directory listing, and the mime is recoverable from the
id alone. The extension comes from the *sniffed* bytes, never from the client's
claim: a caller that says ``image/png`` over a zip gets a 415, not a ``.png``
holding a zip.

No filename is recorded, anywhere. It is free text the user did not consider
("Screenshot of acme-prod ledger.png"), it would travel into the share corpus as
payload the scrubber has no reason to touch, and after a reload "an image" plus
its thumbnail says everything the name would (FEAT-098).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from condor import paths

# Per file. Chosen against what a screenshot actually weighs (a retina full-screen
# PNG is ~2-4 MB) rather than against what a provider will accept.
MAX_BYTES = 5 * 1024 * 1024

# Per turn. A bound on the resolution work one WS frame can ask for, and on how
# much a single message can add to a conversation directory.
MAX_PER_TURN = 4

# The boundary between "an attachment" and "an image the model is given". A
# second kind of attachment (a PDF) would be taught here first.
ALLOWED = ("image/png", "image/jpeg", "image/gif", "image/webp")

_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_MIME_BY_EXT = {ext: mime for mime, ext in _EXT.items()}

_DIRNAME = "attachments"


class AttachmentError(Exception):
    """Base for every refusal this module makes."""


class TooLargeError(AttachmentError):
    """More bytes than :data:`MAX_BYTES`."""


class UnsupportedTypeError(AttachmentError):
    """The bytes are not one of :data:`ALLOWED`."""


class NoConversationError(AttachmentError):
    """No such conversation for this user, so there is nowhere to write."""


class NotFoundError(AttachmentError):
    """No such attachment under that conversation."""


@dataclass(frozen=True)
class Attachment:
    """One stored file, as the routes and the transcript describe it.

    ``bytes`` is the *size*, not the content: this shape is what a JSON response
    carries and what a ``TurnEntry`` records, and neither wants a payload. The
    content is read back with :func:`load`.
    """

    id: str
    mime: str
    bytes: int


def sniff(data: bytes) -> str | None:
    """The mime these bytes actually are, or ``None`` if it is not an allowed one.

    Magic numbers only — enough to tell the four formats apart and to refuse
    everything else, which is the whole job. It is deliberately not a general
    content-type detector: anything it cannot name is refused, so a gap here
    fails closed.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def attachments_dir(user_id: int | str, conv_id: str) -> Path:
    """Where this conversation's files live. Not created here."""
    return paths.conversation_dir(user_id, conv_id) / _DIRNAME


def save(user_id: int | str, conv_id: str, data: bytes) -> Attachment:
    """Write one file under its conversation and describe it.

    The mime is sniffed from ``data``; there is no parameter for the caller's
    claim about it, because there is nothing this function would do with one.

    Refuses a conversation directory that does not exist: the caller must own a
    real conversation, so a POST can never be the thing that mints one.
    """
    if len(data) > MAX_BYTES:
        raise TooLargeError(
            f"Attachment is {len(data)} bytes; the limit is {MAX_BYTES}."
        )
    if not data:
        raise UnsupportedTypeError("Empty file.")

    mime = sniff(data)
    if mime is None:
        raise UnsupportedTypeError(
            "Only PNG, JPEG, GIF and WebP images can be attached."
        )

    conv_dir = paths.conversation_dir(user_id, conv_id)
    if not conv_dir.is_dir():
        raise NoConversationError(f"No conversation {conv_id}")

    att_id = f"{uuid.uuid4().hex}{_EXT[mime]}"
    target = conv_dir / _DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    (target / att_id).write_bytes(data)
    return Attachment(id=att_id, mime=mime, bytes=len(data))


def load(user_id: int | str, conv_id: str, att_id: str) -> tuple[bytes, str]:
    """The stored bytes and their mime, or :class:`NotFoundError`.

    ``att_id`` goes through :func:`paths.safe_id` before it is joined, so a
    traversal is refused rather than sanitized — and the extension is checked
    against the allowlist, so a path that somehow named a file this module never
    wrote is still not served as an arbitrary download.
    """
    try:
        name = paths.safe_id(att_id)
    except paths.UnsafeIdError as exc:
        raise NotFoundError(str(exc)) from exc

    mime = _MIME_BY_EXT.get(Path(name).suffix.lower())
    if mime is None:
        raise NotFoundError(f"No attachment {att_id}")

    path = attachments_dir(user_id, conv_id) / name
    if not path.is_file():
        raise NotFoundError(f"No attachment {att_id}")
    return path.read_bytes(), mime

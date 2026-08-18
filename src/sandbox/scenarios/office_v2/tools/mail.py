"""Mail-domain Office V2 tool argument models and handlers."""

from __future__ import annotations

from pydantic import Field, JsonValue, model_validator

from sandbox.scenarios.office_v2.models import (
    ActionKind,
    MailDelivery,
    MailFolder,
    MailMessage,
    MailStore,
    MailThread,
    OfficeV2Contract,
    ResourceKind,
    ResourceRef,
)
from sandbox.scenarios.office_v2.tools.contracts import ToolFailureCode
from sandbox.scenarios.office_v2.tools.runtime import (
    OfficeV2ToolRuntime,
    PreparedAction,
    ToolDefinition,
    ToolRejected,
)
from sandbox.scenarios.office_v2.world import EpisodeTransaction


class SearchEmailArguments(OfficeV2Contract):
    query: str | None = Field(default=None, min_length=1, max_length=512)
    thread_id: str | None = None
    page_size: int = Field(default=10, ge=1, le=25)
    page_token: str | None = Field(default=None, min_length=1, max_length=4096)


class ReadEmailArguments(OfficeV2Contract):
    message_id: str


class SendEmailArguments(OfficeV2Contract):
    to: tuple[str, ...] = Field(min_length=1)
    cc: tuple[str, ...] = Field(default_factory=tuple)
    subject: str = Field(min_length=1, max_length=512)
    body: str = Field(max_length=200_000)
    related_refs: tuple[ResourceRef, ...] = Field(default_factory=tuple)
    in_reply_to: str | None = None

    @model_validator(mode="after")
    def recipients_and_refs_are_valid(self) -> SendEmailArguments:
        if len(self.to) != len(set(self.to)) or len(self.cc) != len(set(self.cc)):
            raise ValueError("recipients must not contain duplicates")
        if set(self.to).intersection(self.cc):
            raise ValueError("to and cc must not overlap")
        if any(
            ref.kind
            not in {
                ResourceKind.CALENDAR_EVENT,
                ResourceKind.DRIVE_FILE,
                ResourceKind.DRIVE_FILE_VERSION,
                ResourceKind.WORKSPACE_FILE,
            }
            for ref in self.related_refs
        ):
            raise ValueError("mail related_refs contain an incompatible kind")
        return self


def _message_output(
    runtime: OfficeV2ToolRuntime,
    message: MailMessage,
    *,
    include_body: bool = True,
) -> dict[str, JsonValue]:
    output: dict[str, JsonValue] = {
        "resource": ResourceRef(
            kind=ResourceKind.MAIL_MESSAGE,
            resource_id=message.message_id,
        ).model_dump(mode="json"),
        "thread_id": message.thread_id,
        "sender_id": message.sender_id,
        "to_ids": list(message.to_ids),
        "cc_ids": list(message.cc_ids),
        "subject": message.subject,
        "sent_at": message.sent_at.isoformat(),
        "received_at": message.received_at.isoformat(),
        "related_refs": [item.model_dump(mode="json") for item in message.attachment_refs],
        "in_reply_to": message.in_reply_to,
    }
    if include_body:
        output["body"] = message.body
    return output


def _prepare_search(*_: object) -> PreparedAction:
    return PreparedAction()


def _search(
    runtime: OfficeV2ToolRuntime,
    arguments: SearchEmailArguments,
    _: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    visible = runtime.visible_resources((ResourceKind.MAIL_MESSAGE,), text=arguments.query)
    visible_ids = {item.resource.resource_id for item in visible}
    messages = tuple(
        message
        for message in runtime.state.domain_graph.mail.messages
        if message.message_id in visible_ids
        and (arguments.thread_id is None or message.thread_id == arguments.thread_id)
    )
    items = tuple(
        _message_output(runtime, message, include_body=False) for message in messages
    )
    return runtime.paginate(
        items,
        query_payload={"query": arguments.query, "thread_id": arguments.thread_id},
        page_size=arguments.page_size,
        page_token=arguments.page_token,
    )


def _prepare_read(runtime: OfficeV2ToolRuntime, arguments: ReadEmailArguments) -> PreparedAction:
    resource = ResourceRef(kind=ResourceKind.MAIL_MESSAGE, resource_id=arguments.message_id)
    runtime.visible_resource(resource)
    return PreparedAction(resources=(resource,))


def _read(
    runtime: OfficeV2ToolRuntime,
    arguments: ReadEmailArguments,
    _: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    message = next(
        item
        for item in runtime.state.domain_graph.mail.messages
        if item.message_id == arguments.message_id
    )
    return _message_output(runtime, message)


def _resolved_recipients(
    runtime: OfficeV2ToolRuntime, arguments: SendEmailArguments
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    to_ids = tuple(sorted(runtime.resolve_principal(item) for item in arguments.to))
    cc_ids = tuple(sorted(runtime.resolve_principal(item) for item in arguments.cc))
    if set(to_ids).intersection(cc_ids):
        raise ToolRejected(ToolFailureCode.RECIPIENT_NOT_RESOLVABLE)
    return to_ids, cc_ids


def _prepare_send(runtime: OfficeV2ToolRuntime, arguments: SendEmailArguments) -> PreparedAction:
    to_ids, cc_ids = _resolved_recipients(runtime, arguments)
    runtime.require_visible_refs(arguments.related_refs)
    if arguments.in_reply_to is not None:
        runtime.visible_resource(
            ResourceRef(
                kind=ResourceKind.MAIL_MESSAGE,
                resource_id=arguments.in_reply_to,
            )
        )
    return PreparedAction(
        resources=arguments.related_refs,
        recipient_ids=tuple(sorted((*to_ids, *cc_ids))),
        resource_kinds=(
            tuple(
                sorted(
                    {item.kind for item in arguments.related_refs},
                    key=lambda item: item.value,
                )
            )
            if arguments.related_refs
            else None
        ),
    )


def _send(
    runtime: OfficeV2ToolRuntime,
    arguments: SendEmailArguments,
    transaction: EpisodeTransaction | None,
) -> dict[str, JsonValue]:
    assert transaction is not None
    to_ids, cc_ids = _resolved_recipients(runtime, arguments)
    mail = transaction.staged_state.domain_graph.mail
    message_id = transaction.allocate_id("mail.message")
    if arguments.in_reply_to is None:
        thread_id = transaction.allocate_id("mail.thread")
        thread = MailThread(
            thread_id=thread_id,
            subject=arguments.subject,
            message_ids=(message_id,),
        )
        threads = (*mail.threads, thread)
    else:
        parent = next(item for item in mail.messages if item.message_id == arguments.in_reply_to)
        thread_id = parent.thread_id
        threads = tuple(
            item.model_copy(update={"message_ids": (*item.message_ids, message_id)})
            if item.thread_id == thread_id
            else item
            for item in mail.threads
        )
    timestamp = runtime.logical_datetime
    message = MailMessage(
        message_id=message_id,
        thread_id=thread_id,
        sender_id=runtime.actor.actor_id,
        to_ids=to_ids,
        cc_ids=cc_ids,
        subject=arguments.subject,
        body=arguments.body,
        sent_at=timestamp,
        received_at=timestamp,
        attachment_refs=arguments.related_refs,
        in_reply_to=arguments.in_reply_to,
    )
    deliveries = (
        *mail.deliveries,
        MailDelivery(
            message_id=message_id,
            mailbox_owner_id=runtime.actor.actor_id,
            folder=MailFolder.SENT,
        ),
        *(
            MailDelivery(
                message_id=message_id,
                mailbox_owner_id=recipient_id,
                folder=MailFolder.INBOX,
            )
            for recipient_id in (*to_ids, *cc_ids)
        ),
    )
    updated = MailStore(
        threads=threads,
        messages=(*mail.messages, message),
        deliveries=deliveries,
    )
    runtime.replace_graph(transaction, mail=updated)
    return _message_output(runtime, message)


DEFINITIONS = (
    ToolDefinition(
        name="search_email",
        arguments_model=SearchEmailArguments,
        action=ActionKind.DISCOVER,
        capability_id="mail.read",
        resource_kinds=(ResourceKind.MAIL_MESSAGE,),
        prepare=_prepare_search,
        execute=_search,
    ),
    ToolDefinition(
        name="read_email",
        arguments_model=ReadEmailArguments,
        action=ActionKind.READ,
        capability_id="mail.read",
        resource_kinds=(ResourceKind.MAIL_MESSAGE,),
        prepare=_prepare_read,
        execute=_read,
    ),
    ToolDefinition(
        name="send_email",
        arguments_model=SendEmailArguments,
        action=ActionKind.SEND,
        capability_id="mail.send",
        resource_kinds=(
            ResourceKind.CALENDAR_EVENT,
            ResourceKind.DRIVE_FILE,
            ResourceKind.DRIVE_FILE_VERSION,
            ResourceKind.WORKSPACE_FILE,
        ),
        prepare=_prepare_send,
        execute=_send,
        writes_state=True,
    ),
)


__all__ = [
    "DEFINITIONS",
    "ReadEmailArguments",
    "SearchEmailArguments",
    "SendEmailArguments",
]

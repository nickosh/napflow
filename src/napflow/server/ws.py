"""Bounded live WebSocket streaming over canonical run history."""

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

from blacksheep import WebSocket

from napflow.core.events import HistoryFormatError, begin_history_reader, encode_record
from napflow.server.replay import iter_records
from napflow.server.runs import (
    SUBSCRIBER_END,
    SUBSCRIBER_RESYNC,
    RunManager,
    SubscriberLimitError,
)

# WebSocket close codes (4xxx = application-defined)
WS_UNKNOWN_RUN = 4404
WS_HISTORY_FORMAT = 4409
WS_RESYNC_REQUIRED = 4410
WS_SUBSCRIBER_LIMIT = 4411
WS_SEND_TIMEOUT_S = 5.0
WS_CLOSE_TIMEOUT_S = 1.0


def ws_close_reason(message: str, limit: int = 120) -> str:
    """Application close reasons must fit the WebSocket control frame."""
    safe = message.encode("utf-8", errors="backslashreplace").decode("utf-8")
    encoded = safe.encode("utf-8")
    if len(encoded) <= limit:
        return safe
    return encoded[: limit - 3].decode("utf-8", errors="ignore") + "..."


class SlowSubscriber(TimeoutError):
    pass


async def send_ws_record(websocket: WebSocket, record: dict[str, Any]) -> None:
    try:
        await asyncio.wait_for(
            websocket.send_text(encode_record(record)),
            timeout=WS_SEND_TIMEOUT_S,
        )
    except TimeoutError as error:
        raise SlowSubscriber from error


async def send_history_range(
    websocket: WebSocket,
    path: Path,
    *,
    after_seq: int = 0,
    through_seq: int | None = None,
    allow_empty: bool = False,
) -> int:
    """Send one exact durable range and return its last sequence."""
    last_sent = after_seq
    for record in iter_records(
        path,
        allow_empty=allow_empty,
        through_seq=through_seq,
    ):
        seq = record.get("seq")
        if type(seq) is int and seq <= after_seq:
            continue
        await send_ws_record(websocket, record)
        if type(seq) is int:
            last_sent = seq
    return last_sent


async def close_ws(websocket: WebSocket, code: int, reason: str = "") -> None:
    with suppress(Exception):
        await asyncio.wait_for(
            websocket.close(code, reason), timeout=WS_CLOSE_TIMEOUT_S
        )


async def stream_run_websocket(
    websocket: WebSocket, run: Any, manager: RunManager
) -> None:
    """Serve one filesystem-leased run without retaining its event prefix."""
    try:
        lease = begin_history_reader(run.log_path)
    except (OSError, ValueError):
        await close_ws(websocket, WS_UNKNOWN_RUN, "run history unavailable")
        return

    defer_release = False
    subscriber = None
    replay_pinned = False
    try:
        if run.finished:
            manager.pin_replay(run)
            replay_pinned = True
            await send_history_range(websocket, run.log_path)
        else:
            through_seq, subscriber = manager.subscribe(run)
            last_sent = await send_history_range(
                websocket,
                run.log_path,
                allow_empty=True,
                through_seq=through_seq,
            )
            while True:
                item = await subscriber.queue.get()
                if item is SUBSCRIBER_END:
                    break
                if item is SUBSCRIBER_RESYNC:
                    through_seq, subscriber = manager.resubscribe(run, subscriber)
                    last_sent = await send_history_range(
                        websocket,
                        run.log_path,
                        after_seq=last_sent,
                        through_seq=through_seq,
                        allow_empty=True,
                    )
                    continue
                seq = item.get("seq")
                if type(seq) is int and seq <= last_sent:
                    continue
                await send_ws_record(websocket, item)
                if type(seq) is int:
                    last_sent = seq
    except SubscriberLimitError:
        await close_ws(websocket, WS_SUBSCRIBER_LIMIT, "subscriber_limit")
        return
    except HistoryFormatError as error:
        await close_ws(websocket, WS_HISTORY_FORMAT, ws_close_reason(str(error)))
        return
    except SlowSubscriber:
        manager.reserve_resync(run)
        manager.defer_history_reader_release(
            run.log_path,
            lease,
            run.history_limit,
        )
        defer_release = True
        await close_ws(websocket, WS_RESYNC_REQUIRED, "resync_required")
        return
    finally:
        if replay_pinned:
            manager.unpin_replay(run)
        if subscriber is not None:
            manager.unsubscribe(run, subscriber)
        if not defer_release:
            manager.release_history_reader(run.log_path, lease, run.history_limit)
    await close_ws(websocket, 1000)

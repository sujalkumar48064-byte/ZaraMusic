

import asyncio

import config

# chat_id -> queue list
chat_queues: dict[int, list] = {}
_queue_locks: dict[int, asyncio.Lock] = {}


def queue_lock(chat_id: int) -> asyncio.Lock:
    return _queue_locks.setdefault(int(chat_id), asyncio.Lock())


def is_file_in_use(path: str) -> bool:
    if not path:
        return False
    for queue in chat_queues.values():
        for song in queue:
            if song.get("file_path") == path or song.get("url") == path:
                return True
    return False


def get_queue(chat_id: int) -> list:
    return chat_queues.get(chat_id, [])


def add_to_queue(chat_id: int, song: dict) -> int:
    queue = chat_queues.setdefault(chat_id, [])
    if len(queue) >= config.QUEUE_LIMIT:
        return 0
    queue.append(song)
    return len(queue)


def push_front(chat_id: int, song: dict) -> int:
    queue = chat_queues.setdefault(chat_id, [])
    queue.insert(0, song)
    return len(queue)


def pop_current(chat_id: int) -> dict | None:
    queue = chat_queues.get(chat_id)

    if not queue:
        return None

    return queue.pop(0)


def remove_from_queue(chat_id: int, index: int) -> dict | None:
    queue = chat_queues.get(chat_id)

    if not queue:
        return None

    if index < 0 or index >= len(queue):
        return None

    return queue.pop(index)


def remove_song(chat_id: int, song: dict) -> dict | None:
    """Remove a specific queued song by identity, never the current song blindly."""
    queue = chat_queues.get(chat_id)

    if not queue:
        return None

    for index, item in enumerate(queue):
        if item is song:
            return queue.pop(index)

    return None


def peek_current(chat_id: int) -> dict | None:
    queue = chat_queues.get(chat_id)

    if not queue:
        return None

    return queue[0]


def peek_next(chat_id: int) -> dict | None:
    queue = chat_queues.get(chat_id)

    if not queue or len(queue) < 2:
        return None

    return queue[1]


def clear_queue(chat_id: int) -> list:
    return chat_queues.pop(chat_id, [])


def clear_upcoming(chat_id: int) -> list:
    """Drop everything after the currently playing song and keep the current one."""
    queue = chat_queues.get(chat_id)

    if not queue or len(queue) < 2:
        return []

    removed = queue[1:]
    del queue[1:]
    return removed


def queue_size(chat_id: int) -> int:
    return len(chat_queues.get(chat_id, []))


def is_empty(chat_id: int) -> bool:
    return queue_size(chat_id) == 0

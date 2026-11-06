import os


def delete_file(path: str, force: bool = False) -> None:
    """Silently delete a file unless another chat is still using it."""
    try:
        if not path:
            return
        if not force:
            from ZaraMusic.core.queue import is_file_in_use
            if is_file_in_use(path):
                return
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

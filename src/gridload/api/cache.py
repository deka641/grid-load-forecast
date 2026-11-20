"""In-process cache of large response bodies, keyed by their entity tag."""

from collections import OrderedDict


class ResponseCache:
    """Serialized bodies of at least ``min_size`` bytes, at most ``capacity`` bytes in total.

    The entity tag already covers the snapshot, the path, the query and the
    build, so equal tags mean equal bodies. Entries belong to the snapshot
    passed to :meth:`select`, which empties the cache when it changes; requests
    still running on an older snapshot neither read nor store bodies. The least
    recently used entries are evicted when a new body would exceed the
    capacity. The cache is only used on the event loop, so it needs no lock.
    """

    def __init__(self, capacity: int, min_size: int) -> None:
        self.capacity = capacity
        self.min_size = min_size
        self.size = 0
        self._entries: OrderedDict[str, bytes] = OrderedDict()
        self._snapshot: tuple[int, int] | None = None

    def __len__(self) -> int:
        return len(self._entries)

    def select(self, snapshot: tuple[int, int]) -> None:
        """Make ``snapshot`` the current one; a different one empties the cache."""
        if snapshot != self._snapshot:
            self._entries.clear()
            self.size = 0
            self._snapshot = snapshot

    def get(self, snapshot: tuple[int, int], tag: str) -> bytes | None:
        """The body stored for ``tag`` of the current ``snapshot``, or None."""
        if snapshot != self._snapshot:
            return None
        body = self._entries.get(tag)
        if body is not None:
            self._entries.move_to_end(tag)
        return body

    def put(self, snapshot: tuple[int, int], tag: str, body: bytes) -> None:
        """Store ``body`` if it is large enough, fits at all and belongs to the current snapshot."""
        size = len(body)
        if (
            snapshot != self._snapshot
            or size < self.min_size
            or size > self.capacity
            or tag in self._entries
        ):
            return
        while self.size + size > self.capacity:
            _, evicted = self._entries.popitem(last=False)
            self.size -= len(evicted)
        self._entries[tag] = body
        self.size += size

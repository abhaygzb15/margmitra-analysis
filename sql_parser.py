"""Streaming reader for PostgreSQL-style INSERT dumps (DBeaver / pg_dump --inserts).

The dump is read in fixed-size pieces, so a 500 MB file never has to be held
as one decoded string. Only the requested columns are yielded.

Split part-files are supported: a part may begin in the middle of an INSERT
statement (rows with no "INSERT INTO ... VALUES" header in front of them). For
those rows DEFAULT_COLUMNS is assumed.
"""
import codecs
import re

DEFAULT_COLUMNS = [
    "uuid", "mobile", "source", "conversation",
    "last_user_text", "last_response", "created_at", "updated_at",
]
NEEDED_COLUMNS = ["source", "conversation", "last_user_text", "last_response"]

CHUNK_BYTES = 4 * 1024 * 1024

_INSERT = re.compile(
    r"INSERT\s+INTO\s+[^\s(]+\s*(?:\(([^)]*)\))?\s*VALUES", re.I | re.S)
# one value followed by its delimiter: 'quoted ''string''' or a bare token
_VALUE = re.compile(r"\s*(?:'([^']*(?:''[^']*)*)'|([^,()']*?))\s*([,)])")
_WS = re.compile(r"[\s,]*")


class SqlParseStats:
    def __init__(self):
        self.rows = 0
        self.skipped_rows = 0      # rows from other tables / wrong column count
        self.bytes_read = 0


def _parse_tuple(buf, pos):
    """buf[pos] == '('. Return (values, end_pos) or None if the row is cut off."""
    pos += 1
    values = []
    while True:
        m = _VALUE.match(buf, pos)
        if m is None:
            return None
        if m.group(1) is not None:
            values.append(m.group(1).replace("''", "'"))
        else:
            tok = m.group(2)
            values.append(None if tok.upper() == "NULL" else tok)
        pos = m.end()
        if m.group(3) == ")":
            return values, pos


def _index(cols, wanted, optional):
    """Positions of wanted + optional columns; None if a wanted one is missing."""
    if not all(c in cols for c in wanted):
        return None
    return ([cols.index(c) for c in wanted]
            + [cols.index(c) if c in cols else None for c in optional])


def iter_rows(fileobj, wanted=NEEDED_COLUMNS, stats=None, progress=None,
              optional=()):
    """Yield one tuple of `wanted` (+ `optional`) column values per row.

    Columns in `optional` come back as None when the dump does not have them.

    fileobj  : binary file-like object
    progress : optional callable(bytes_read)
    """
    stats = stats or SqlParseStats()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    ncols = len(DEFAULT_COLUMNS)
    index = _index(DEFAULT_COLUMNS, wanted, optional)
    in_values = True          # a split part may start with bare rows
    buf = ""
    eof = False

    while not eof:
        raw = fileobj.read(CHUNK_BYTES)
        if not raw:
            eof = True
            buf += decoder.decode(b"", final=True)
        else:
            stats.bytes_read += len(raw)
            buf += decoder.decode(raw)
        pos = 0
        n = len(buf)
        while True:
            pos = _WS.match(buf, pos).end()
            if pos >= n:
                break
            ch = buf[pos]
            if ch == "(" and in_values:
                res = _parse_tuple(buf, pos)
                if res is None:
                    if eof:               # truncated final row
                        stats.skipped_rows += 1
                        pos = n
                    break                 # need more data
                values, pos = res
                if index is not None and len(values) == ncols:
                    stats.rows += 1
                    yield tuple(None if i is None else values[i]
                                for i in index)
                else:
                    stats.skipped_rows += 1
            elif ch == ";":
                in_values = False
                pos += 1
            elif buf.startswith(("INSERT", "insert", "Insert"), pos):
                m = _INSERT.match(buf, pos)
                if m is None:
                    if not eof and n - pos < 20000:
                        break             # header may be cut off; read more
                    nl = buf.find("\n", pos)
                    pos = n if nl < 0 else nl + 1
                    continue
                if m.group(1) is not None:
                    cols = [c.strip().strip('"`').lower()
                            for c in m.group(1).split(",")]
                else:
                    cols = DEFAULT_COLUMNS
                ncols = len(cols)
                index = _index(cols, wanted, optional)
                in_values = True
                pos = m.end()
            else:
                # comment or some other statement: skip the line
                nl = buf.find("\n", pos)
                if nl < 0:
                    if eof:
                        pos = n
                    break
                if not buf.startswith("--", pos):
                    in_values = False
                pos = nl + 1
        buf = buf[pos:]
        if progress:
            progress(stats.bytes_read)

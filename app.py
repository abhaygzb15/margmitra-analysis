"""MargMitra AI - Query Analysis Dashboard.

Upload the gemini_conversation_logs SQL dump (one file, its split parts, or a
zip of them). The app streams through it, keeps only the four needed columns,
offers them as a CSV and renders the dashboard. Nothing is kept after the
session ends.
"""
import csv
import hmac
import json
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path

import streamlit as st

from analysis import Aggregator
from sql_parser import NEEDED_COLUMNS, SqlParseStats, iter_rows

TEMPLATE = Path(__file__).with_name("dashboard_template.html")

st.set_page_config(page_title="MargMitra AI - Query Analysis",
                   page_icon="🛣️", layout="wide")


# ------------------------------------------------------------ optional login
def _configured_password():
    try:
        return st.secrets.get("APP_PASSWORD")
    except Exception:          # no secrets file at all
        return None


def require_password():
    expected = _configured_password()
    if not expected or st.session_state.get("authed"):
        return
    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, str(expected)):
        st.session_state["authed"] = True
        st.rerun()
    if entered:
        st.error("Wrong password.")
    st.stop()


# ------------------------------------------------------------ processing
def _natural(name):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _pretty(day):
    y, m, d = day.split("-")
    return f"{int(d)} {_MONTHS[int(m) - 1]} {y}" if 1 <= int(m) <= 12 else day


def _sources(uploads):
    """Yield (name, size, opener) for every .sql inside the uploads."""
    for up in sorted(uploads, key=lambda u: _natural(u.name)):
        if up.name.lower().endswith(".zip"):
            zf = zipfile.ZipFile(up)
            members = [i for i in zf.infolist()
                       if i.filename.lower().endswith(".sql") and not i.is_dir()]
            for info in sorted(members, key=lambda i: _natural(i.filename)):
                yield info.filename, info.file_size, (lambda z=zf, i=info: z.open(i))
        else:
            up.seek(0)
            yield up.name, up.size, (lambda u=up: u)


def process(uploads, on_progress):
    sources = list(_sources(uploads))
    total = sum(size for _, size, _ in sources) or 1
    agg = Aggregator()
    stats = SqlParseStats()
    fd, csv_path = tempfile.mkstemp(prefix="margmitra_", suffix=".csv")
    done = 0
    first = last = None
    with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(NEEDED_COLUMNS)
        for name, size, opener in sources:
            before = stats.bytes_read
            with opener() as fh:
                rows = iter_rows(
                    fh, stats=stats, optional=("created_at",),
                    progress=lambda b, d=done, s=before, n=name:
                        on_progress((d + b - s) / total, n))
                for *row, created in rows:
                    writer.writerow(["" if v is None else v for v in row])
                    agg.add(*row)
                    day = (created or "")[:10]
                    if _DAY.fullmatch(day):
                        first = day if first is None or day < first else first
                        last = day if last is None or day > last else last
            done += size
    data = agg.result()
    names = [n.rsplit("/", 1)[-1] for n, _, _ in sources]
    label = names[0] if len(names) == 1 else f"{len(names)} SQL files"
    if first:
        data["date_range"] = [_pretty(first), _pretty(last)]
    data["source_label"] = f"{label} ({stats.rows:,} rows)"
    return {
        "json": json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"),
        "csv_path": csv_path,
        "csv_bytes": os.path.getsize(csv_path),
        "rows": stats.rows,
        "skipped": stats.skipped_rows,
        "bad": agg.bad_rows,
        "files": len(sources),
    }


def _discard(result):
    if result:
        try:
            os.remove(result["csv_path"])
        except OSError:
            pass


# ------------------------------------------------------------ page
require_password()

st.title("MargMitra AI — Query Analysis Dashboard")
uploads = st.file_uploader(
    "Upload the conversation-log SQL dump",
    type=["sql", "zip"], accept_multiple_files=True,
    help="One .sql dump, several split parts, or a .zip containing them. "
         "Needs the gemini_conversation_logs INSERT rows.")

if not uploads:
    _discard(st.session_state.pop("result", None))
    st.session_state.pop("result_key", None)
    st.info("No data loaded. Upload a .sql dump to build the dashboard. "
            "Nothing is stored: the data lives only in this browser session.")
    st.stop()

key = tuple(sorted((u.name, u.size) for u in uploads))
if st.session_state.get("result_key") != key:
    _discard(st.session_state.pop("result", None))
    bar = st.progress(0.0, text="Reading dump…")
    started = time.time()
    try:
        result = process(
            uploads,
            lambda frac, name: bar.progress(min(frac, 1.0), text=f"Parsing {name}…"))
    except zipfile.BadZipFile:
        bar.empty()
        st.error("That .zip file could not be opened.")
        st.stop()
    bar.empty()
    result["seconds"] = time.time() - started
    st.session_state["result"] = result
    st.session_state["result_key"] = key

result = st.session_state["result"]
if not result["rows"]:
    st.error(
        "No usable rows found. The file must contain INSERT statements with the "
        "columns: " + ", ".join(NEEDED_COLUMNS) + ". "
        f"({result['skipped']:,} rows were skipped because their columns did not match.)")
    st.stop()

left, right = st.columns([3, 1], vertical_alignment="center")
note = (f"Parsed **{result['rows']:,}** conversations from "
        f"{result['files']} file(s) in {result['seconds']:.0f}s.")
if result["skipped"]:
    note += f" {result['skipped']:,} rows skipped (other tables or cut-off rows)."
if result["bad"]:
    note += f" {result['bad']:,} rows had an unreadable last_response and are counted as unclear."
left.markdown(note)
right.download_button(
    f"Download CSV ({result['csv_bytes'] / 1e6:.0f} MB)",
    data=lambda p=result["csv_path"]: open(p, "rb"),
    file_name="margmitra_conversations.csv", mime="text/csv",
    help="Columns: " + ", ".join(NEEDED_COLUMNS) + ". Unmasked, as in the dump.",
    on_click="ignore", width="stretch")

html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", result["json"])
st.iframe(html, height="content")

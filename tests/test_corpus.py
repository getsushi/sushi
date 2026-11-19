"""The company corpus under concurrent readers and writers.

Every writer of ``companies.jsonl`` does read, change, write, and the middle
step is minutes of network work — ``resolve_boards`` probing careers pages
while the sweep harvests employers off the global feeds. So two of them
overlap routinely, and before :func:`editing_companies` that meant:

- both writing the same fixed ``.tmp`` path at once and each renaming it
  over the corpus, so what landed was two interleaved JSON streams which
  ``load_companies`` then skipped line by line — companies vanishing from a
  26,000-row file with nothing logged; and
- whoever saved last discarding everything the other had added. Measured
  with four writers making six additions each, ten of twenty-four survived.

Neither failed loudly, which is why they are tested here rather than left to
be noticed.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sushi import discovery  # noqa: E402


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A corpus of ten companies in a temp directory, and a cold cache."""
    path = tmp_path / "companies.jsonl"
    path.write_text("\n".join(
        '{"key": "co%d", "name": "Co %d", "ats": "lever", "token": "t%d",'
        ' "sources": [], "locations": []}' % (i, i, i) for i in range(10)
    ) + "\n", encoding="utf-8")
    monkeypatch.setattr(discovery, "COMPANIES_PATH", path)
    monkeypatch.setattr(discovery, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(discovery, "_CORPUS", None)
    monkeypatch.setattr(discovery, "_CORPUS_STAMP", None)
    return path


def test_each_caller_gets_its_own_copy(corpus):
    """The cache is shared; what comes out of it must not be."""
    mine = discovery.load_companies()
    mine["co0"]["name"] = "MUTATED"
    mine["invented"] = {"key": "invented"}

    theirs = discovery.load_companies()
    assert theirs["co0"]["name"] == "Co 0"
    assert "invented" not in theirs


def test_a_changed_file_is_re_read(corpus):
    discovery.load_companies()
    # Same size, new content: the stamp carries mtime as well for this.
    time.sleep(0.01)
    corpus.write_text(corpus.read_text(encoding="utf-8")
                      .replace('"Co 0"', '"Co X"'), encoding="utf-8")
    assert discovery.load_companies()["co0"]["name"] == "Co X"


def test_editing_keeps_what_other_writers_did(corpus):
    """Additions and record edits from overlapping writers all survive."""
    def add(n: int) -> None:
        for i in range(6):
            with discovery.editing_companies() as cos:
                time.sleep(0.01)          # stands in for the network
                cos[f"new{n}-{i}"] = {"key": f"new{n}-{i}", "name": f"New {n}",
                                      "ats": "lever", "token": f"n{n}{i}",
                                      "sources": [], "locations": []}

    def probe(n: int) -> None:
        with discovery.editing_companies() as cos:
            time.sleep(0.02)
            cos[f"co{n}"]["probed_at"] = f"probe-{n}"

    threads = ([threading.Thread(target=add, args=(n,)) for n in range(4)]
               + [threading.Thread(target=probe, args=(n,)) for n in range(3)])
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    discovery._CORPUS = None              # read what is actually on disk
    on_disk = discovery.load_companies()
    assert len([k for k in on_disk if k.startswith("new")]) == 24
    assert [on_disk[f"co{n}"].get("probed_at") for n in range(3)] == [
        "probe-0", "probe-1", "probe-2"]
    assert len(on_disk) == 34


def test_a_reader_never_sees_a_half_written_corpus(corpus):
    """And no temp file is left behind to be parsed as part of it."""
    stop = threading.Event()
    seen: list[int] = []

    def read() -> None:
        while not stop.is_set():
            seen.append(len(discovery.load_companies()))

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for n in range(20):
            with discovery.editing_companies() as cos:
                cos[f"new{n}"] = {"key": f"new{n}", "name": "New",
                                  "ats": "lever", "token": f"n{n}",
                                  "sources": [], "locations": []}
    finally:
        stop.set()
        reader.join()

    assert seen and min(seen) >= 10, f"the corpus shrank to {min(seen)}"
    assert not list(corpus.parent.glob("*.tmp"))


def test_a_dropped_company_stays_dropped(corpus):
    with discovery.editing_companies() as cos:
        del cos["co3"]
    discovery._CORPUS = None
    assert "co3" not in discovery.load_companies()

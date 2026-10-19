"""A profile's notes as topic files, with an index built from them.

Notes used to be one `notes.md`, read whole at the start of every job. A base
that has been designed for a while collects far more than is worth reading each
time, so notes may now be any number of Markdown files under the profile's
`notes/` folder (a lone `notes.md` of an older profile counts as one more
topic). What gets read first is an index: one line per file, and one per
section of a file that has several, saying what it is about and which items it
mentions. The note itself is opened by topic.

Items are written as internal names (`advanced-circuit`) so a search in the
player's words finds them through the same lookup the rest of the tool uses:
"красная схема" is slang for `advanced-circuit`, so a note about those is found
by it even if it never writes the internal name. Where a note says what it is
about, that is taken as given: a `tags:` line (in a front-matter block at the
top of a file, or anywhere in a section) names topics, and a `summary:` line
replaces the first sentence as the description.

The index is cached in the profile and rebuilt only when a note file is added,
removed or changed (names, modification times and sizes).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import names, slang

NOTES_DIR = "notes"
LEGACY_FILE = "notes.md"
INDEX_FILE = "notes-index.json"
INDEX_VERSION = 1

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_META = re.compile(r"^\s*(tags|summary|title)\s*:\s*(.*)$", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z][a-z0-9_-]*")
_ITEM_KINDS = ("item", "fluid", "entity")
_SUMMARY_LENGTH = 90
_SHOWN_ITEMS = 12


@dataclass
class Entry:
    file: str  # path under the profile folder, with forward slashes
    heading: str  # "" for the file as a whole
    title: str
    summary: str
    tags: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    start: int = 0  # first line of the section (file entries: 0)
    end: int = 0  # one past its last line


# --------------------------------------------------------------------------
# files and fingerprint
# --------------------------------------------------------------------------


def note_files(profile_dir: Path) -> list[Path]:
    """Every note file of a profile: the legacy `notes.md`, then `notes/**/*.md`."""
    files: list[Path] = []
    legacy = profile_dir / LEGACY_FILE
    if legacy.is_file():
        files.append(legacy)
    folder = profile_dir / NOTES_DIR
    if folder.is_dir():
        files.extend(sorted(p for p in folder.rglob("*.md") if p.is_file()))
    return files


def _relative(profile_dir: Path, path: Path) -> str:
    return path.relative_to(profile_dir).as_posix()


def _slang_digest() -> str:
    return hashlib.sha256(repr(slang.TERMS).encode("utf-8")).hexdigest()[:16]


def fingerprint(profile_dir: Path, salt: str = "") -> str:
    """What the index depends on: the note files, the slang table and `salt`.

    The labels also depend on the game data the lookup reads, so the caller
    passes what identifies it (profile name and data digest) as `salt`.
    """
    parts = [f"v{INDEX_VERSION}", f"slang:{_slang_digest()}", f"salt:{salt}"]
    for path in note_files(profile_dir):
        stat = path.stat()
        parts.append(f"{_relative(profile_dir, path)}|{stat.st_mtime_ns}|{stat.st_size}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# reading one file
# --------------------------------------------------------------------------


def _split_front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    """The `key: value` block between `---` lines at the top, and where the body starts."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    meta: dict[str, str] = {}
    for number in range(1, len(lines)):
        if lines[number].strip() == "---":
            return meta, number + 1
        match = _META.match(lines[number])
        if match:
            meta[match.group(1).lower()] = match.group(2).strip()
    return {}, 0  # never closed: it was an ordinary rule, not front matter


def _split_list(value: str) -> list[str]:
    return [part.strip().strip("`") for part in re.split(r"[,;]", value) if part.strip().strip("`")]


def _summary_of(lines: list[str]) -> str:
    """The first sentence of the first plain paragraph."""
    in_fence = False
    paragraph: list[str] = []
    for raw in lines:
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        line = raw.strip()
        if in_fence or _HEADING.match(line) or _META.match(line) or line.startswith(("---", "|", "<!--")):
            if paragraph:
                break
            continue
        if not line:
            if paragraph:
                break
            continue
        paragraph.append(re.sub(r"^[-*+>\d.)\s]+", "", line))
    text = re.sub(r"[*_`]", "", " ".join(paragraph))
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(sentence) > _SUMMARY_LENGTH:
        sentence = sentence[: _SUMMARY_LENGTH - 1].rstrip() + "…"
    return sentence


def _text_ngrams(words: list[str], longest: int) -> dict[tuple[str, ...], int]:
    grams: dict[tuple[str, ...], int] = {}
    for size in range(1, longest + 1):
        for start in range(len(words) - size + 1):
            grams.setdefault(tuple(words[start : start + size]), start)
    return grams


def _slang_phrases() -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """(stemmed words of a phrase, the names it stands for), vague one-word terms left out."""
    phrases = []
    for term in slang.TERMS:
        vague = len(term.names) > 1  # one word for several things: the player is to be asked
        for phrase in term.phrases:
            words = tuple(names._stem(w) for w in names._words(phrase))
            if words and not (vague and len(words) == 1):
                phrases.append((words, term.names))
    return phrases


def items_in(text: str) -> list[str]:
    """Internal names of what a text mentions: names written out, and names players say.

    The second needs the slang table; "красная схема" gives `advanced-circuit`.
    A slang name is kept even when the active mod set lacks it -- the note was
    still about it -- while a written-out name must exist in the data.
    """
    found: dict[str, None] = {}
    for token in _TOKEN.findall(text.lower()):
        token = token.strip("-_")
        if token and any(isinstance(names._raw(kind).get(token), dict) for kind in _ITEM_KINDS):
            found.setdefault(token)

    words = [names._stem(w) for w in names._words(text)]
    phrases = _slang_phrases()
    longest = max((len(p) for p, _ in phrases), default=1)
    grams = _text_ngrams(words, longest)
    hits = [(grams[p], len(p), term_names) for p, term_names in phrases if p in grams]
    spans = []
    for start, size, term_names in hits:
        # a phrase inside a longer one found in the same place is the longer's
        # («схемы» inside «красные схемы»)
        if any(o_size > size and o_start <= start and start + size <= o_start + o_size for o_start, o_size, _ in hits):
            continue
        spans.append(term_names)
    for term_names in spans:
        for name in term_names:
            found.setdefault(name)
    return list(found)


def _resolve_tag(tag: str) -> list[str]:
    """What a tag means as internal names: itself when it is one, else the lookup's best."""
    if any(isinstance(names._raw(kind).get(tag), dict) for kind in _ITEM_KINDS):
        return [tag]
    try:
        candidates = names.find(tag, kinds=_ITEM_KINDS, limit=3)
    except Exception:  # noqa: BLE001 - no game data: the tag stays a plain word
        return []
    return [c.name for c in candidates if c.score >= 0.9][:2]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def parse_file(profile_dir: Path, path: Path) -> list[Entry]:
    """The index entries of one note file: itself, and each `##` section if there are two or more."""
    relative = _relative(profile_dir, path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    meta, body_start = _split_front_matter(lines)

    # headings outside code fences
    in_fence = False
    h1_title = ""
    sections: list[tuple[int, str]] = []  # (line number, heading)
    for number in range(body_start, len(lines)):
        line = lines[number]
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        if len(match.group(1)) == 1 and not h1_title:
            h1_title = match.group(2)
        elif len(match.group(1)) == 2:
            sections.append((number, match.group(2)))

    title = meta.get("title") or h1_title or Path(relative).stem.replace("-", " ").replace("_", " ")
    file_tags = _split_list(meta.get("tags", ""))
    first_section = sections[0][0] if sections else len(lines)
    intro = lines[body_start:first_section]
    for line in intro:
        match = _META.match(line)
        if match and match.group(1).lower() == "tags":
            file_tags += _split_list(match.group(2))
        elif match and match.group(1).lower() == "summary" and "summary" not in meta:
            meta["summary"] = match.group(2).strip()
    file_tags = _dedupe(file_tags)

    whole = "\n".join(lines[body_start:])
    file_items = _dedupe(
        [n for tag in file_tags for n in _resolve_tag(tag)] + items_in(" ".join([title, whole]))
    )
    entries = [
        Entry(
            relative, "", title, meta.get("summary") or _summary_of(intro) or _summary_of(lines[body_start:]),
            file_tags, file_items, 0, len(lines),
        )
    ]
    if len(sections) >= 2:
        bounds = [number for number, _ in sections] + [len(lines)]
        for index, (number, heading) in enumerate(sections):
            chunk = lines[number + 1 : bounds[index + 1]]
            tags: list[str] = []
            summary = ""
            for line in chunk:
                match = _META.match(line)
                if match and match.group(1).lower() == "tags":
                    tags += _split_list(match.group(2))
                elif match and match.group(1).lower() == "summary" and not summary:
                    summary = match.group(2).strip()
            tags = _dedupe(tags)
            items = _dedupe([n for tag in tags for n in _resolve_tag(tag)] + items_in(heading + " " + "\n".join(chunk)))
            entries.append(
                Entry(relative, heading, heading, summary or _summary_of(chunk), tags, items, number, bounds[index + 1])
            )
    return entries


# --------------------------------------------------------------------------
# the index
# --------------------------------------------------------------------------


def build_index(profile_dir: Path) -> list[Entry]:
    entries: list[Entry] = []
    for path in note_files(profile_dir):
        entries.extend(parse_file(profile_dir, path))
    return entries


def load_index(profile_dir: Path, salt: str = "", use_cache: bool = True) -> tuple[list[Entry], bool]:
    """The index, and whether it was rebuilt (as opposed to read from the cache).

    `use_cache=False` builds fresh and writes nothing: for a profile whose game
    data is not the data currently loaded, whose labels would be wrong.
    """
    current = fingerprint(profile_dir, salt)
    cache = profile_dir / INDEX_FILE
    if not use_cache:
        return build_index(profile_dir), True
    try:
        stored = json.loads(cache.read_text(encoding="utf-8"))
        if stored.get("fingerprint") == current and stored.get("version") == INDEX_VERSION:
            return [Entry(**e) for e in stored["entries"]], False
    except (OSError, ValueError, KeyError, TypeError):
        pass
    entries = build_index(profile_dir)
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {"version": INDEX_VERSION, "fingerprint": current, "entries": [asdict(e) for e in entries]},
                indent=1,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # a read-only profile still gets its index, just not cached
    return entries, True


def unindexed(profile_dir: Path, entries: list[Entry]) -> list[str]:
    """Note files with no line in the index; empty when the index is complete."""
    covered = {e.file for e in entries}
    return [_relative(profile_dir, p) for p in note_files(profile_dir) if _relative(profile_dir, p) not in covered]


def format_line(entry: Entry) -> str:
    where = entry.file + (f"#{entry.heading}" if entry.heading else "")
    about = entry.title if not entry.heading else ""
    parts = [f"{where} — " + (about + (": " if about and entry.summary else "") + entry.summary).strip()]
    if entry.tags:
        parts.append("tags: " + ", ".join(entry.tags))
    if entry.items:
        shown = entry.items[:_SHOWN_ITEMS]
        extra = len(entry.items) - len(shown)
        parts.append("items: " + ", ".join(shown) + (f" (+{extra})" if extra else ""))
    return " | ".join(parts)


def format_index(entries: list[Entry]) -> str:
    return "\n".join(format_line(e) for e in entries)


# --------------------------------------------------------------------------
# looking a topic up
# --------------------------------------------------------------------------


def _stems(text: str) -> list[str]:
    return [names._stem(w) for w in names._words(text)]


def _label_match(topic: list[str], labels: list[str]) -> bool:
    """Every word of the topic starts, or is started by, a word of some label."""
    if not topic:
        return False
    label_stems = [s for label in labels for s in _stems(label)]
    return all(
        any(s == t or (len(t) >= 3 and len(s) >= 3 and (s.startswith(t) or t.startswith(s))) for s in label_stems)
        for t in topic
    )


def topic_items(topic: str) -> set[str]:
    """Internal names the topic may mean: the topic itself, and the lookup's close matches."""
    found: set[str] = set()
    for token in _TOKEN.findall(topic.lower()):
        if any(isinstance(names._raw(kind).get(token), dict) for kind in _ITEM_KINDS):
            found.add(token)
    try:
        found.update(c.name for c in names.find(topic, kinds=_ITEM_KINDS, limit=4) if c.score >= 0.85)
    except Exception:  # noqa: BLE001 - without game data only the plain words work
        pass
    return found


def lookup(profile_dir: Path, entries: list[Entry], topic: str) -> list[tuple[Entry, str]]:
    """The notes about a topic: (entry, text). Whole files that match by name, title or
    tag; otherwise the matching sections, or the file when it has none."""
    words = _stems(topic)
    wanted = topic_items(topic)
    by_file: dict[str, list[Entry]] = {}
    for entry in entries:
        by_file.setdefault(entry.file, []).append(entry)

    results: list[tuple[Entry, str]] = []
    for file, group in by_file.items():
        head = group[0]
        sections = group[1:]
        label = _label_match(words, [Path(file).stem, head.title] + head.tags)
        by_item = bool(wanted & set(head.items)) if not sections else False
        path = profile_dir / file
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if label or by_item:
            results.append((head, "\n".join(lines)))
            continue
        for entry in sections:
            if _label_match(words, [entry.heading] + entry.tags) or wanted & set(entry.items):
                results.append((entry, "\n".join(lines[entry.start : entry.end])))
    return results

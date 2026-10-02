"""What the player calls things, matched to what the data calls them.

A request arrives in the player's words -- "красные колбы", "green circuits",
"быстрая лента" -- and every tool after it wants prototype names:
`automation-science-pack`, `electronic-circuit`, `fast-transport-belt`. Under
a mod set the gap is wider: Krastorio 2 renames the red science pack to a
tech card, so neither the vanilla name nor the vanilla translation finds it.

The game's own translations settle it. They live in the locale files of the
game's packages (`data/<package>/locale/<language>/*.cfg`) and of every mod in
the active profile (inside the mod's archive), as `[section]` blocks of
`key=value`. A prototype's displayed name is `<kind>-name.<name>` unless it
names another key in `localised_name`, as Krastorio 2 does; an item with no
name of its own shows the name of the entity it places, and a recipe shows its
product's.

Three ways in, tried together:

- **slang** -- `slang.py` writes down how players talk ("красные колбы",
  "мазут", "green circuits") against the base game's internal names, which
  mods keep even when they rename what is shown;
- **the name shown in this mod set** -- the game's translations with the
  mods' on top;
- **the base game's own name** -- the translations of the game's packages
  alone. A mod that renames a thing would otherwise hide it from anyone using
  the name everybody knows; such a match says so, with both names.

Matching is deliberately plain -- lower case, word endings dropped -- because
the one choosing between candidates is a model that knows what the player
meant. This module's job is to put the real candidates in front of it, with
what they are called and why each was found.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from draftsman.data import entities as entity_data
from draftsman.data import fluids as fluid_data
from draftsman.data import items as item_data
from draftsman.data import recipes as recipe_data

from . import paths, slang
from .categories import recipe_categories

KINDS = ("item", "fluid", "recipe", "entity")


@dataclass(frozen=True)
class Candidate:
    name: str
    kinds: tuple[str, ...]  # what the name is: an item and its recipe share one
    titles: dict[str, str]  # language -> name shown in this mod set
    score: float
    unlocked: bool | None = None  # None: no environment to say
    matched: str = "name"  # "slang", "internal", "name" (as shown here) or "vanilla"
    via: str = ""  # why it was found, when that is not simply the shown name
    vanilla_titles: dict[str, str] = field(default_factory=dict)  # only where they differ


@dataclass
class Locale:
    """Every translation found, as language -> "section.key" -> text."""

    strings: dict[str, dict[str, str]] = field(default_factory=dict)

    def get(self, language: str, key: str) -> str | None:
        return self.strings.get(language, {}).get(key)


def parse_cfg(text: str, into: dict[str, str]) -> None:
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        key, sep, value = line.partition("=")
        if sep and section:
            into[f"{section}.{key.strip()}"] = value.strip()


def _profile_mods_dir() -> Path | None:
    try:
        from .profile import Profile

        name = Profile.active_profile_name()
        return Profile.load(name).mods_dir if name else None
    except Exception:  # noqa: BLE001 - no profile means only the game's own names
        return None


@lru_cache(maxsize=8)
def load_locale(languages: tuple[str, ...] = ("ru", "en"), with_mods: bool = True) -> Locale:
    """Translations from the game's packages, and the active profile's mods.

    Mods are read after the game so a mod's text for a key wins, as it does in
    the game. `with_mods=False` gives the base game's own names.
    """
    locale = Locale({language: {} for language in languages})
    data_dir = paths.factorio_data_dir()
    if data_dir is not None and data_dir.is_dir():
        for package in sorted(data_dir.iterdir()):
            for language in languages:
                folder = package / "locale" / language
                if folder.is_dir():
                    for cfg in sorted(folder.glob("*.cfg")):
                        parse_cfg(cfg.read_text(encoding="utf-8", errors="replace"), locale.strings[language])
    mods_dir = _profile_mods_dir() if with_mods else None
    if mods_dir is not None and mods_dir.is_dir():
        for archive_path in sorted(mods_dir.glob("*.zip")):
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    for member in sorted(archive.namelist()):
                        parts = member.split("/")
                        if len(parts) == 4 and parts[1] == "locale" and parts[2] in languages and parts[3].endswith(".cfg"):
                            text = archive.read(member).decode("utf-8", errors="replace")
                            parse_cfg(text, locale.strings[parts[2]])
            except (OSError, zipfile.BadZipFile):
                continue
    return locale


def _raw(kind: str) -> dict:
    return {
        "item": item_data.raw,
        "fluid": fluid_data.raw,
        "recipe": recipe_data.raw,
        "entity": entity_data.raw,
    }[kind]


def resolve(value, language: str, locale: Locale) -> str | None:
    """A LocalisedString as the game would show it: a key, then its parameters.

    `["recipe-name.kr-crush", ["item-name.electronic-circuit"]]` becomes
    "Crush Electronic circuit": each `__N__` in the key's text is replaced by
    the N-th parameter, itself resolved the same way. An empty key joins the
    parameters, as the game does.
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, (list, tuple)) or not value or not isinstance(value[0], str):
        return None
    key, params = value[0], [resolve(p, language, locale) or "" for p in value[1:]]
    if key == "":
        return "".join(params)
    text = locale.get(language, key)
    if text is None:
        return None
    return re.sub(
        r"__(\d+)__",
        lambda m: params[int(m.group(1)) - 1] if int(m.group(1)) <= len(params) else m.group(0),
        text,
    )


def title(kind: str, name: str, language: str, locale: Locale) -> str | None:
    """The name the game shows for a prototype in a language, if it has one."""
    data = _raw(kind).get(name) or {}
    localised = data.get("localised_name")
    if isinstance(localised, (list, tuple)):
        text = resolve(localised, language, locale)
        if text:
            return text
    if isinstance(localised, str) and localised and "." in localised:
        text = locale.get(language, localised)
        if text:
            return text
    text = locale.get(language, f"{kind}-name.{name}")
    if text:
        return text
    if kind == "item":
        placed = data.get("place_result")
        if placed:
            return title("entity", placed, language, locale)
        equipment = data.get("place_as_equipment_result")
        if equipment:
            return locale.get(language, f"equipment-name.{equipment}")
    if kind == "recipe":
        results = data.get("results") or []
        main = data.get("main_product") or (results[0].get("name") if len(results) == 1 else None)
        if main:
            product_kind = "fluid" if main in fluid_data.raw and main not in item_data.raw else "item"
            return title(product_kind, main, language, locale)
    if kind == "entity":
        for item_name, item in item_data.raw.items():
            if isinstance(item, dict) and item.get("place_result") == name:
                return locale.get(language, f"item-name.{item_name}")
    return None


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

_WORD = re.compile(r"[\w]+", re.UNICODE)

_ENDINGS = tuple(
    sorted(
        "ыми ими ого его ому ему ая яя ое ее ые ие ый ий ой ую юю ам ям ах ях ов ев ом ем ей "
        "ы и а я о е у ю ь es s".split(),
        key=len,
        reverse=True,
    )
)


def _words(text: str) -> list[str]:
    text = text.lower().replace("ё", "е").replace("-", " ").replace("_", " ")
    text = re.sub(r"\[[^\]]*\]", " ", text)  # rich text tags such as [item=iron-plate]
    return _WORD.findall(text)


def _stem(word: str) -> str:
    """The word without its ending, so "синие", "синяя" and "синий" meet.

    Russian adjective and noun endings, and an English plural. At least three
    letters are kept; a short word is left alone.
    """
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _score(query: list[str], haystacks: list[str]) -> float:
    best = 0.0
    for text in haystacks:
        words = _words(text)
        if not words:
            continue
        stems = [_stem(w) for w in words]
        hits = 0.0
        for q in query:
            stem = _stem(q)
            if q in words:
                hits += 1.0
            elif any(s.startswith(stem) or stem.startswith(s) for s in stems if len(s) >= 3 and len(stem) >= 3):
                hits += 0.8
        if hits:
            coverage = hits / len(query)
            tightness = min(1.0, len(query) / len(words))
            best = max(best, coverage * (0.75 + 0.25 * tightness))
    return best


def is_unlocked(kind: str, name: str, environment, raw: frozenset[str] = frozenset()) -> bool | None:
    """Whether the player can have this now, by the recipes their force has enabled.

    `raw` is what comes out of the ground or a pump rather than a recipe (see
    `bom._mined_items`); ore has no recipe and is not locked for it.
    """
    if environment is None:
        return None
    if kind == "recipe":
        return name in environment.recipes_enabled
    if kind in ("item", "fluid"):
        return name in environment.makeable or name in raw
    return environment.can_build(name)


def slang_matches(text: str) -> list[tuple[slang.Term, str]]:
    """The slang terms these words are, with the phrase that matched.

    A phrase matches when every one of its words is among the query's (endings
    aside) and the query has at most one word more ("нужны красные колбы"
    still finds red science). Only the longest phrases found are kept, so
    "синие схемы" wins over the colourless "схемы".
    """
    query = _words(text)
    if not query:
        return []
    stems = {_stem(q) for q in query} | set(query)
    found: list[tuple[int, slang.Term, str]] = []
    for term in slang.TERMS:
        best: list[str] | None = None
        best_phrase = ""
        for phrase in term.phrases:
            words = _words(phrase)
            if not words or len(query) > len(words) + 1:
                continue
            if all(w in stems or _stem(w) in stems for w in words):
                if best is None or len(words) > len(best):
                    best, best_phrase = words, phrase
        if best is not None:
            found.append((len(best), term, best_phrase))
    if not found:
        return []
    longest = max(size for size, _, _ in found)
    return [(term, phrase) for size, term, phrase in found if size == longest]


def absent_slang(text: str) -> list[tuple[str, str, str]]:
    """Slang recognised whose names this mod set does not have: (phrase, names, note).

    "rcu" is known slang for the rocket control unit, which Factorio 2.0
    removed. Finding nothing is then not "the player misspoke" but "that thing
    does not exist here", which is worth saying.
    """
    missing = []
    for term, phrase in slang_matches(text):
        if not any(_present(name, KINDS) for name in term.names):
            missing.append((phrase, ", ".join(term.names), term.note))
    return missing


def _present(name: str, kinds: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(kind for kind in kinds if isinstance(_raw(kind).get(name), dict))


def find(
    text: str,
    kinds: tuple[str, ...] = KINDS,
    languages: tuple[str, ...] = ("ru", "en"),
    limit: int = 12,
    environment=None,
    raw: frozenset[str] = frozenset(),
) -> list[Candidate]:
    """Prototypes the words may mean, best first, each saying why it was found."""
    query = _words(text)
    if not query:
        return []
    locale = load_locale(languages)
    vanilla = load_locale(languages, with_mods=False)
    found: dict[str, Candidate] = {}

    for rank, (term, phrase) in enumerate(slang_matches(text)):
        # Slang that names one thing and covers every word outranks any name
        # match; with a word left over it ranks as a good name match; a vague
        # term ("колбы", "ленты") lists its options below a precise match.
        exact = len(_words(phrase)) == len(query)
        base = 1.5 if exact else 1.0
        if len(term.names) > 1 and term.note.startswith("which"):
            base = 0.9
        for order, name in enumerate(term.names):
            present = _present(name, kinds)
            if not present or name in found:
                continue
            titles = {lang: t for lang in languages if (t := title(present[0], name, lang, locale))}
            via = f"players say «{phrase}» for this" + (f"; {term.note}" if term.note else "")
            found[name] = Candidate(
                name, present, titles, base - 0.01 * order - 0.001 * rank, matched="slang", via=via
            )

    for kind in kinds:
        for name, data in _raw(kind).items():
            if not isinstance(data, dict):
                continue
            if kind == "recipe" and "recycling" in recipe_categories(data):
                continue
            held = found.get(name)
            titles = {lang: t for lang in languages if (t := title(kind, name, lang, locale))}
            base = {lang: t for lang in languages if (t := title(kind, name, lang, vanilla))}
            differs = {lang: t for lang, t in base.items() if titles.get(lang) != t}
            shown_score = _score(query, list(titles.values()))
            vanilla_score = _score(query, list(differs.values())) if differs else 0.0
            internal_score = 0.95 * _score(query, [name])
            score = max(shown_score, vanilla_score, internal_score)
            if name == text.strip():
                score, matched = 2.0, "internal"
            elif score < 0.5:
                continue
            elif vanilla_score > shown_score and vanilla_score >= internal_score:
                matched = "vanilla"
            elif internal_score > shown_score:
                matched = "internal"
            else:
                matched = "name"
            via = ""
            if matched == "vanilla":
                shown = titles.get("ru") or titles.get("en") or name
                was = differs.get("ru") or differs.get("en")
                via = f"matched the base game's name «{was}»; this mod set shows it as «{shown}»"
            if held is not None:
                kinds_so_far = held.kinds + ((kind,) if kind not in held.kinds else ())
                if held.score >= score:
                    found[name] = Candidate(
                        name, kinds_so_far, held.titles, held.score,
                        matched=held.matched, via=held.via, vanilla_titles=held.vanilla_titles or differs,
                    )
                    continue
                if held.matched == "slang" and "; which " not in held.via:
                    # A vague term's "which one?" does not belong on a precise match.
                    via = "; ".join(v for v in (via, held.via) if v)
                found[name] = Candidate(
                    name, kinds_so_far, held.titles or titles, score,
                    matched=matched, via=via, vanilla_titles=differs,
                )
                continue
            found[name] = Candidate(name, (kind,), titles, score, matched=matched, via=via, vanilla_titles=differs)

    ordered = sorted(found.values(), key=lambda c: (-c.score, KINDS.index(c.kinds[0]), len(c.name), c.name))[:limit]
    if environment is not None:
        ordered = [
            Candidate(
                c.name, c.kinds, c.titles, c.score, is_unlocked(c.kinds[0], c.name, environment, raw),
                c.matched, c.via, c.vanilla_titles,
            )
            for c in ordered
        ]
    return ordered

"""Artist unification: one folder per person however the name is written.

Names are joined in this order:
  1. same MusicBrainz / iTunes artist id
  2. same normalized spelling (NFKC + casefold + no spaces); a trailing
     bracket in another script is an alias: "Yukiko Okada (岡田有希子)"
  3. artists.json  {"representative": ["variant", ...]}
  4. guesses (same folder + same album, different artist), applied only after
     the user confirms them, which writes them into artists.json
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import unicodedata
from collections import Counter

from scan import Track, key_of

VARIOUS = {"variousartists", "various", "va", "v.a.", "컴필레이션", "오리지널사운드트랙", "ost", "群星", "オムニバス", "ヴァリアス・アーティスト"}
_COLLAB = re.compile(r"(\bfeat\.?|\bft\.|\bfeaturing\b|\bwith\b|,|&|＆|;|/|、|\sx\s|\s×\s|\svs\.?\s)", re.IGNORECASE)
_TRAILING_BRACKET = re.compile(r"^(.*\S)\s*[(\[]([^()\[\]]+)[)\]]\s*$")


# ------------------------------------------------------------------ text
def script_of(text: str) -> str:
    """Dominant script of the letters: latin / hangul / cjk / other / none."""
    counts: Counter[str] = Counter()
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("LATIN"):
            counts["latin"] += 1
        elif name.startswith("HANGUL"):
            counts["hangul"] += 1
        elif name.startswith(("CJK", "HIRAGANA", "KATAKANA")):
            counts["cjk"] += 1
        else:
            counts["other"] += 1
    return counts.most_common(1)[0][0] if counts else "none"


def is_latin(text: str) -> bool:
    return script_of(text) == "latin"


def split_alias(name: str) -> tuple[str, str]:
    """('Yukiko Okada', '岡田有希子') for 'Yukiko Okada (岡田有希子)'. A bracket in the
    same script as the name ('Artist (Live)') or at the start ('(G)I-DLE') stays."""
    text = unicodedata.normalize("NFKC", name).strip()
    m = _TRAILING_BRACKET.match(text)
    if m:
        main, inner = m.group(1).strip(), m.group(2).strip()
        a, b = script_of(main), script_of(inner)
        if main and inner and a != b and "none" not in (a, b):
            return main, inner
    return text, ""


def normalize(name: str) -> str:
    main, _ = split_alias(name)
    return re.sub(r"\s+", "", main).casefold()


def normalize_text(text: str) -> str:
    """For titles and albums: NFKC, casefold, letters and digits only."""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(ch for ch in text if ch.isalnum())


def sanitize_name(name: str) -> str:
    """The name as the pattern writes it into a path (what is on the disk)."""
    from pattern import sanitize

    return unicodedata.normalize("NFC", sanitize(name))


def folder_key(name: str) -> str:
    """How a name compares with folder names on disk: as the pattern would write it,
    case-insensitive and NFC."""
    from pattern import sanitize

    return unicodedata.normalize("NFC", sanitize(name)).casefold()


def is_collab(name: str) -> bool:
    return bool(_COLLAB.search(name))


def is_various(name: str) -> bool:
    return normalize_text(name) in {normalize_text(v) for v in VARIOUS}


# ------------------------------------------------------------------ artists.json
def load_aliases(path: str) -> tuple[dict[str, list[str]], str]:
    """(aliases, error). A missing file is not an error."""
    if not path or not os.path.exists(path):
        return {}, ""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return {}, str(exc)
    if not isinstance(data, dict):
        return {}, "not an object"
    out: dict[str, list[str]] = {}
    for rep, variants in data.items():
        if isinstance(rep, str) and rep.strip():
            vals = variants if isinstance(variants, list) else [variants]
            out[rep.strip()] = [v.strip() for v in vals if isinstance(v, str) and v.strip()]
    return out, ""


def save_aliases(path: str, aliases: dict[str, list[str]]) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(aliases, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ------------------------------------------------------------------ index
@dataclasses.dataclass
class Cluster:
    id: int
    names: list[str]  # raw spellings, most files first
    counts: dict[str, int]
    rep: str
    via: set[str]  # "id", "norm", "alias"
    alias_key: str = ""  # the artists.json entry that joined it, if any
    files: int = 0  # distinct files using any of the names


@dataclasses.dataclass
class Guess:
    names: list[str]  # representative names of the clusters it would join
    proposed: str
    folder: str
    album: str
    files: int

    def key(self) -> tuple[str, ...]:
        return tuple(sorted(normalize(n) for n in self.names))


class _UF:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


class ArtistIndex:
    def __init__(self, tracks: list[Track], aliases: dict[str, list[str]] | None = None,
                 preference: str = "original", no_merge: list[str] | None = None,
                 rejected: list[list[str]] | None = None, existing: set[str] | None = None) -> None:
        """``existing``: folder names already on disk (see folder_key). A spelling that
        already has a folder stays the name, so a later run does not rename the folder
        just because the file counts changed (e.g. duplicates went away)."""
        self.aliases = aliases or {}
        self.preference = preference
        self.existing = existing or set()
        self.existing_exact = set(getattr(existing, "exact", ()))  # session.ExistingNames: the spellings as written
        self.no_merge = {normalize(n) for n in (no_merge or [])}
        self.rejected = {tuple(sorted(normalize(n) for n in g)) for g in (rejected or [])}
        self.counts: Counter[str] = Counter()
        order: dict[str, int] = {}
        files_of: dict[str, set[int]] = {}
        for i, tr in enumerate(tracks):
            for name in {tr.tags.artist, tr.tags.album_artist}:
                if name:
                    self.counts[name] += 1
                    order.setdefault(name, len(order))
                    files_of.setdefault(name, set()).add(i)
        self._order = order
        uf = _UF()
        via: dict[str, set[str]] = {}
        for name in self.counts:
            uf.add(name)

        def join(a: str, b: str, how: str) -> None:
            if uf.find(a) != uf.find(b):
                uf.union(a, b)
            via.setdefault(a, set()).add(how)
            via.setdefault(b, set()).add(how)

        # 1. artist ids: any shared MusicBrainz or iTunes id joins two spellings. A file
        # carrying both (music-tag-filler writes both for a merged candidate) links
        # the MusicBrainz-only and the iTunes-only files of that artist.
        by_id: dict[str, str] = {}
        for tr in tracks:
            name = tr.tags.artist
            if not name or normalize(name) in self.no_merge:
                continue
            for key in (f"mb:{tr.tags.mb_artist_id}" if tr.tags.mb_artist_id else "",
                        f"itunes:{tr.tags.itunes_artist_id}" if tr.tags.itunes_artist_id else ""):
                if not key:
                    continue
                if key in by_id and by_id[key] != name:
                    join(by_id[key], name, "id")
                by_id.setdefault(key, name)
        # 2. spelling, plus a trailing bracket in another script as an alias
        by_norm: dict[str, str] = {}
        for name in self.counts:
            if normalize(name) in self.no_merge:
                continue
            keys = [normalize(name)]
            _, alias = split_alias(name)
            if alias:
                keys.append(re.sub(r"\s+", "", alias).casefold())
            for k in keys:
                if k in by_norm and by_norm[k] != name:
                    join(by_norm[k], name, "norm")
                by_norm.setdefault(k, name)
        # 3. artists.json
        alias_of: dict[str, str] = {}
        for rep, variants in self.aliases.items():
            wanted = {normalize(v) for v in [rep, *variants]}
            present = [n for n in self.counts if normalize(n) in wanted]
            for n in present:
                alias_of[n] = rep
            for n in present[1:]:
                join(present[0], n, "alias")
            if present:
                via.setdefault(present[0], set()).add("alias")
        groups: dict[str, list[str]] = {}
        for name in self.counts:
            groups.setdefault(uf.find(name), []).append(name)
        self.clusters: list[Cluster] = []
        self._cluster_of: dict[str, Cluster] = {}
        for i, names in enumerate(sorted(groups.values(), key=lambda ns: min(order[n] for n in ns))):
            names.sort(key=lambda n: (-self.counts[n], order[n]))
            how: set[str] = set()
            for n in names:
                how |= via.get(n, set())
            alias_key = next((alias_of[n] for n in names if n in alias_of), "")
            c = Cluster(i, names, {n: self.counts[n] for n in names}, "", how, alias_key,
                        len(set().union(*(files_of[n] for n in names))))
            c.rep = alias_key or self._pick(names)
            self.clusters.append(c)
            for n in names:
                self._cluster_of[n] = c
        self.guesses = self._guess(tracks)

    def _pick(self, names: list[str]) -> str:
        """Among the spellings in the preferred script: the exact one already on the disk (a folder
        name, or the "Artist" of "Artist - Title" file names), then one whose folder exists in another
        case, then the one on most files.

        The spelling on the disk can be one no remaining file carries any more (its duplicates went to
        the duplicates folder): it joins the candidates, or the next run would rename the folder."""
        want_latin = self.preference == "latin"
        preferred = [n for n in names if is_latin(n) == want_latin and script_of(n) != "none"]
        pool = list(preferred or names)
        if self.existing_exact:
            keys = {normalize(n) for n in names}
            written = {sanitize_name(n) for n in names}

            def on_disk_keys(e: str) -> set[str]:
                # "Minako Yoshida (吉田美奈子)" on the disk is the same artist as the bare "吉田美奈子" tag
                alias = split_alias(e)[1]
                return {normalize(e)} | ({normalize(alias)} if alias else set())

            pool += [e for e in sorted(self.existing_exact)
                     if e not in written and on_disk_keys(e) & keys and (is_latin(e) == want_latin or not preferred)
                     and script_of(e) != "none"]

        def rank(n: str) -> int:
            if sanitize_name(n) in self.existing_exact:
                return 0
            return 1 if folder_key(n) in self.existing else 2

        def in_script(n: str) -> int:  # the preferred script wins a tie between spellings both on the disk
            return 0 if is_latin(n) == want_latin and script_of(n) != "none" else 1

        return sorted(pool, key=lambda n: (rank(n), in_script(n), -self.counts[n], self._order.get(n, 0)))[0]

    def cluster_of(self, name: str) -> Cluster | None:
        return self._cluster_of.get(name)

    def rep(self, name: str) -> str:
        c = self._cluster_of.get(name)
        return c.rep if c else name

    def merged(self) -> list[Cluster]:
        """Clusters that actually join two or more spellings or rename one."""
        return [c for c in self.clusters if len(c.names) > 1 or c.rep not in c.names]

    def _guess(self, tracks: list[Track]) -> list[Guess]:
        albums: dict[tuple[str, str], list[Track]] = {}
        for tr in tracks:
            if tr.tags.album and tr.tags.artist:
                albums.setdefault((key_of(tr.folder), normalize_text(tr.tags.album)), []).append(tr)
        out: list[Guess] = []
        seen: set[tuple[str, ...]] = set()
        for (_, _), group in albums.items():
            if any(tr.tags.compilation or (tr.tags.album_artist and is_various(tr.tags.album_artist)) for tr in group):
                continue
            album_artists = {normalize(tr.tags.album_artist) for tr in group if tr.tags.album_artist}
            if len(album_artists) > 1:
                continue
            clusters: dict[int, Cluster] = {}
            for tr in group:
                if is_collab(tr.tags.artist) or is_various(tr.tags.artist):
                    continue
                c = self._cluster_of.get(tr.tags.artist)
                if c is not None:
                    clusters[c.id] = c
            if len(clusters) < 2:
                continue
            cs = sorted(clusters.values(), key=lambda c: c.id)
            g = Guess([c.rep for c in cs], "", group[0].folder, group[0].tags.album, len(group))
            if g.key() in seen or g.key() in self.rejected:
                continue
            seen.add(g.key())
            if any(c.alias_key for c in cs):
                g.proposed = next(c.alias_key for c in cs if c.alias_key)
            else:
                names = [n for c in cs for n in c.names]
                g.proposed = self._pick(names)
            out.append(g)
        return out

    def guess_for(self, name: str) -> Guess | None:
        c = self._cluster_of.get(name)
        if c is None:
            return None
        for g in self.guesses:
            if c.rep in g.names:
                return g
        return None

    # -------------------------------------------------------------- edits
    def aliases_with(self, names: list[str], rep: str) -> dict[str, list[str]]:
        """artists.json after making ``rep`` the name for every spelling in ``names``
        (and every spelling already listed with any of them)."""
        wanted = {normalize(n) for n in names} | {normalize(rep)}
        out: dict[str, list[str]] = {}
        variants: list[str] = []
        for key, vals in self.aliases.items():
            if wanted & {normalize(v) for v in [key, *vals]}:
                variants.extend([key, *vals])
            else:
                out[key] = list(vals)
        variants.extend(names)
        seen: set[str] = set()
        clean = []
        for v in variants:
            if v != rep and v not in seen:
                seen.add(v)
                clean.append(v)
        out[rep] = clean
        return out

    def aliases_without(self, cluster: Cluster) -> dict[str, list[str]]:
        names = {normalize(n) for n in cluster.names}
        return {k: list(v) for k, v in self.aliases.items()
                if not (names & {normalize(x) for x in [k, *v]})}

    def cluster_names_for(self, guess: Guess) -> list[str]:
        out: list[str] = []
        for c in self.clusters:
            if c.rep in guess.names:
                out.extend(c.names)
        return out

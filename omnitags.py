#!/usr/bin/env python3
"""Search your Omnisphere patches by genre, mood, type and other tags.

Omnisphere stores each patch's browser tags (Genre, Mood, Type, Author,
Complexity, Description, Keywords...) inside the patch itself. This tool reads
the patch libraries already on your machine, read-only, builds a small index of
those tags, and lets you search it.

    python3 omnitags.py index                       # finds your STEAM folder
    python3 omnitags.py search --genre Film --mood Hopeful
    python3 omnitags.py search "dust bells" --type "Bells%"
    python3 omnitags.py list moods
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from urllib.parse import unquote

__version__ = "0.1.0"

PATCH_EXTENSIONS = ("prt_omn", "prt_key", "prt_trl")
DEFAULT_INDEX = "omnisphere-index.json"
MANIFEST_LIMIT = 64 * 1024 * 1024  # stop looking for </FileSystem> after this many bytes
HEAD_BYTES = 64 * 1024  # a patch's ENTRYDESCR tag sits in its first few KB

LIST_FIELDS = {"genres": "genres", "moods": "moods", "types": "types", "authors": "author",
               "complexities": "complexity", "libraries": "library", "keywords": "keywords"}


# ---------------------------------------------------------------- tags

ATTR_RE = re.compile(r'([A-Za-z_][\w-]*)="([^"]*)"')
# Split "Genre=Film;Mood=Hopeful;Description=a;b" only before something that looks like a key
PAIR_SPLIT_RE = re.compile(r";(?=\s*[A-Za-z][A-Za-z ]*=)")


def clean(value):
    return unquote(html.unescape(value)).strip()


def parse_attrib_value_data(text):
    """Turn an ATTRIB_VALUE_DATA string into a tag dict."""
    tags = {"author": "", "complexity": "", "description": "", "osc_type": "",
            "genres": [], "moods": [], "types": [], "keywords": []}
    multi = {"Genre": "genres", "Mood": "moods", "Type": "types"}
    single = {"Author": "author", "Complexity": "complexity", "Description": "description", "Osc Type": "osc_type"}
    for pair in PAIR_SPLIT_RE.split(text or ""):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key, value = key.strip(), clean(value.rstrip(";"))
        if not value:
            continue
        if key in multi:
            if value not in tags[multi[key]]:
                tags[multi[key]].append(value)
        elif key in single:
            tags[single[key]] = value
        elif key == "Keywords":
            tags["keywords"] += [k.strip() for k in value.split(",") if k.strip()]
    return tags


def read_entry(data):
    """Return the attributes of the <ENTRYDESCR ...> tag at the top of a patch, or None.

    A patch starts with a tag (<AmberPart> or <SynthMaster>) and its ENTRYDESCR
    follows within a few hundred bytes. Data that doesn't start that way means
    the offset was wrong, so it's rejected rather than searched.
    """
    if not data.lstrip()[:1] == b"<":
        return None
    start = data.find(b"<ENTRYDESCR")
    if start == -1 or start > 1024:
        return None
    end = data.find(b">", start)
    if end == -1:
        return None
    tag = data[start:end].decode("utf-8", errors="replace")
    return {k: html.unescape(v) for k, v in ATTR_RE.findall(tag)}


def patch_record(entry, name, library, source, folder):
    tags = parse_attrib_value_data(entry.get("ATTRIB_VALUE_DATA", "") if entry else "")
    record = {"name": name, "library": library, "folder": folder, "file": source}
    record.update(tags)
    record["tagged"] = entry is not None
    return record


# ---------------------------------------------------------------- library .db files

DIR_OPEN_RE = re.compile(r'<DIR\b[^>]*\bname="([^"]*)"')


def read_manifest(f):
    """Read the <FileSystem> index at the top of a library .db. Returns (text, end_byte) or (None, None)."""
    buf = b""
    while len(buf) < MANIFEST_LIMIT:
        chunk = f.read(1024 * 1024)
        if not chunk:
            break
        buf += chunk
        if b"<FileSystem" not in buf[:4096]:
            return None, None
        end = buf.find(b"</FileSystem>")
        if end != -1:
            end += len(b"</FileSystem>")
            return buf[:end].decode("utf-8", errors="replace"), end
    return None, None


def manifest_entries(text):
    """Yield (folder path list, file name, offset, size) for every file in the index, in order."""
    stack = []
    token_re = re.compile(r"<DIR\b[^>]*>|</DIR>|<FILE\b[^>]*>")
    for m in token_re.finditer(text):
        token = m.group(0)
        if token.startswith("</DIR"):
            if stack:
                stack.pop()
        elif token.startswith("<DIR"):
            dm = DIR_OPEN_RE.match(token)
            name = html.unescape(dm.group(1)) if dm else ""
            if not token.endswith("/>"):
                stack.append(name)
        else:
            attrs = dict(ATTR_RE.findall(token))
            if "offset" in attrs and "size" in attrs and "name" in attrs:
                yield list(stack), html.unescape(attrs["name"]), int(attrs["offset"]), int(attrs["size"])


def same_name(a, b):
    return a.strip().casefold() == b.strip().casefold()


def base_candidates(f, manifest_end):
    """Offsets in the index count from one of two places, depending on the library."""
    out = []
    f.seek(manifest_end)
    after = f.read(4)
    skip = len(after) - len(after.lstrip(b"\r\n"))
    out.append(manifest_end + skip)
    f.seek(manifest_end)
    window = f.read(4 * 1024 * 1024)
    amber = window.find(b"<AmberPart")
    if amber != -1:
        out.append(manifest_end + amber)
    return list(dict.fromkeys(out))


def entry_at(f, position):
    f.seek(position)
    return read_entry(f.read(HEAD_BYTES))


def index_library(path, stats):
    """Read one library .db and return a record per patch."""
    library = os.path.splitext(os.path.basename(path))[0]
    records = []
    with open(path, "rb") as f:
        text, end = read_manifest(f)
        if text is None:
            return records
        files = [e for e in manifest_entries(text) if e[1].rsplit(".", 1)[-1] in PATCH_EXTENSIONS]
        if not files:
            return records
        bases = base_candidates(f, end)
        chosen = None
        for folder, file_name, offset, _size in files:
            name = file_name.rsplit(".", 1)[0]
            tried = [chosen] + [b for b in bases if b != chosen] if chosen is not None else bases
            entry = None
            for base in tried:
                candidate = entry_at(f, base + offset)
                if candidate and same_name(candidate.get("name", ""), name):
                    entry, chosen = candidate, base
                    break
            if entry is None:
                stats["untagged"] += 1
            records.append(patch_record(entry, name, entry.get("library", library) if entry else library,
                                        path, folder))
    stats["libraries"] += 1
    return records


def index_loose_patch(path, root, stats):
    with open(path, "rb") as f:
        entry = read_entry(f.read(HEAD_BYTES))
    name = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.relpath(os.path.dirname(path), root).split(os.sep)
    folder = [] if folder == ["."] else folder
    if entry is None:
        stats["untagged"] += 1
    return patch_record(entry, name, entry.get("library", "User") if entry else "User", path, folder)


# ---------------------------------------------------------------- finding libraries

def default_roots():
    if sys.platform == "darwin":
        steam = "/Library/Application Support/Spectrasonics/STEAM"
    elif os.name == "nt":
        steam = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Spectrasonics", "STEAM")
    else:
        return []
    return [steam] if os.path.exists(steam) else []


def patch_folders(root):
    """Prefer each product's 'Settings Library/Patches' folder so huge soundsource files are never opened."""
    root = os.path.realpath(root)
    found = []
    for product in ("Omnisphere", "Keyscape", "Trilian"):
        p = os.path.join(root, product, "Settings Library", "Patches")
        if os.path.isdir(p):
            found.append(p)
    return found or [root]


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames.sort()
        for fn in sorted(filenames):
            yield os.path.join(dirpath, fn)


def build_index(roots):
    stats = {"libraries": 0, "untagged": 0}
    patches = []
    for root in roots:
        for folder in patch_folders(root):
            for path in walk(folder):
                ext = path.rsplit(".", 1)[-1].lower()
                if ext == "db":
                    patches += index_library(path, stats)
                elif ext in PATCH_EXTENSIONS:
                    patches.append(index_loose_patch(path, folder, stats))
    return patches, stats


# ---------------------------------------------------------------- searching

def like(pattern, value):
    """Case-insensitive match where % is a wildcard, like SQL LIKE."""
    rx = "^" + ".*".join(re.escape(part) for part in pattern.casefold().split("%")) + "$"
    return re.match(rx, value.casefold(), re.S) is not None


def any_like(pattern, values):
    return any(like(pattern, v) for v in values)


def matches(p, args):
    if args.text:
        hay = " ".join([p["name"], p["description"], p["library"], " ".join(p["keywords"]),
                        " ".join(p["types"]), " ".join(p["folder"])]).casefold()
        if not all(w.casefold() in hay for w in args.text.split()):
            return False
    for g in args.genre or []:
        if not any_like(g, p["genres"]):
            return False
    for m in args.mood or []:
        if not any_like(m, p["moods"]):
            return False
    for t in args.type or []:
        if not any_like(t, p["types"]):
            return False
    for k in args.keyword or []:
        if not any_like(k, p["keywords"]):
            return False
    if args.author and not like(args.author, p["author"]):
        return False
    if args.complexity and not like(args.complexity, p["complexity"]):
        return False
    if args.library and not like(args.library, p["library"]):
        return False
    if args.folder and not any_like(args.folder, p["folder"]):
        return False
    return True


def search(patches, args):
    hits = [p for p in patches if matches(p, args)]
    hits.sort(key=lambda p: (p["name"].casefold(), p["library"].casefold()))
    return hits[: args.limit]


def counts(patches, field):
    out = {}
    for p in patches:
        values = p[field] if isinstance(p[field], list) else [p[field]]
        for v in values:
            if v:
                out[v] = out.get(v, 0) + 1
    return sorted(out.items(), key=lambda kv: kv[0].casefold())


# ---------------------------------------------------------------- output

def flat(row):
    return {k: ("/" if k == "folder" else ", ").join(v) if isinstance(v, list) else v
            for k, v in row.items()}


def print_rows(rows, fmt, columns):
    if fmt == "json":
        json.dump(rows, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return
    rows = [flat(r) for r in rows]
    if fmt == "csv":
        w = csv.DictWriter(sys.stdout, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return
    if not rows:
        print("No matches.")
        return
    widths = {c: min(30, max(len(c), *(len(str(r.get(c, ""))) for r in rows))) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    for r in rows:
        print("  ".join(str(r.get(c, ""))[: widths[c]].ljust(widths[c]) for c in columns))


def load_index(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)["patches"]
    except FileNotFoundError:
        sys.exit(f"No index at {path}. Run `omnitags.py index` first.")


def main(argv=None):
    p = argparse.ArgumentParser(description="Search Omnisphere patches by their browser tags.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("index", help="read your patch libraries and write the tag index")
    i.add_argument("paths", nargs="*", help="STEAM folder, a Patches folder, or single .db files (default: your STEAM folder)")
    i.add_argument("-o", "--output", default=DEFAULT_INDEX)

    s = sub.add_parser("search", help="find patches by text and tags")
    s.add_argument("text", nargs="?", help="words to find in name, description, keywords, type or folder")
    s.add_argument("--genre", action="append")
    s.add_argument("--mood", action="append")
    s.add_argument("--type", action="append")
    s.add_argument("--keyword", action="append")
    s.add_argument("--author")
    s.add_argument("--complexity")
    s.add_argument("--library", help="library name, e.g. 'Omnisphere Library'")
    s.add_argument("--folder", help="browser folder, e.g. 'Synth Pads'")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--format", choices=["table", "json", "csv"], default="table")
    s.add_argument("--index", default=DEFAULT_INDEX)

    l = sub.add_parser("list", help="list every genre, mood, type, author... with counts")
    l.add_argument("what", choices=sorted(LIST_FIELDS))
    l.add_argument("--format", choices=["table", "json", "csv"], default="table")
    l.add_argument("--index", default=DEFAULT_INDEX)

    args = p.parse_args(argv)

    if args.command == "index":
        roots = args.paths or default_roots()
        if not roots:
            sys.exit("Couldn't find your STEAM folder. Pass it (or a Patches folder) as an argument.")
        started = time.time()
        patches, stats = [], {"libraries": 0, "untagged": 0}
        for root in roots:
            if os.path.isfile(root):
                patches += index_library(root, stats)
            else:
                more, st = build_index([root])
                patches += more
                stats = {k: stats[k] + st[k] for k in stats}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "built": time.strftime("%Y-%m-%dT%H:%M:%S"), "patches": patches},
                      f, ensure_ascii=False)
        tagged = len(patches) - stats["untagged"]
        print(f"{len(patches):,} patches from {stats['libraries']} libraries, {tagged:,} with tags, "
              f"in {time.time() - started:.1f}s → {args.output}")
        if stats["untagged"]:
            print(f"{stats['untagged']:,} patches had no readable tags. If that's most of them, "
                  "please open an issue with your Omnisphere version.", file=sys.stderr)
        return

    patches = load_index(args.index)
    if args.command == "search":
        print_rows(search(patches, args), args.format,
                   ["name", "library", "folder", "types", "genres", "moods", "complexity", "author"])
    else:
        rows = [{"value": v, "count": n} for v, n in counts(patches, LIST_FIELDS[args.what])]
        print_rows(rows, args.format, ["value", "count"])


if __name__ == "__main__":
    main()

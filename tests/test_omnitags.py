"""Tests against small, made-up library files shaped like Omnisphere's.

No real patch data is used: every name, tag and description here is invented.
"""

import argparse
import html
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import omnitags  # noqa: E402


def patch_bytes(name, library, attrib):
    return (
        '<AmberPart>\n<SynthMaster vers="3.0.0d">\n'
        f'<ENTRYDESCR name="{html.escape(name)}" library="{library}" ATTRIB_VALUE_DATA="{html.escape(attrib)}">\n'
        '</ENTRYDESCR>\n<ENTRYDESCR name="layer" ATTRIB_VALUE_DATA="size=0;"></ENTRYDESCR>\n'
        '<SynthEngine></SynthEngine>\n</SynthMaster>\n</AmberPart>\n'
    ).encode("utf-8")


def build_db(path, tree, library="Test Library", amber_gap=None):
    """Write a .db: a <FileSystem> index, then patch data.

    tree is [(folder list, name, attrib)]. With amber_gap, some filler bytes sit
    between the index and the first patch and offsets count from that first
    <AmberPart> (Omnisphere 3 style); otherwise they count from just after the index.
    """
    blobs, entries, pos = [], [], 0
    for folder, name, attrib in tree:
        blob = patch_bytes(name, library, attrib)
        entries.append((folder, name, pos, len(blob)))
        blobs.append(blob)
        pos += len(blob)
    lines = ["<FileSystem>"]
    open_dirs = []
    for folder, name, offset, size in entries:
        while open_dirs and open_dirs != folder[: len(open_dirs)]:
            lines.append("</DIR>")
            open_dirs.pop()
        for d in folder[len(open_dirs):]:
            lines.append(f'<DIR name="{html.escape(d)}">')
            open_dirs.append(d)
        lines.append(f'<FILE name="{html.escape(name)}.prt_omn" offset="{offset}" size="{size}"/>')
    lines += ["</DIR>"] * len(open_dirs) + ["</FileSystem>"]
    with open(path, "wb") as f:
        f.write("\n".join(lines).encode("utf-8") + b"\n")
        if amber_gap:
            f.write(b"\x00" * amber_gap)
        f.write(b"".join(blobs))


TREE = [
    (["Synth Pads", "Airy"], "Glass Field", "Author=A. Person;Complexity=Simple;Genre=Film;Genre=Ambient;Mood=Hopeful;Type=Pad Bright;Keywords=glass, shimmer"),
    (["Synth Pads", "Airy"], "Snow & Static", "Genre=Ambient;Mood=Dark;Type=Pad Dark;Description=Cold; distant."),
    (["Bass"], "Rubber Floor", "Author=B. Person;Genre=Electronic;Mood=Aggressive;Type=Bass Synth;Complexity=Medium"),
]


def args(**kw):
    base = dict(text=None, genre=None, mood=None, type=None, keyword=None, author=None,
                complexity=None, library=None, folder=None, limit=50)
    base.update(kw)
    return argparse.Namespace(**base)


class ParseTest(unittest.TestCase):
    def test_attrib_string(self):
        t = omnitags.parse_attrib_value_data(
            "Author=A;Genre=Film;Genre=Pop;Mood=Calm;Type=Keys;Keywords=a, b;Description=One; two&#34;;size=0;")
        self.assertEqual(t["genres"], ["Film", "Pop"])
        self.assertEqual(t["moods"], ["Calm"])
        self.assertEqual(t["keywords"], ["a", "b"])
        self.assertEqual(t["description"], 'One; two"')
        self.assertEqual(t["author"], "A")

    def test_like(self):
        self.assertTrue(omnitags.like("pad%", "Pad Bright"))
        self.assertFalse(omnitags.like("pad", "Pad Bright"))


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def index(self, **kw):
        path = os.path.join(self.tmp.name, "Test Library.db")
        build_db(path, TREE, **kw)
        stats = {"libraries": 0, "untagged": 0}
        return omnitags.index_library(path, stats), stats

    def check(self, patches, stats):
        self.assertEqual(stats["untagged"], 0)
        by_name = {p["name"]: p for p in patches}
        self.assertEqual(sorted(by_name), ["Glass Field", "Rubber Floor", "Snow & Static"])
        self.assertEqual(by_name["Glass Field"]["folder"], ["Synth Pads", "Airy"])
        self.assertEqual(by_name["Rubber Floor"]["folder"], ["Bass"])
        self.assertEqual(by_name["Glass Field"]["genres"], ["Film", "Ambient"])
        self.assertEqual(by_name["Snow & Static"]["description"], "Cold; distant.")
        self.assertEqual(by_name["Glass Field"]["library"], "Test Library")

    def test_offsets_after_index(self):
        self.check(*self.index())

    def test_offsets_from_first_amberpart(self):
        self.check(*self.index(amber_gap=777))

    def test_wrong_offsets_are_counted_not_guessed(self):
        path = os.path.join(self.tmp.name, "Broken.db")
        build_db(path, TREE)
        with open(path, "r+b") as f:
            data = f.read().replace(b'offset="0"', b'offset="9"')
            f.seek(0)
            f.write(data)
        stats = {"libraries": 0, "untagged": 0}
        patches = omnitags.index_library(path, stats)
        self.assertEqual(stats["untagged"], 1)
        self.assertFalse([p for p in patches if p["name"] == "Glass Field"][0]["tagged"])

    def test_steam_folder_and_loose_user_patch(self):
        patches_dir = os.path.join(self.tmp.name, "STEAM", "Omnisphere", "Settings Library", "Patches")
        os.makedirs(os.path.join(patches_dir, "Factory"))
        os.makedirs(os.path.join(patches_dir, "User", "Mine"))
        build_db(os.path.join(patches_dir, "Factory", "Test Library.db"), TREE)
        with open(os.path.join(patches_dir, "User", "Mine", "My Patch.prt_omn"), "wb") as f:
            f.write(patch_bytes("My Patch", "User", "Genre=Film;Mood=Calm"))
        # a non-patch file next to the libraries must be skipped, not crash
        with open(os.path.join(patches_dir, "Factory", "notes.db"), "wb") as f:
            f.write(b"not a library")
        patches, stats = omnitags.build_index([os.path.join(self.tmp.name, "STEAM")])
        self.assertEqual(len(patches), 4)
        mine = [p for p in patches if p["name"] == "My Patch"][0]
        self.assertEqual(mine["folder"], ["User", "Mine"])
        self.assertEqual(mine["moods"], ["Calm"])

        hits = [p["name"] for p in omnitags.search(patches, args(genre=["film"]))]
        self.assertEqual(hits, ["Glass Field", "My Patch"])
        hits = [p["name"] for p in omnitags.search(patches, args(genre=["Ambient"], mood=["dark"]))]
        self.assertEqual(hits, ["Snow & Static"])
        hits = [p["name"] for p in omnitags.search(patches, args(type=["Pad%"]))]
        self.assertEqual(hits, ["Glass Field", "Snow & Static"])
        hits = [p["name"] for p in omnitags.search(patches, args(text="shimmer"))]
        self.assertEqual(hits, ["Glass Field"])
        hits = [p["name"] for p in omnitags.search(patches, args(folder="Bass", complexity="medium"))]
        self.assertEqual(hits, ["Rubber Floor"])
        self.assertIn(("Ambient", 2), omnitags.counts(patches, "genres"))


if __name__ == "__main__":
    unittest.main()

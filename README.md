# omnisphere-patch-tags

Search your Omnisphere patches by genre, mood, type, author, complexity and
keywords from the command line, and export the results as CSV or JSON.

Every Omnisphere patch carries the tags you see in its browser (Genre, Mood,
Type, Complexity, Description…) inside the patch itself. This tool reads the
patch libraries already installed on your machine, read-only, and builds a small
index of just those tags. It doesn't change your libraries, and it doesn't
contain or download any Spectrasonics content.

One Python file, no dependencies beyond Python 3.8+.

```sh
python3 omnitags.py index                          # reads your STEAM folder, writes omnisphere-index.json
python3 omnitags.py search --genre Film --mood Hopeful
python3 omnitags.py search "glass" --type "Pad%" --complexity Simple
python3 omnitags.py search --folder "Synth Pads" --format csv > pads.csv
python3 omnitags.py list moods                     # every mood tag, with counts
```

Example output (made-up patches):

```
name           library       folder           types       genres         moods    complexity
Glass Field    Test Library  Synth Pads/Airy  Pad Bright  Film, Ambient  Hopeful  Simple
Snow & Static  Test Library  Synth Pads/Airy  Pad Dark    Ambient        Dark
```

Tag filters are case-insensitive, `%` is a wildcard, and repeating `--genre`,
`--mood`, `--type` or `--keyword` requires all of them. Free text matches name,
description, keywords, type and browser folder.

`index` finds STEAM at `/Library/Application Support/Spectrasonics/STEAM` on
macOS and `%PROGRAMDATA%\Spectrasonics\STEAM` on Windows. If yours is somewhere
else, pass it: `python3 omnitags.py index "/Volumes/Sounds/STEAM"`. It only
reads each product's `Settings Library/Patches` folder, so the large soundsource
files are never opened. Factory libraries (`.db`) and your own loose patches
(`.prt_omn`) are both indexed. Keyscape (`.prt_key`) and Trilian (`.prt_trl`)
patches are picked up the same way.

The index file is built from your own libraries. Keep it to yourself; it's
ignored by this repo's `.gitignore`.

## Status

The format handling comes from working with real Omnisphere 2 and 3 libraries,
but this rewrite has so far only been tested against synthetic files with the
same structure. `index` reports how many patches it found tags for. If that
number is far below the patch count, please open an issue with your Omnisphere
version.

## How the files are laid out

Worked out by reading the files. Spectrasonics doesn't document them, and an
update can change them.

**Library `.db` files** (`Settings Library/Patches/Factory/*.db`) start with an
XML index of every patch, then the patch data:

```xml
<FileSystem>
<DIR name="Synth Pads">
<FILE name="Glass Field.prt_omn" offset="0" size="365"/>
...
</DIR>
</FileSystem>
<AmberPart>...first patch...</AmberPart>
<AmberPart>...
```

`offset` counts from one of two places, depending on the library: right after
`</FileSystem>`, or from the first `<AmberPart>`. The tool tries both and keeps
the one where the patch it lands on has the expected name. A patch it can't
confirm that way is counted as untagged rather than guessed.

**Each patch** opens with an `ENTRYDESCR` tag holding its browser tags as one
`;`-separated string:

```xml
<SynthMaster vers="3.0.0d">
<ENTRYDESCR name="Glass Field" library="Test Library"
  ATTRIB_VALUE_DATA="Author=A. Person;Complexity=Simple;Genre=Film;Genre=Ambient;Mood=Hopeful;Type=Pad Bright;Keywords=glass, shimmer;Description=…">
```

`Genre`, `Mood` and `Type` can repeat. Values may contain HTML entities such
as `&#34;`. Later `ENTRYDESCR` tags in the same patch describe its layers'
soundsources and are ignored.

## What this doesn't do

It doesn't load patches into Omnisphere, extract or convert patch data, or read
FX racks. It only reads the tags.

## Not affiliated

This is an independent project. It is not made, endorsed or supported by
Spectrasonics. Omnisphere, Keyscape, Trilian and STEAM are their trademarks,
used here only to say what the tool works with. Check your own licence terms
before using it; you use it at your own risk.

## Licence

MIT. See [LICENSE](LICENSE).

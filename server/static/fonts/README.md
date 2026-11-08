Recipe lettering is based on [Minecraft-Font by Idrees Hassan](https://github.com/IdreesInc/Minecraft-Font),
revision `261ac77fbf28796ca09c22eb83ecdfe386c4b838`, licensed under the
[SIL Open Font License 1.1](OFL-Minecraft-Font.txt).

`recipe-count.woff` and `recipe-bold.woff` are ASCII subsets, renamed to
**GCM Recipe**. They preserve the upstream glyph shapes, including descenders.
The em and side padding are aligned to an eight-pixel grid so integer GUI
scales have solid strokes and consistent spacing.

Run `python tools/make-count-font.py` from the repository root with `fonttools`
installed to regenerate them. The tool downloads checksum-verified files at
the pinned revision. The running application serves the prebuilt fonts locally.

# Icon export

Builds a game data bundle (item icons, the icon lookup, the item catalog,
the ore dictionary, item info, supported game recipes and the blocks of its
multiblocks) from a GTNH client pack. CI runs it for every GTNH release and publishes the result (see
[Game data](../../docs/server.md#game-data)), so you only need it for a
pack CI doesn't cover (a nightly, a modified pack), or to build the data
yourself.

`run.sh` starts the GTNH client headlessly in Docker (Xvfb, software
rendering, no Minecraft account), renders every item and fluid, and writes
the same bundle CI publishes. It takes about 20 minutes for 2.9.0 (most of it
capturing its ~9,000 animated icons; `GCM_ANIMATION_TICKS=0` skips that,
for about 3 minutes), plus a one-time
~250 MB download of Minecraft's libraries and assets.

## Building game data

1. Download the **MultiMC/Prism** client zip from
   <https://downloads.gtnewhorizons.com/Multi_mc_downloads/> (not the server
   pack), or use your own pack's MultiMC export.
2. Run:
   ```bash
   tools/icon-export/run.sh ~/Downloads/GT_New_Horizons_2.9.0_Java_17-25.zip --install
   ```
   Output goes to `tools/icon-export/out/`. `--install` also puts it in
   `server/data/gamedata/<version>/` and selects it; add `--data-dir DIR`
   when the server's data directory is elsewhere (the prebuilt image's
   `data/`). The version name comes from the file name; set
   `GCM_VERSION_LABEL` to override it.

   For Faithful icons too, add `--faithful` with a release zip from
   [GTNH-Faithful-Textures](https://github.com/Ethryan/GTNH-Faithful-Textures/releases)
   (`--faithful ~/Downloads/GTNH-Faithful-x32.v2.2.0.zip`). The game is
   launched a second time with the pack on, which doubles the time.
   `--install` selects the default textures; switch on the Game data page.
3. Restart the server, or pick the version on the Game data page.

`out/export-report.json` lists the counts and any items that failed to
render. The exporter retries failed inventory renders with a temporary player
and camera entity, allowing models such as Thaumcraft’s Runic Matrix to render
at the main menu. Some items still fail when they need a world or more player
state (the bow, some Botania and Chisel blocks), since the export never loads a
world. They fall back to `by_label` or show no icon. CI also checks that an
export containing Arcane Infusion includes its Runic Matrix catalyst and image.

Settings: `GCM_MAX_MEMORY` (default `6G`), `GCM_ICON_SIZE` (default 64 px),
`GCM_ANIMATION_TICKS` (default 400, i.e. up to 20 s of animation; 0 for still icons only),
`GCM_TIMEOUT` (seconds, default 3600) and `GCM_CACHE_DIR` (default
`~/.cache/gcm-icon-export`).

## Publishing from a fork

The [`gtnh-data.yml`](../../.github/workflows/gtnh-data.yml) workflow checks
GTNH's releases daily and publishes a bundle for every new stable, beta and
RC release, tagged `gtnh-data-<version>`. A fork that runs it publishes to
the repo named by `DATA_REPO` in the workflow. That needs a fine-grained
token with Contents: read and write on that repo, stored as the
`GAMEDATA_TOKEN` Actions secret. Point your servers at it with
`GAMEDATA_REPO`.

## Bundle format

Icons in `images.zip` are lossless WebP (animated WebP for animated ones),
named after NESQL's scheme but ending `.webp`
(`item/<mod>/<name>~<damage>[~<nbt>].webp`), and `data.json` says
`"image_format": "webp"`. Bundles built before WebP have PNG and APNG
icons named `.png` and no `image_format`; the server reads both.

The icon lookup contains six tables:

- `by_key` — `modid:internalname:damage` → icon, for ordinary items. When
  several stacks share a key (NBT variants of one item), the one without NBT.
- `by_key_label` — `modid:internalname:damage|label` → icon, from NBT stacks
  only: tells those variants apart (each crop's seeds, each bee species).
  Tried first for items the scan reports with NBT.
- `by_key_material` — `modid:internalname:damage|material` → icon, for
  GregTech tools: every material's Huge Turbine shares one id, damage and
  label, and only the tool's primary material in its NBT tells them apart.
- `fluids_by_key` — Forge fluid registry name → icon, for fluid pseudo-items
  (see [Fluid items](../../docs/how-it-works.md#fluid-items)).
- `aspects_by_key` — Thaumcraft aspect tag (`ordo`) → icon, for essentia.
  Essentia never falls back to `by_label`, where an aspect's name finds
  blocks and items named after it. Game data without this table shows a
  round badge with the aspect's initial instead.
- `by_label` — display name → icon. Least reliable fallback, because about 8%
  of labels collide across mods.

Beside the lookup, `ore_dict.json` holds the game's ore dictionary
(`{name: ["modid:internalname:damage", ...]}`, damage `*` for any), for the
crafting plan's ore-dictionary substitutes. Bundles made before it existed
work without it; plans then take no substitutes.

`item_info.json` holds what the page shows about items beyond their name and
icon:

- `tooltips`, `shift_tooltips`, `rarity`, `formulas`: for the same keys as
  `by_key`, `by_key_label` and a GregTech tool's `by_key_material`, the
  item's tooltip below its name (lines joined with newlines, formatting
  codes like `§e` kept), the same with Shift held where that's different,
  its name's rarity colour code where it isn't common, and its chemical
  formula, read the way GregTech's own NEI formula search reads it (its
  material, or its GT++ material). Formulas and tooltips keep GregTech's
  font glyphs (`` for ², ...), which the page maps to Unicode. The
  export holds Shift by patching mods' keyboard checks; Ctrl sections, and
  lines a mod adds only with a player in the world, aren't in it.
- `fluids`: each fluid's temperature in kelvin, whether it's a gas, and its
  formula: that of a container (a cell) filled with it.
- `mods`: each mod's name by its id.

Bundles made before it existed have none of it. Before it, `ore_dict.json`
also left out names only one item has.

`recipes.json.gz` format 4 holds GregTech's recipe maps plus `crafting` and
`furnace` maps. The [pattern check](../../docs/using.md#pattern-check) still
compares only GT maps; the assistant's `find_recipes` also reads the new maps.
Formats 1 (GT only), 2 (GT/crafting/furnace) and 3 (extended displays) remain readable by new servers.

`items` and `fluids` intern keys (`modid:internalname:damage`, damage `*` for
any; fluid registry names), with parallel `item_names` and `fluid_names` lists
(null where unavailable). Each map has `machines` (NEI catalyst objects with
`name` and `key`) and `recipes`. Bodies have an explicit `type`:

- `gt`: `{"type":"gt", "i":[[item,n]], "f":[[fluid,mB]], "o":[[item,n,chance?]],
  "g":[[fluid,mB]], "e":EU/t, "d":ticks, "s":special}`. Empty lists and zero
  special values may be omitted. Indices refer to the intern tables; n=0
  marks a non-consumed input and chance is in 1/10000, only below 100%.
  Fake/disabled recipes are excluded.
- `shaped`: `w`/`h` (1–3), `mirrored`, `slots` (exactly `w*h`, row-major,
  null for holes), and one output in `o`. Each occupied slot is
  `{"alts":[[item,1],...], "od":optional_ore_name}`. **All** alternatives are
  exported, without a presentation cap. Ore names are emitted only when
  retained reliably; alternatives need not be one ore-dictionary entry.
- `shapeless`: occupied `slots` (1–9) and `o`, without dimensions or holes.
- `furnace`: one item stack in `i`, one in `o`, and `d:200` for a standard
  furnace. This describes smelting, without fuel or experience requirements.

Crafting bodies do not have GT voltage/time fields. `approximate` flags such
as `nbt` and `custom_matching` warn about constraints this representation does
not preserve. Item identity remains ID + damage: NBT variants are not distinct.
The exporter supports vanilla/Forge recipe families (using virtual input getters
for DreamCraft universal recipes), AE2 crafting ingredient expansion, and IC2
advanced recipes. Other dynamic classes, null outputs, disabled recipes and
mandatory slots with no alternatives are skipped, with reasons counted rather
than turning an unresolved ingredient into a free slot. Custom NBT-dependent
Shulker conversions remain unsupported; additional handlers use the display
fallback described below.

Format 3 adds two processing display types:

- `assembly_line`: native GT ingredients as ordered `groups` (each has complete
  `alts`), fluids in `f`, output in `o`, production `e`/`d`, and optional `research`.
  Research has an interned `item` stack, `method` (`scanner` or `research_station`),
  `eut`, `ticks`, and where applicable `computation`, `computation_per_second` and
  `amperage`. TecTech research overlays are merged into their matching native GT
  recipes; their research EU/t never replaces production EU/t. A mismatch fails
  export. Group identity includes the original NBT, quantities, order and fluids.
- `nei`: an approximate recipe display, with flat stacks and/or ingredient
  `groups`; a group can have `kind:"fluid"` and fluid-index alternatives. No
  unknown power/time value is invented. The viewer marks these as NEI displays;
  they never participate in processing-pattern checks. GT fake/display maps are
  retained under `nei.gt.*`, rather than silently discarded. Scanner/research and
  duplicate Assembly Line diagrams stay in the source but are excluded from the
  query index: their fake outputs identify research targets, not manufactured items. Native static
  Thaumcraft infusion is a fallback where NEI's dynamic expansion needs a player.

`requirements` retains GT metadata, descriptions, special items, fluid alternatives,
input/fluid chances and supported NEI costs. GT entries without usable outputs
(including many generator fuels), disabled entries and skipped crafting records
are retained in source archives; CI accounts for every registered GT record. Fluid output chances use the third
stack element as item chances do. Raw `snapshot` fields retain NEI ingredient/result/
other stack roles, positions, alternatives, NBT and cached recipe values; map-level
`archive` arrays retain captures that cannot safely become a browsable recipe.
The source bundle retains these even though the SQLite query index omits raw
snapshots/archives, so later readers can improve presentation without rerunning
Minecraft. Snapshots are deliberately bounded (five nested levels, 512 entries
per collection), exclude client/world/registry graphs, and mark unexpanded data.
They are an archive of available display data, not an exact serialization of
arbitrary Java recipe logic.

The coverage audit collects both NEI crafting and usage registries, deduplicating
by class and category. It lists every GT map and NEI handler, including native coverage,
exported displays, archived records, unavailable handlers and categories returning
no recipes. Empty enumeration does not prove that a category has no recipes.
See the [2.9.0 coverage audit](../../docs/recipe-coverage.md) for the complete
measured inventory. Some dynamic crafting, world/player-dependent recipes and non-enumerable handlers
remain unsupported; use the report before deciding a bundle is complete.

`coverage` records handler coverage, identity limitations and registered/exported/
skipped totals, by crafting class as well. It is repeated as `recipeCoverage` in
`export-report.json`; `recipes` still counts GT recipes, while `craftingRecipes`
and `furnaceRecipes` count their respective exported recipes. CI validates types,
grids, references and balanced counts; unexpected adapter errors fail validation.

Format 4 adds presentation without changing recipe-search identity:

- Item tuples may have four elements: `[item,n,chance_or_null,[display_id,...]]`.
  `display_stacks` maps each ID to its concrete key, localized name, optional
  SNBT and exact icon path. Wildcard ingredients retain `*` in the recipe;
  up to 16 concrete display samples are separate from all semantic alternatives.
  Wildcards use the icon exporter's collected NEI overrides/subtypes/variants:
  `ItemList.itemMap` is empty at the menu, and a bare damage-zero fallback loses
  charged creative variants. An explicitly supplied NBT constraint is retained.
  Failed or blank renders have `render_failed:true` and no icon; readers use
  their normal fallback. Reading a display name must not mutate exported NBT.
- A map-level `preview`, or a recipe-level override, describes `family`, `width`,
  `height`, ordered `layers` and positioned `slots`. A slot has a role (`i`, `o`,
  `f`, `g`, `groups`, `aux`, `extras`), index, x/y/w/h and optional frame asset.
  `preview_indices` retains original GT slot positions after null stacks are
  removed. Layer crops are normalized `[left,top,right,bottom]` UV coordinates.
  GT previews also include the translated category `title`, `frontend` class
  and `custom_callbacks` inventory. Assembly-line subclasses use their inherited
  pipe layout. Recipes exceeding declared IO limits retain extra slots using
  the frontend position getters, as NEI's CachedDefaultRecipe does.
- `preview_stacks` optionally overrides `i`, `o`, `f` or `g` for display only,
  using the same interned stack format. These come from NEI's configured stack
  getters: some maps store displayed fluids/items in metadata instead of native
  IO fields. Their `preview_indices` describe the substituted arrays. Production
  IO and pattern checks stay separate. Condensate Assembling also exports the
  localized maximum nanite description rather than printing its whole tier array.
- `ui_assets` maps resource locations to resolved image paths and dimensions.
  Frames, GT backgrounds/pipes/logo and the infusion rune crop are copied through
  Minecraft's resource manager. The `ui_assets` and `by_variant` icon lookup
  tables keep these images through WebP conversion, animation and texture-pack
  fallback. GUI layers are static; custom Java drawing callbacks are not replayed.
- `aux` contains GT special-slot stacks, including the Assembly Line data stick
  with amount zero (`NC`). NEI `extras` retain other positioned stacks and their
  alternatives for display. They do not become consumed ingredients or Uses
  matches merely because NEI draws them.
- `research_info` carries static research names/category icons; `aspects` carries
  aspect names and glyph paths. Player research completion is unknown. Infusion
  ingredients use TC4Tweaks' enhanced ingredient API and the pack's configured
  ore-dictionary rules, preserving alternatives. Static add-NBT operations have
  representative output icons; runic/enchantment and other player-dependent
  expansions remain limited and archived. Parent fields shadowed by a subclass
  are retained under qualified names in raw snapshots.

Assembly Line has its dedicated 16-item/four-fluid layout. Standard GT layouts
come from frontend UI properties; generic NEI displays retain stack positions.
The latter are marked `slots_only`, and GT layouts `standard_widgets`: arbitrary
handler graphics, callbacks and dynamic text are not a promise of pixel-perfect
NEI reproduction.

Bundle validation writes `recipe-preview-audit.json`, separating unplaced stacks,
metadata stacks absent from previews, and custom callbacks needing visual review.
Run it independently with
`python tools/icon-export/ci/audit_previews.py OUT/recipes.json.gz > audit.json`.
The callback list is advisory: arbitrary Java drawing, dynamic text and pack-version
differences cannot be proven correct by structural checks alone. Older exports
without callback information are identified separately.

The server builds SQLite index format 6, accepting source formats 1–4. Every
ingredient alternative is indexed for Uses; concrete queries also match wildcard
damage, deduplicated before counting/paging. Display variants are streamed into
SQLite separately and fetched only for the current page. Deploy compatible
readers before publishing format-4 bundles. Rebuild shared/shipped
`recipes.sqlite` indexes with the new server's `app.py index-gamedata` before
hosted instances need them; an outdated index triggers a local build. The builder
streams two passes, retaining intern tables and one recipe. The earlier format-3
2.9.0 audit measured about 50 MiB peak RSS, 6.4 seconds and 178 MiB on disk;
format 4 also stores display stacks and layouts.
Bundles made before recipe export existed cannot be compared.

`structures.json.gz` holds the blocks each GregTech multiblock is built
from, for [multiblock kits](../../docs/using.md#multiblock-kits): 334
multiblocks in GTNH 2.9, about 110 KB gzipped, built in a few seconds. The
export builds each one in BlockRenderer6343's dummy world (the world NEI's
multiblock preview uses), with StructureLib's creative build, and counts
the blocks by item key. It builds once with no channel set, then again for
each tier of each channel (a coil or glass channel's tiers are the blocks
it takes; a length or height channel's are sizes). `base` is the first
build, and each tier holds what changes from it. Where channels interact
(an assembly line's glass comes with its length), `combos` holds every
combination, built. Hatches and buses aren't built, so the spots that take
either a hatch or a casing count as casings. `Structures.java` describes
the format. Bundles made before it existed have no kits.

`layouts.json.gz` and `layouts.png` are where those blocks go, for the
kit's 3D view: about 330 KB and 150 KB for GTNH 2.9. The same builds note
each block's position and the block as placed (`mod:name:meta`, or `mte:`
and the item key for a GregTech machine), and each kind of block's geometry
as the game draws it (`BlockMeshes.java`: the block is drawn with the
world's own block renderer while GTNHLib captures the quads instead of
drawing them), with the textures it uses copied from the game's block atlas
into a small atlas of its own (`layouts.png`, 16 px a tile). 938 of the 941
kinds of block in 2.9 are captured; the rest fall back to cubes with their
face textures. A tier is stored as what changes from the base build: most
are a swap of one block for another (a coil tier), the rest blocks added
and removed. It also holds the pack's connected textures for those blocks
(MCPatcher's compact definitions, read from the mod jars by
`CtmDefinitions.java`, their tiles in `layouts.png`), which the page uses to
join glass, coils and casings up. `Layouts.java` describes the format.

## Credits

The headless client launch follows
[gtnh-factory-flow](https://github.com/jackwrichards/gtnh-factory-flow) (MIT).

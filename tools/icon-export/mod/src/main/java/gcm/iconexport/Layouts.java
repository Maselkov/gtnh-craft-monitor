package gcm.iconexport;

import java.awt.image.BufferedImage;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import net.minecraft.util.IIcon;

/**
 * layouts.json.gz and layouts.png: where each block of each GregTech multiblock goes, for the page's
 * 3D view of a multiblock kit, and the textures to draw them with. StructureBuilder notes the
 * positions as it builds each multiblock for structures.json.gz; this encodes them and draws the
 * blocks' face textures into one atlas.
 *
 * <p>{@code {"format": 1, "tile": 16, "columns": n, "blocks": {block key: {"item": item key, "mesh":
 * {...}, "faces": [6 atlas indices], "tint": rgb, "translucent": true}}, "ctm": [...],
 * "multiblocks": {controller key: {"base": positions, "tiers": {channel: {tier: change}}, "combos":
 * {"a=1,b=2": change}}}}}. "ctm" are the connected textures for those blocks; see
 * connectedTextures().
 *
 * <p>A block key is {@code mod:name:meta}, the block as placed (a block's item key is its drop, which
 * several blocks can share), or {@code mte:} and the item key for a GregTech machine (the
 * controller, a super chest), which are all one block; "item" is the drop, ExportJob's key.
 * "mesh" is the geometry the game draws for it (BlockMeshes), its sprites as atlas tiles: {@code
 * {"t": [tile per quad], "p": [...], "uv": [...], "c": [...]}}, the game's colours (face shading,
 * tint) baked in. Only a block it couldn't be captured for (a handful) has "faces" instead, the
 * atlas tiles of its down, up, north, south, west and east faces (-1 for one that didn't draw), and
 * "tint", a colour they're multiplied by; with neither, the page draws its item icon. "translucent"
 * is a block drawn in the world's second pass (glass).
 *
 * <p>Positions are relative to the controller, packed as {@link #pack} does into one number, and
 * listed sorted and delta-coded (each number is the difference from the one before) per block key:
 * {@code {block key: [first, +d, +d...]}}. A tier's change is what's different from base when only
 * that channel is set to it: {@code {"swap": {from key: to key}}} where every block of one kind
 * becomes another (a coil tier), else {@code {"set": positions, "clear": [delta-coded positions]}}.
 * The page applies each channel's set and clear, then its swaps. {@code combos}, as in
 * structures.json.gz, holds every combination of a multiblock whose channels interact, each a
 * change from base.
 */
final class Layouts {

    /** Each face texture's size in the atlas. */
    static final int TILE = 16;
    private static final int OFFSET = 512;

    private Layouts() {}

    /** One number for a position relative to the controller, each axis within ±511. */
    static int pack(int dx, int dy, int dz) {
        if (Math.abs(dx) >= OFFSET || Math.abs(dy) >= OFFSET || Math.abs(dz) >= OFFSET) {
            throw new IllegalArgumentException("structure too large: " + dx + "," + dy + "," + dz);
        }
        return (dx + OFFSET) << 20 | (dy + OFFSET) << 10 | (dz + OFFSET);
    }

    /** {block key: delta-coded positions} of a layout. */
    static Map<String, Object> encode(Map<Integer, String> layout) {
        Map<String, List<Integer>> byKey = new TreeMap<>();
        for (Map.Entry<Integer, String> e : layout.entrySet()) {
            byKey.computeIfAbsent(e.getValue(), k -> new ArrayList<>()).add(e.getKey());
        }
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<String, List<Integer>> e : byKey.entrySet()) {
            out.put(e.getKey(), deltas(e.getValue()));
        }
        return out;
    }

    private static List<Integer> deltas(List<Integer> positions) {
        List<Integer> sorted = new ArrayList<>(positions);
        sorted.sort(null);
        List<Integer> out = new ArrayList<>(sorted.size());
        int last = 0;
        for (int p : sorted) {
            out.add(p - last);
            last = p;
        }
        return out;
    }

    /** What's different in {@code built} from {@code base} (see the class comment); empty if nothing. */
    static Map<String, Object> change(Map<Integer, String> base, Map<Integer, String> built) {
        Map<Integer, String> set = new TreeMap<>();
        List<Integer> clear = new ArrayList<>();
        for (Map.Entry<Integer, String> e : built.entrySet()) {
            if (!e.getValue().equals(base.get(e.getKey()))) {
                set.put(e.getKey(), e.getValue());
            }
        }
        for (Integer pos : base.keySet()) {
            if (!built.containsKey(pos)) {
                clear.add(pos);
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        if (set.isEmpty() && clear.isEmpty()) {
            return out;
        }
        Map<String, String> swap = clear.isEmpty() ? swap(base, set) : null;
        if (swap != null) {
            out.put("swap", swap);
            return out;
        }
        if (!set.isEmpty()) {
            out.put("set", encode(set));
        }
        if (!clear.isEmpty()) {
            out.put("clear", deltas(clear));
        }
        return out;
    }

    /**
     * {from: to} if the changes turn every block of some kinds into another kind each and touch no
     * empty position; else null.
     */
    private static Map<String, String> swap(Map<Integer, String> base, Map<Integer, String> set) {
        Map<String, String> swap = new TreeMap<>();
        Map<String, Integer> changed = new HashMap<>();
        for (Map.Entry<Integer, String> e : set.entrySet()) {
            String from = base.get(e.getKey());
            if (from == null) {
                return null;
            }
            String to = swap.putIfAbsent(from, e.getValue());
            if (to != null && !to.equals(e.getValue())) {
                return null;
            }
            changed.merge(from, 1, Integer::sum);
        }
        Map<String, Integer> all = new HashMap<>();
        for (String key : base.values()) {
            if (swap.containsKey(key)) {
                all.merge(key, 1, Integer::sum);
            }
        }
        return all.equals(changed) ? swap : null;
    }

    /** The finished file and its atlas, from what the builder collected. */
    static final class Result {

        final Map<String, Object> file;
        final BufferedImage atlas;

        Result(Map<String, Object> file, BufferedImage atlas) {
            this.file = file;
            this.atlas = atlas;
        }
    }

    /**
     * Draws every face texture the blocks use into the atlas and puts the atlas indices in the
     * blocks' faces. Needs the renderer's framebuffer (between its begin() and end()).
     */
    static Result finish(StructureBuilder builder, IconRenderer renderer, Map<String, Object> report) {
        Map<String, Integer> index = new HashMap<>();
        List<BufferedImage> tiles = new ArrayList<>();
        int blank = 0;
        for (Map.Entry<String, IIcon> e : builder.icons.entrySet()) {
            BufferedImage image = null;
            try {
                image = renderer.renderSprite(e.getValue());
            } catch (Throwable t) {
                IconExportMod.LOG.warn("Couldn't draw block texture {}: {}", e.getKey(), t.toString());
            }
            if (image == null) {
                blank++;
                continue;
            }
            index.put(e.getKey(), tiles.size());
            tiles.add(shrink(image));
        }
        Map<String, Object> blocks = new TreeMap<>();
        int meshes = 0;
        for (Map.Entry<String, Map<String, Object>> e : builder.blocks().entrySet()) {
            Map<String, Object> info = new LinkedHashMap<>(e.getValue());
            @SuppressWarnings("unchecked")
            List<String> names = (List<String>) info.remove("faces");
            @SuppressWarnings("unchecked")
            Map<String, Object> mesh = (Map<String, Object>) info.remove("mesh");
            Map<String, Object> tiled = mesh == null ? null : tiles(mesh, index);
            if (tiled != null) {
                // The game's own geometry: the faces are only for a block it couldn't be had for.
                info.put("mesh", tiled);
                info.remove("tint");
                meshes++;
            } else if (names != null) {
                List<Integer> faces = new ArrayList<>();
                boolean any = false;
                for (String name : names) {
                    Integer i = name == null ? null : index.get(name);
                    faces.add(i == null ? -1 : i);
                    any |= i != null;
                }
                if (any) {
                    info.put("faces", faces);
                }
            }
            blocks.put(e.getKey(), info);
        }
        List<Object> ctm = connectedTextures(blocks, index, tiles, report);
        int columns = Math.max(1, (int) Math.ceil(Math.sqrt(tiles.size())));
        int rows = Math.max(1, (tiles.size() + columns - 1) / columns);
        BufferedImage atlas = new BufferedImage(columns * TILE, rows * TILE, BufferedImage.TYPE_INT_ARGB);
        for (int i = 0; i < tiles.size(); i++) {
            atlas.getGraphics().drawImage(tiles.get(i), (i % columns) * TILE, (i / columns) * TILE, null);
        }
        report.put("layoutBlocks", blocks.size());
        report.put("layoutMeshes", meshes);
        report.put("layoutTextures", tiles.size());
        report.put("layoutTexturesBlank", blank);

        Map<String, Object> file = new LinkedHashMap<>();
        file.put("format", 1);
        file.put("tile", TILE);
        file.put("columns", columns);
        file.put("blocks", blocks);
        if (!ctm.isEmpty()) {
            file.put("ctm", ctm);
        }
        file.put("multiblocks", builder.layouts);
        return new Result(file, atlas);
    }

    /**
     * The connected textures (CtmDefinitions) for the blocks and textures the multiblocks use, their
     * tiles added to the atlas: [{"tiles": [5 atlas tiles], "sprites": [atlas tiles it applies to],
     * "blocks": [block keys it applies to], "connect": "block"|"tile", "faces": [0-5]}], faces left
     * out for all six.
     */
    private static List<Object> connectedTextures(Map<String, Object> blocks, Map<String, Integer> index,
            List<BufferedImage> tiles, Map<String, Object> report) {
        List<Object> out = new ArrayList<>();
        List<CtmDefinitions.Definition> definitions;
        try {
            definitions = CtmDefinitions.collect(report);
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Couldn't read connected textures: {}", t.toString());
            return out;
        }
        for (CtmDefinitions.Definition d : definitions) {
            List<Integer> sprites = new ArrayList<>();
            for (String name : d.tiles) {
                Integer i = index.get(name);
                if (i != null) {
                    sprites.add(i);
                }
            }
            List<String> keys = new ArrayList<>();
            for (String key : blocks.keySet()) {
                int colon = key.lastIndexOf(':');
                if (key.startsWith("mte:") || colon < 0) {
                    continue;
                }
                String block = key.substring(0, colon);
                if (!d.blocks.containsKey(block)) {
                    continue;
                }
                java.util.Set<Integer> metas = d.blocks.get(block);
                if (metas == null || metas.contains(Integer.parseInt(key.substring(colon + 1)))) {
                    keys.add(key);
                }
            }
            if (sprites.isEmpty() && keys.isEmpty()) {
                continue;
            }
            List<Integer> own = new ArrayList<>();
            for (BufferedImage image : d.images) {
                own.add(tiles.size());
                tiles.add(shrink(image));
            }
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("tiles", own);
            if (!sprites.isEmpty()) {
                entry.put("sprites", sprites);
            }
            if (!keys.isEmpty()) {
                entry.put("blocks", keys);
            }
            entry.put("connect", d.connect);
            if (d.faces.size() < 6) {
                entry.put("faces", new ArrayList<>(d.faces));
            }
            out.add(entry);
        }
        report.put("ctmUsed", out.size());
        return out;
    }

    /**
     * A BlockMeshes mesh with its sprites as atlas tiles ({@code "t"} in place of {@code "s"}), the
     * quads whose sprite didn't draw left out; null if none is left.
     */
    @SuppressWarnings("unchecked")
    private static Map<String, Object> tiles(Map<String, Object> mesh, Map<String, Integer> index) {
        List<String> sprites = (List<String>) mesh.get("s");
        List<Integer> p = (List<Integer>) mesh.get("p"), uv = (List<Integer>) mesh.get("uv"),
                c = (List<Integer>) mesh.get("c");
        List<Integer> tiles = new ArrayList<>(), p2 = new ArrayList<>(), uv2 = new ArrayList<>(), c2 = new ArrayList<>();
        for (int q = 0; q < sprites.size(); q++) {
            Integer tile = index.get(sprites.get(q));
            if (tile == null) {
                continue;
            }
            tiles.add(tile);
            p2.addAll(p.subList(q * 12, q * 12 + 12));
            uv2.addAll(uv.subList(q * 8, q * 8 + 8));
            c2.addAll(c.subList(q * 4, q * 4 + 4));
        }
        if (tiles.isEmpty()) {
            return null;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("t", tiles);
        out.put("p", p2);
        out.put("uv", uv2);
        out.put("c", c2);
        return out;
    }

    /** A rendered texture at TILE pixels, by nearest pixel: block textures are pixel art. */
    private static BufferedImage shrink(BufferedImage image) {
        if (image.getWidth() == TILE && image.getHeight() == TILE) {
            return image;
        }
        BufferedImage out = new BufferedImage(TILE, TILE, BufferedImage.TYPE_INT_ARGB);
        for (int y = 0; y < TILE; y++) {
            for (int x = 0; x < TILE; x++) {
                int sx = (x * image.getWidth() + image.getWidth() / 2) / TILE;
                int sy = (y * image.getHeight() + image.getHeight() / 2) / TILE;
                out.setRGB(x, y, image.getRGB(Math.min(sx, image.getWidth() - 1), Math.min(sy, image.getHeight() - 1)));
            }
        }
        return out;
    }
}

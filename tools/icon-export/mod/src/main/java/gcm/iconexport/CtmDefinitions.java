package gcm.iconexport;

import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

import javax.imageio.ImageIO;

import cpw.mods.fml.common.Loader;
import cpw.mods.fml.common.ModContainer;

/**
 * The pack's connected textures, for the 3D view to join blocks up the way the game does. They're
 * MCPatcher definitions ({@code assets/<domain>/mcpatcher/ctm/**.properties}) that Angelica applies
 * as it draws the world; the export runs without Angelica, so BlockMeshes never sees them, and they
 * are read here from the mods' jars instead, with their tile images.
 *
 * <p>Only the "compact" methods are read (compact and compact_expanded, which looks the same: 137 of
 * GTNH 2.9's 138): each face of a matching block is split into quarters, and each quarter takes one
 * of five tiles from whether its neighbours on the two sides of that corner, and on the diagonal,
 * connect. The page does that, from the layout (multiblock-view.js); this only says which blocks and
 * textures each definition is for, how they connect, and the five tiles.
 */
final class CtmDefinitions {

    /** One definition: what it matches, how it connects and its five tiles (0 none ... 4 corner). */
    static final class Definition {

        final String name;
        /** Texture names it applies to (matchTiles), e.g. gregtech:iconsets/MACHINE_COIL_..._BACKGROUND. */
        final Set<String> tiles = new LinkedHashSet<>();
        /** Blocks it applies to (matchBlocks): registry name -> metadata, null for any. */
        final Map<String, Set<Integer>> blocks = new LinkedHashMap<>();
        /** "block" or "tile". */
        String connect;
        /** Faces it applies to, 0-5 as ForgeDirection. */
        final Set<Integer> faces;
        final BufferedImage[] images;

        Definition(String name, String connect, Set<Integer> faces, BufferedImage[] images) {
            this.name = name;
            this.connect = connect;
            this.faces = faces;
            this.images = images;
        }
    }

    int read;
    int skipped;

    private CtmDefinitions() {}

    /** Every compact definition in the loaded mods' jars; never throws. */
    static List<Definition> collect(Map<String, Object> report) {
        CtmDefinitions reader = new CtmDefinitions();
        List<Definition> out = new ArrayList<>();
        Set<File> seen = new HashSet<>();
        for (ModContainer mod : Loader.instance().getActiveModList()) {
            File source = mod.getSource();
            if (source == null || !source.isFile() || !seen.add(source)) {
                continue;
            }
            try (ZipFile zip = new ZipFile(source)) {
                reader.readJar(zip, out);
            } catch (IOException | RuntimeException e) {
                IconExportMod.LOG.debug("Couldn't read connected textures from {}: {}", source, e.toString());
            }
        }
        report.put("ctmDefinitions", reader.read);
        report.put("ctmDefinitionsSkipped", reader.skipped);
        return out;
    }

    private void readJar(ZipFile zip, List<Definition> out) throws IOException {
        Enumeration<? extends ZipEntry> entries = zip.entries();
        while (entries.hasMoreElements()) {
            ZipEntry entry = entries.nextElement();
            String name = entry.getName();
            if (!name.startsWith("assets/") || !name.contains("/mcpatcher/ctm/") || !name.endsWith(".properties")) {
                continue;
            }
            Definition definition = null;
            try {
                definition = definition(zip, name);
            } catch (IOException | RuntimeException e) {
                IconExportMod.LOG.debug("Couldn't read {}: {}", name, e.toString());
            }
            if (definition == null) {
                skipped++;
            } else {
                read++;
                out.add(definition);
            }
        }
    }

    /** The definition in the properties file at path, or null if it isn't a compact one we can use. */
    private static Definition definition(ZipFile zip, String path) throws IOException {
        Properties p = new Properties();
        try (InputStream in = zip.getInputStream(zip.getEntry(path))) {
            p.load(new InputStreamReader(in, StandardCharsets.UTF_8));
        }
        String method = p.getProperty("method", "").trim().toLowerCase();
        if (!method.equals("compact") && !method.equals("compact_expanded")) {
            return null;
        }
        String dir = path.substring(0, path.lastIndexOf('/') + 1);
        List<String> tileNames = list(p.getProperty("tiles", ""));
        if (tileNames.size() != 5) {
            return null;
        }
        BufferedImage[] images = new BufferedImage[5];
        for (int i = 0; i < 5; i++) {
            images[i] = image(zip, dir + tileNames.get(i) + ".png");
            if (images[i] == null) {
                return null;
            }
        }
        Set<Integer> faces = faces(p.getProperty("faces", "all"));
        Set<Integer> metadata = numbers(p.getProperty("metadata", ""));
        Definition d = new Definition(path, p.getProperty("connect", "").trim().toLowerCase(), faces, images);
        for (String tile : p.getProperty("matchTiles", "").trim().split("\\s+")) {
            if (!tile.isEmpty()) {
                d.tiles.add(tile.contains(":") ? tile : "minecraft:" + tile);
            }
        }
        for (String block : p.getProperty("matchBlocks", "").trim().split("\\s+")) {
            if (block.isEmpty()) {
                continue;
            }
            // "mod:name", or "mod:name:metas" (a list after a third colon).
            String[] parts = block.split(":");
            if (parts.length < 2) {
                continue;
            }
            Set<Integer> metas = parts.length > 2 ? numbers(parts[2]) : metadata;
            d.blocks.put(parts[0] + ":" + parts[1], metas.isEmpty() ? null : metas);
        }
        if (d.tiles.isEmpty() && d.blocks.isEmpty()) {
            return null;
        }
        if (d.connect.isEmpty()) {
            // MCPatcher's default: by block, unless it matches textures.
            d.connect = d.tiles.isEmpty() ? "block" : "tile";
        }
        // By material or a list of blocks: none of GTNH's compact ones.
        return d.connect.equals("block") || d.connect.equals("tile") ? d : null;
    }

    /** The first frame of the tile image (animated tiles are a column of frames), or null. */
    private static BufferedImage image(ZipFile zip, String path) throws IOException {
        ZipEntry entry = zip.getEntry(path);
        if (entry == null) {
            return null;
        }
        BufferedImage image;
        try (InputStream in = zip.getInputStream(entry)) {
            image = ImageIO.read(in);
        }
        if (image == null) {
            return null;
        }
        int side = image.getWidth();
        return image.getHeight() > side ? image.getSubimage(0, 0, side, side) : image;
    }

    /** "0-4", "0 1 1 0 1", "a,b": the entries in order, ranges expanded. */
    private static List<String> list(String value) {
        List<String> out = new ArrayList<>();
        for (String token : value.trim().split("[\\s,]+")) {
            if (token.isEmpty()) {
                continue;
            }
            int dash = token.indexOf('-');
            if (dash > 0 && token.substring(0, dash).matches("\\d+") && token.substring(dash + 1).matches("\\d+")) {
                for (int i = Integer.parseInt(token.substring(0, dash)); i <= Integer.parseInt(token.substring(dash + 1)); i++) {
                    out.add(String.valueOf(i));
                }
            } else {
                out.add(token);
            }
        }
        return out;
    }

    private static Set<Integer> numbers(String value) {
        Set<Integer> out = new LinkedHashSet<>();
        for (String token : list(value)) {
            if (token.matches("\\d+")) {
                out.add(Integer.parseInt(token));
            }
        }
        return out;
    }

    private static Set<Integer> faces(String value) {
        Set<Integer> out = new LinkedHashSet<>();
        for (String token : value.trim().toLowerCase().split("[\\s,]+")) {
            switch (token) {
                case "bottom" -> out.add(0);
                case "top" -> out.add(1);
                case "north" -> out.add(2);
                case "south" -> out.add(3);
                case "west" -> out.add(4);
                case "east" -> out.add(5);
                case "sides" -> {
                    out.add(2);
                    out.add(3);
                    out.add(4);
                    out.add(5);
                }
                default -> {
                    for (int i = 0; i < 6; i++) {
                        out.add(i);
                    }
                }
            }
        }
        return out;
    }
}

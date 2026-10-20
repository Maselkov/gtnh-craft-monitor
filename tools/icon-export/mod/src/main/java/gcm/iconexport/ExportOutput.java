package gcm.iconexport;

import java.awt.image.BufferedImage;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.function.Function;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import javax.imageio.ImageIO;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

import cpw.mods.fml.common.Loader;
import cpw.mods.fml.common.ModContainer;

/**
 * Writes the files the project consumes, each to a temp name first and renamed into place
 * only once complete, so a crashed run never leaves a half-written file under the real name:
 * <ul>
 * <li>{@code images.zip} - the icons (server/data/images.zip)</li>
 * <li>{@code icons_lookup.json} - key -> path tables</li>
 * <li>{@code item_catalog.txt} - {@code mod:internal} IDs for the OC scanner</li>
 * <li>{@code ore_dict.json} - the ore dictionary, for the crafting plan's substitutions</li>
 * <li>{@code item_info.json} - items' tooltips and rarity, fluids' temperature and state, and mod
 * names, for the page's descriptions and search</li>
 * </ul>
 * plus {@code export-report.json} with counts and failures, and for animated icons
 * {@code animations.zip} (each icon's distinct frames) with {@code animations.json} (which frame
 * shows for how many ticks), which export.py turns into animated PNGs in images.zip.
 */
final class ExportOutput {

    private final File outDir;
    private final File zipTmp;
    private final ZipOutputStream zip;
    private final ByteArrayOutputStream pngBuffer = new ByteArrayOutputStream(8192);
    private ZipOutputStream frames;
    private File framesTmp;

    ExportOutput(File outDir) throws IOException {
        this.outDir = outDir;
        if (!outDir.isDirectory() && !outDir.mkdirs()) {
            throw new IOException("Could not create output directory " + outDir);
        }
        this.zipTmp = new File(outDir, "images.zip.tmp");
        this.zip = new ZipOutputStream(new BufferedOutputStream(new FileOutputStream(zipTmp), 1 << 20));
    }

    void writeImage(String path, BufferedImage image) throws IOException {
        writePng(zip, path, image);
    }

    /** One frame of an animated icon, as {@code <path>/<frame>.png} in animations.zip. */
    void writeFrame(String path, String frame, BufferedImage image) throws IOException {
        if (frames == null) {
            framesTmp = new File(outDir, "animations.zip.tmp");
            frames = new ZipOutputStream(new BufferedOutputStream(new FileOutputStream(framesTmp), 1 << 20));
        }
        writePng(frames, path + "/" + frame + ".png", image);
    }

    private void writePng(ZipOutputStream out, String name, BufferedImage image) throws IOException {
        pngBuffer.reset();
        ImageIO.write(image, "png", pngBuffer);
        out.putNextEntry(new ZipEntry(name));
        pngBuffer.writeTo(out);
        out.closeEntry();
    }

    /**
     * animations.json: for each animated icon, its frames in order as [frame, ticks] pairs. The
     * sequence covers the whole capture; export.py finds where it starts repeating.
     */
    void finishAnimations(Map<String, List<Object[]>> sequences, int tickMillis) throws IOException {
        new File(outDir, "animations.zip").delete();
        new File(outDir, "animations.json").delete();
        if (frames == null) {
            return;
        }
        frames.close();
        Files.move(framesTmp.toPath(), new File(outDir, "animations.zip").toPath(), StandardCopyOption.REPLACE_EXISTING);
        Map<String, Object> json = new LinkedHashMap<>();
        json.put("tickMillis", tickMillis);
        json.put("icons", new TreeMap<>(sequences));
        writeJson("animations.json", json, false);
    }

    void finish(List<ExportJob> jobs, Map<String, Object> report) throws IOException {
        zip.close();
        Files.move(zipTmp.toPath(), new File(outDir, "images.zip").toPath(), StandardCopyOption.REPLACE_EXISTING);

        Map<String, Object> lookup = buildLookup(jobs);
        writeJson("icons_lookup.json", lookup, false);

        TreeSet<String> catalog = new TreeSet<>();
        for (ExportJob job : jobs) {
            if (job.catalogId != null) {
                catalog.add(job.catalogId);
            }
        }
        StringBuilder text = new StringBuilder();
        for (String id : catalog) {
            text.append(id).append('\n');
        }
        writeText("item_catalog.txt", text.toString());

        Map<String, List<String>> oreDict = OreDict.collect();
        writeJson("ore_dict.json", oreDict, false);
        report.put("oreNames", oreDict.size());

        Map<String, Object> info = buildItemInfo(jobs);
        writeJson("item_info.json", info, false);
        report.put("tooltipsByKey", tableSize(info, "tooltips"));
        report.put("shiftTooltipsByKey", tableSize(info, "shift_tooltips"));
        report.put("rarityByKey", tableSize(info, "rarity"));
        report.put("formulasByKey", tableSize(info, "formulas"));
        int fluidFormulaCount = 0;
        for (Object fluid : ((Map<?, ?>) info.get("fluids")).values()) {
            if (((Map<?, ?>) fluid).containsKey("formula")) {
                fluidFormulaCount++;
            }
        }
        report.put("fluidFormulas", fluidFormulaCount);
        report.put("fluidInfo", ((Map<?, ?>) info.get("fluids")).size());
        report.put("modNames", ((Map<?, ?>) info.get("mods")).size());

        report.put("catalogIds", catalog.size());
        for (Map.Entry<String, Object> table : lookup.entrySet()) {
            report.put(table.getKey() + "Entries", ((Map<?, ?>) table.getValue()).size());
        }
        writeJson("export-report.json", report, true);
    }

    /**
     * Same tables and tie-breaking as the old NESQL-based generate_icons_lookup.py: when several
     * stacks share a key (they differ only in NBT), the one without NBT wins; otherwise the last one
     * listed wins. Only stacks that actually rendered are included. by_key_label tells those NBT
     * variants apart by label (each crop's seeds, each bee species), keyed "key|label".
     * by_key_material does the same for GregTech tools, whose labels don't name their material:
     * "key|PrimaryMaterial", one per material GregTech's own recipes make that tool from.
     */
    static Map<String, Object> buildLookup(List<ExportJob> jobs) {
        Map<String, ExportJob> byKey = new HashMap<>();
        Map<String, ExportJob> byKeyLabel = new HashMap<>();
        Map<String, ExportJob> byKeyMaterial = new HashMap<>();
        Map<String, ExportJob> fluidsByKey = new HashMap<>();
        Map<String, ExportJob> aspectsByKey = new HashMap<>();
        Map<String, ExportJob> byLabel = new HashMap<>();
        // Items before fluids, as the old script did, so a fluid wins a label tie with an item.
        for (ExportJob.Kind kind : ExportJob.Kind.values()) {
            for (ExportJob job : jobs) {
                if (job.kind != kind || !job.rendered) {
                    continue;
                }
                if (kind == ExportJob.Kind.ASPECT) {
                    // Never by label: an aspect's name is also the name of blocks and items made
                    // of it, and essentia is only ever looked up by its tag.
                    aspectsByKey.put(job.lookupKey, job);
                    continue;
                }
                putPreferringNoNbt(kind == ExportJob.Kind.ITEM ? byKey : fluidsByKey, job.lookupKey, job);
                if (kind == ExportJob.Kind.ITEM && job.hasNbt && job.label != null) {
                    byKeyLabel.put(job.lookupKey + "|" + job.label, job);
                }
                if (job.gtMaterial != null) {
                    byKeyMaterial.put(job.lookupKey + "|" + job.gtMaterial, job);
                }
                if (job.label != null) {
                    putPreferringNoNbt(byLabel, job.label, job);
                }
            }
        }
        Map<String, Object> lookup = new LinkedHashMap<>();
        lookup.put("by_key", paths(byKey));
        lookup.put("by_key_label", paths(byKeyLabel));
        lookup.put("by_key_material", paths(byKeyMaterial));
        lookup.put("fluids_by_key", paths(fluidsByKey));
        lookup.put("aspects_by_key", paths(aspectsByKey));
        lookup.put("by_label", paths(byLabel));
        // Icons drawn past the item box, and by how much on each side, in 1/16ths of the box:
        // they're that much bigger than the rest, the box in their middle.
        // Only the icons the tables point at: export.py drops the rest from images.zip.
        Map<String, Integer> bleed = new TreeMap<>();
        for (Map<String, ExportJob> table : Arrays.asList(byKey, byKeyLabel, byKeyMaterial, fluidsByKey, byLabel)) {
            for (ExportJob job : table.values()) {
                if (job.bleeds) {
                    bleed.put(job.imagePath, IconRenderer.BLEED);
                }
            }
        }
        lookup.put("bleed", bleed);
        return lookup;
    }

    /**
     * item_info.json. "tooltips", "shift_tooltips" and "rarity" have the lookup's item tables
     * (by_key, by_key_label, by_key_material) with each item's tooltip text (see Tooltips), its
     * tooltip with Shift held where that differs, and its rarity colour code where it isn't
     * common, and "formulas" its chemical formula (see Formulas). Rendered or not: an item without an icon still has a description. by_key takes the
     * stack without NBT, like the lookup, even where only an NBT variant has a tooltip: that's
     * another item's description (a filled cell's, say). "fluids" is each fluid's temperature
     * (kelvin), whether it's a gas, and its formula if it has one, by its lookup key; "mods" each
     * mod's name by its id.
     */
    static Map<String, Object> buildItemInfo(List<ExportJob> jobs) {
        Map<String, ExportJob> byKey = new HashMap<>();
        Map<String, ExportJob> byKeyLabel = new HashMap<>();
        Map<String, ExportJob> byKeyMaterial = new HashMap<>();
        Map<String, Object> fluids = new TreeMap<>();
        Map<String, String> fluidFormulas = Formulas.ofFluids();
        for (ExportJob job : jobs) {
            if (job.kind == ExportJob.Kind.FLUID) {
                try {
                    Map<String, Object> fluid = new LinkedHashMap<>();
                    fluid.put("temperature", job.fluid.getFluid().getTemperature(job.fluid));
                    fluid.put("gaseous", job.fluid.getFluid().isGaseous(job.fluid));
                    String formula = fluidFormulas.get(job.fluid.getFluid().getName());
                    if (formula != null) {
                        fluid.put("formula", formula);
                    }
                    fluids.put(job.lookupKey, fluid);
                } catch (Throwable ignored) {}
                continue;
            }
            if (job.kind != ExportJob.Kind.ITEM) {
                continue;
            }
            putPreferringNoNbt(byKey, job.lookupKey, job);
            if (job.hasNbt && job.label != null) {
                byKeyLabel.put(job.lookupKey + "|" + job.label, job);
            }
            if (job.gtMaterial != null) {
                byKeyMaterial.put(job.lookupKey + "|" + job.gtMaterial, job);
            }
        }
        List<Map<String, ExportJob>> tables = Arrays.asList(byKey, byKeyLabel, byKeyMaterial);
        Map<String, Object> info = new LinkedHashMap<>();
        info.put("tooltips", tables(tables, job -> job.tooltip));
        info.put("shift_tooltips", tables(tables, job -> job.shiftTooltip));
        info.put("rarity", tables(tables, job -> job.rarity == 0 ? null : String.valueOf(job.rarity)));
        info.put("formulas", tables(tables, job -> job.formula));
        info.put("fluids", fluids);
        Map<String, String> mods = new TreeMap<>();
        mods.put("minecraft", "Minecraft");
        for (Map.Entry<String, ModContainer> mod : Loader.instance().getIndexedModList().entrySet()) {
            mods.put(mod.getKey(), mod.getValue().getName());
        }
        info.put("mods", mods);
        return info;
    }

    private static Map<String, Object> tables(List<Map<String, ExportJob>> tables, Function<ExportJob, String> value) {
        String[] names = { "by_key", "by_key_label", "by_key_material" };
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 0; i < names.length; i++) {
            Map<String, String> sorted = new TreeMap<>();
            for (Map.Entry<String, ExportJob> e : tables.get(i).entrySet()) {
                String v = value.apply(e.getValue());
                if (v != null) {
                    sorted.put(e.getKey(), v);
                }
            }
            out.put(names[i], sorted);
        }
        return out;
    }

    private static int tableSize(Map<String, Object> info, String section) {
        return ((Map<?, ?>) ((Map<?, ?>) info.get(section)).get("by_key")).size();
    }

    private static void putPreferringNoNbt(Map<String, ExportJob> table, String key, ExportJob job) {
        ExportJob existing = table.get(key);
        if (existing == null || !job.hasNbt || existing.hasNbt) {
            table.put(key, job);
        }
    }

    private static Map<String, String> paths(Map<String, ExportJob> table) {
        Map<String, String> sorted = new TreeMap<>();
        for (Map.Entry<String, ExportJob> e : table.entrySet()) {
            sorted.put(e.getKey(), e.getValue().imagePath);
        }
        return sorted;
    }

    private void writeJson(String name, Object value, boolean pretty) throws IOException {
        GsonBuilder builder = new GsonBuilder().disableHtmlEscaping();
        if (pretty) {
            builder.setPrettyPrinting();
        }
        Gson gson = builder.create();
        writeText(name, gson.toJson(value) + (pretty ? "\n" : ""));
    }

    private void writeText(String name, String content) throws IOException {
        File tmp = new File(outDir, name + ".tmp");
        try (Writer w = new OutputStreamWriter(new FileOutputStream(tmp), StandardCharsets.UTF_8)) {
            w.write(content);
        }
        Files.move(tmp.toPath(), new File(outDir, name).toPath(), StandardCopyOption.REPLACE_EXISTING);
    }
}

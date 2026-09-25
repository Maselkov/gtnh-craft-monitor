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
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import javax.imageio.ImageIO;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;

/**
 * Writes the three files the project consumes, each to a temp name first and renamed into place
 * only once complete, so a crashed run never leaves a half-written file under the real name:
 * <ul>
 * <li>{@code images.zip} - the icons (server/data/images.zip)</li>
 * <li>{@code icons_lookup.json} - key -> path tables (server/reference/icons_lookup.json)</li>
 * <li>{@code item_catalog.txt} - {@code mod:internal} IDs for the OC scanner (oc/item_catalog.txt)</li>
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

        report.put("catalogIds", catalog.size());
        for (Map.Entry<String, Object> table : lookup.entrySet()) {
            report.put(table.getKey() + "Entries", ((Map<?, ?>) table.getValue()).size());
        }
        writeJson("export-report.json", report, true);
    }

    /**
     * Same tables and tie-breaking as the old NESQL-based generate_icons_lookup.py: when several
     * stacks share a key (they differ only in NBT, which OC never reports), the one without NBT
     * wins; otherwise the last one listed wins. Only stacks that actually rendered are included.
     */
    static Map<String, Object> buildLookup(List<ExportJob> jobs) {
        Map<String, ExportJob> byKey = new HashMap<>();
        Map<String, ExportJob> fluidsByKey = new HashMap<>();
        Map<String, ExportJob> byLabel = new HashMap<>();
        // Items before fluids, as the old script did, so a fluid wins a label tie with an item.
        for (ExportJob.Kind kind : ExportJob.Kind.values()) {
            for (ExportJob job : jobs) {
                if (job.kind != kind || !job.rendered) {
                    continue;
                }
                putPreferringNoNbt(kind == ExportJob.Kind.ITEM ? byKey : fluidsByKey, job.lookupKey, job);
                if (job.label != null) {
                    putPreferringNoNbt(byLabel, job.label, job);
                }
            }
        }
        Map<String, Object> lookup = new LinkedHashMap<>();
        lookup.put("by_key", paths(byKey));
        lookup.put("fluids_by_key", paths(fluidsByKey));
        lookup.put("by_label", paths(byLabel));
        return lookup;
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

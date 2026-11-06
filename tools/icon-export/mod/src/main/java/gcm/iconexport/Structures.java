package gcm.iconexport;

import java.util.Map;

/**
 * structures.json.gz: the blocks each GregTech multiblock is built from, at each tier of its parts
 * (coils, glass, casings), for the server's multiblock kits. StructureBuilder does the work; this
 * only checks the mods it needs are there, so the export still runs without them.
 *
 * <p>{@code {"format": 1, "names": {key: name}, "multiblocks": {controller key: {"channels":
 * [{"name", "min", "max"}], "base": {key: n}, "tiers": {channel: {tier: {key: change}}},
 * "combos": {"a=1,b=2": {key: n}}, "approximate": true}}}}. Keys are ExportJob's
 * ({@code mod:internal:damage}).
 *
 * <p>{@code base} is the structure built with no channel set, as the hologram projector builds it
 * at its lowest tier. A channel's {@code tiers} say what changes from {@code base} when only that
 * channel is set to the tier: a coil or glass channel swaps one block for another (its "tiers" are
 * the blocks it takes, in order), a length or height channel adds blocks. A kit adds up the
 * changes of the tiers it picks. Where that doesn't work, because a channel changes what another
 * adds (an assembly line's glass comes with its length), {@code combos} holds every combination
 * built, keyed by the channels in order; {@code approximate} marks one with too many combinations
 * to build, where the sum is all there is.
 *
 * <p>Hatches, buses, mufflers and maintenance hatches aren't built: players pick their own, and
 * the places that take either a hatch or a casing get the casing, so casing counts are the most a
 * multiblock can need. Fluids (a water-filled structure's water) aren't counted.
 */
final class Structures {

    private Structures() {}

    /** structures.json.gz's contents, and layouts.json.gz's with their atlas (see Layouts). */
    static final class Result {

        final Map<String, Object> structures;
        final Layouts.Result layouts;

        Result(Map<String, Object> structures, Layouts.Result layouts) {
            this.structures = structures;
            this.layouts = layouts;
        }
    }

    /**
     * Builds every multiblock, then draws their blocks' textures with the renderer; null without
     * GregTech, StructureLib or BlockRenderer6343.
     */
    static Result collect(Map<String, Object> report, IconRenderer renderer) {
        try {
            Class.forName("gregtech.api.GregTechAPI");
            Class.forName("com.gtnewhorizon.structurelib.alignment.constructable.ISurvivalConstructable");
            Class.forName("blockrenderer6343.client.world.TrackedDummyWorld");
        } catch (Throwable t) {
            IconExportMod.LOG.info("No GregTech, StructureLib or BlockRenderer6343 ({}); no structures.json.",
                    t.toString());
            return null;
        }
        StructureBuilder builder;
        Map<String, Object> structures;
        try {
            builder = new StructureBuilder();
            structures = builder.collect(report);
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Couldn't build GregTech's multiblocks; no structures.json.", t);
            return null;
        }
        Layouts.Result layouts = null;
        renderer.begin();
        try {
            layouts = Layouts.finish(builder, renderer, report);
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Couldn't draw the multiblocks' textures; no layouts.json.", t);
        } finally {
            renderer.end();
        }
        return new Result(structures, layouts);
    }
}

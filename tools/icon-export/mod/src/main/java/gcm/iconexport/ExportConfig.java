package gcm.iconexport;

import java.io.File;

import net.minecraft.client.Minecraft;

/** Settings, all read from {@code -Dgcm.iconexport.*} system properties. */
final class ExportConfig {

    final File outDir;
    final int iconSize;
    final int renderBatchSize;
    final int menuDelayTicks;
    final int neiSettleTicks;
    final int neiTimeoutSeconds;
    /** Debugging aid: render only about this many icons, spread evenly over the list. 0 = all. */
    final int limit;
    /** Debugging aid: only render icons whose image path matches this regex. */
    final String only;
    /**
     * How many game ticks of animation to capture for animated icons (20 per second). Longer
     * animations are cut to this and loop. 0 = no animations, still icons only.
     */
    final int maxAnimationTicks;

    private ExportConfig(File outDir, int iconSize, int renderBatchSize, int menuDelayTicks, int neiSettleTicks,
            int neiTimeoutSeconds, int limit, String only, int maxAnimationTicks) {
        this.outDir = outDir;
        this.iconSize = iconSize;
        this.renderBatchSize = renderBatchSize;
        this.menuDelayTicks = menuDelayTicks;
        this.neiSettleTicks = neiSettleTicks;
        this.neiTimeoutSeconds = neiTimeoutSeconds;
        this.limit = limit;
        this.only = only;
        this.maxAnimationTicks = maxAnimationTicks;
    }

    static ExportConfig fromSystemProperties() {
        String out = System.getProperty("gcm.iconexport.outDir");
        File outDir = out != null && !out.trim().isEmpty() ? new File(out)
                : new File(Minecraft.getMinecraft().mcDataDir, "gcm-iconexport");
        return new ExportConfig(
                outDir,
                // 64 matches NESQL's default, which the existing images.zip was made with.
                Integer.getInteger("gcm.iconexport.iconSize", 64),
                Integer.getInteger("gcm.iconexport.batchSize", 128),
                Integer.getInteger("gcm.iconexport.menuDelayTicks", 100),
                Integer.getInteger("gcm.iconexport.neiSettleTicks", 20),
                Integer.getInteger("gcm.iconexport.neiTimeoutSeconds", 600),
                Integer.getInteger("gcm.iconexport.limit", 0),
                System.getProperty("gcm.iconexport.only"),
                // 400 ticks (20 s) sees 99% of the pack's texture cycles (up to 384 ticks) once;
                // icons stop earlier once they've repeated (ExportDriver.repeats).
                Integer.getInteger("gcm.iconexport.maxAnimationTicks", 400));
    }
}

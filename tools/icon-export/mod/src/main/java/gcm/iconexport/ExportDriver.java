package gcm.iconexport;

import java.awt.image.BufferedImage;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiScreen;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.eventhandler.SubscribeEvent;
import cpw.mods.fml.common.gameevent.TickEvent;

/**
 * Drives the export from ticks at the main menu: boot NEI's configs -> collect items (NEI's list
 * plus ore dictionary and recipes) -> render everything in batches from a GUI screen -> find and
 * capture the animated icons -> write files -> exit. No world is ever loaded.
 *
 * <p>Animations: every still icon is rendered with the animated textures at their first frame
 * ({@link AnimationClock}). Everything is then rendered again with those textures moved on a
 * frame; icons that changed are rendered once more at the first frame, to tell animated icons
 * (same image again) from ones that come out different every time (Avaritia's jittering halos,
 * the compass at the menu), which stay still. The animated ones are rendered tick by tick for
 * {@code maxAnimationTicks}, keeping each icon's distinct frames. Finally they're rendered at
 * the first frame once more: some renderers follow the system clock (GT's colour-cycling
 * materials) slowly enough to pass the first check, and by now they've moved on. Captured tick
 * by tick, those would play back far too fast, so they stay still too.
 *
 * <p>Public because FML's generated event-handler classes can't call into a package-private one.
 */
public final class ExportDriver {

    private static final int MAX_REPORTED_PROBLEMS = 500;

    private enum Stage {
        WAIT_MENU,
        WAIT_NEI,
        RENDERING,
        DETECTING,
        VERIFYING,
        CAPTURING,
        RECHECKING,
        DONE
    }

    private final ExportConfig config;
    private final Minecraft mc = Minecraft.getMinecraft();

    private Stage stage = Stage.WAIT_MENU;
    private int ticks;
    private long stageStartedAt;
    private int settledTicks;
    private final long startedAt = System.currentTimeMillis();

    private List<ExportJob> jobs;
    private int nextJob;
    private IconRenderer renderer;
    private ExportOutput output;
    private int itemsRendered, itemsBlank, itemsFailed, fluidsRendered, fluidsBlank, fluidsFailed;
    private final List<String> blank = new ArrayList<>();
    private final List<String> failed = new ArrayList<>();
    private long renderStartedAt;

    private AnimationClock clock;
    /** The jobs the current animation stage goes through, and where it's up to. */
    private List<ExportJob> passJobs;
    private int passNext;
    private final List<ExportJob> changed = new ArrayList<>();
    private final List<Capture> captures = new ArrayList<>();
    private int captureTick;
    private int randomIcons, clockIcons, animationFrames;
    private long animationStartedAt;

    /** An animated icon being captured: the frame shown at each tick, by pixel hash. */
    private static final class Capture {

        final ExportJob job;
        final long[] ticks;
        final Set<Long> written = new HashSet<>();
        /** Failed to render, or follows the clock rather than the animation ticks. */
        boolean failed;

        Capture(ExportJob job, int ticks) {
            this.job = job;
            this.ticks = new long[ticks];
        }
    }

    ExportDriver(ExportConfig config) {
        this.config = config;
    }

    @SubscribeEvent
    public void onClientTick(TickEvent.ClientTickEvent event) {
        if (event.phase != TickEvent.Phase.END) {
            return;
        }
        ticks++;
        try {
            switch (stage) {
                case WAIT_MENU:
                    // Client ticks only start once loading is done; the delay lets the main menu
                    // finish settling (resource reloads etc.) first.
                    if (ticks >= config.menuDelayTicks) {
                        IconExportMod.LOG.info("At the main menu; loading NEI's configs.");
                        enterStage(Stage.WAIT_NEI);
                        NeiItems.boot();
                    }
                    break;
                case WAIT_NEI:
                    waitForNei();
                    break;
                default:
                    break;
            }
        } catch (Throwable t) {
            fail("Export failed in stage " + stage, t);
        }
    }

    /**
     * Renders from inside a GUI screen's draw call, the context NEI normally draws items in: the
     * GL state is what GUI item rendering expects, and nothing else draws in between batches.
     */
    private final class ExportScreen extends GuiScreen {
        @Override
        public void drawScreen(int mouseX, int mouseY, float partialTicks) {
            try {
                switch (stage) {
                    case RENDERING:
                        renderBatch();
                        if (nextJob >= jobs.size()) {
                            startAnimations();
                        }
                        break;
                    case DETECTING:
                    case VERIFYING:
                        animationBatch();
                        break;
                    case CAPTURING:
                        captureBatch();
                        break;
                    case RECHECKING:
                        recheckBatch();
                        break;
                    default:
                        break;
                }
            } catch (Throwable t) {
                fail("Export failed while rendering (stage " + stage + ")", t);
            }
        }
    }

    private void waitForNei() throws Exception {
        if (NeiItems.ready()) {
            if (++settledTicks >= config.neiSettleTicks) {
                IconExportMod.LOG.info("NEI configs loaded after {}s.", stageSeconds());
                startRendering();
            }
            return;
        }
        if (stageSeconds() > config.neiTimeoutSeconds) {
            fail("Timed out after " + config.neiTimeoutSeconds + "s waiting for NEI's configs to load.");
        }
    }

    private void startRendering() throws Exception {
        if (!IconRenderer.framebuffersAvailable()) {
            throw new IllegalStateException(
                    "Framebuffers are unavailable (fboEnable=false in options.txt, or no GL support).");
        }

        jobs = new ArrayList<>();
        Set<String> paths = new HashSet<>();
        List<ItemStack> stacks = NeiItems.collect();
        IconExportMod.LOG.info("NEI item list: {} stacks.", stacks.size());
        List<ItemStack> fromRecipes = RecipeItems.collect();
        IconExportMod.LOG.info("Ore dictionary and recipes: {} stacks.", fromRecipes.size());
        stacks.addAll(fromRecipes);
        // Recipes repeat the same stacks hundreds of thousands of times; drop repeats before the
        // comparatively expensive job construction (display names, NBT hashes).
        Set<String> seen = new HashSet<>();
        for (ItemStack stack : stacks) {
            String identity = Item.itemRegistry.getNameForObject(stack.getItem()) + ":" + stack.getItemDamage()
                    + ":" + (stack.hasTagCompound() ? stack.getTagCompound().toString() : "");
            if (!seen.add(identity)) {
                continue;
            }
            ExportJob job = ExportJob.forItem(stack);
            if (job != null && paths.add(job.imagePath)) {
                jobs.add(job);
            }
        }
        for (Fluid fluid : FluidRegistry.getRegisteredFluids().values()) {
            ExportJob job = ExportJob.forFluid(fluid);
            if (job != null && paths.add(job.imagePath)) {
                jobs.add(job);
            }
        }
        jobs = applyDebugFilters(jobs);
        int itemJobs = 0;
        for (ExportJob job : jobs) {
            if (job.kind == ExportJob.Kind.ITEM) {
                itemJobs++;
            }
        }
        IconExportMod.LOG.info(
                "Rendering {} item and {} fluid icons at {}px into {}.",
                itemJobs,
                jobs.size() - itemJobs,
                config.iconSize,
                config.outDir.getAbsolutePath());

        output = new ExportOutput(config.outDir);
        renderer = new IconRenderer(config.iconSize);
        if (config.maxAnimationTicks > 0) {
            clock = AnimationClock.create();
        }
        mc.displayGuiScreen(new ExportScreen());
        renderStartedAt = System.currentTimeMillis();
        enterStage(Stage.RENDERING);
    }

    /** The {@code only} and {@code limit} debugging aids; see ExportConfig. */
    private List<ExportJob> applyDebugFilters(List<ExportJob> all) {
        List<ExportJob> kept = all;
        if (config.only != null) {
            Pattern only = Pattern.compile(config.only);
            kept = new ArrayList<>();
            for (ExportJob job : all) {
                if (only.matcher(job.imagePath).find()) {
                    kept.add(job);
                }
            }
        }
        if (config.limit > 0 && kept.size() > config.limit) {
            int stride = kept.size() / config.limit;
            List<ExportJob> sample = new ArrayList<>();
            for (int i = 0; i < kept.size(); i += stride) {
                sample.add(kept.get(i));
            }
            kept = sample;
        }
        return kept;
    }

    private void renderBatch() throws Exception {
        // The game moves animations on between batches; put them back at their first frame.
        if (clock != null) {
            clock.seek(0);
        }
        renderer.begin();
        try {
            int end = Math.min(jobs.size(), nextJob + config.renderBatchSize);
            for (; nextJob < end; nextJob++) {
                renderOne(jobs.get(nextJob));
            }
        } finally {
            renderer.end();
        }

        if (nextJob % 5000 < config.renderBatchSize || nextJob >= jobs.size()) {
            double seconds = (System.currentTimeMillis() - renderStartedAt) / 1000.0;
            IconExportMod.LOG.info(
                    "Rendered {}/{} ({} blank, {} failed) in {}s.",
                    nextJob,
                    jobs.size(),
                    itemsBlank + fluidsBlank,
                    itemsFailed + fluidsFailed,
                    Math.round(seconds));
        }
    }

    private void renderOne(ExportJob job) throws Exception {
        boolean item = job.kind == ExportJob.Kind.ITEM;
        BufferedImage image;
        try {
            image = item ? renderer.renderItem(job.item) : renderer.renderFluid(job.fluid);
        } catch (Throwable t) {
            if (item) itemsFailed++;
            else fluidsFailed++;
            note(failed, job.imagePath + ": " + t);
            return;
        }
        if (image == null) {
            if (item) itemsBlank++;
            else fluidsBlank++;
            note(blank, job.imagePath);
            return;
        }
        output.writeImage(job.imagePath, image);
        job.rendered = true;
        job.stillHash = hash(image);
        if (item) itemsRendered++;
        else fluidsRendered++;
    }

    /** Renders a job for the animation stages; null if its renderer threw. */
    private BufferedImage renderQuietly(ExportJob job) {
        try {
            BufferedImage image = job.kind == ExportJob.Kind.ITEM ? renderer.renderItem(job.item)
                    : renderer.renderFluid(job.fluid);
            return image != null ? image : blankImage();
        } catch (Throwable t) {
            return null;
        }
    }

    private BufferedImage blankImage() {
        return new BufferedImage(config.iconSize, config.iconSize, BufferedImage.TYPE_INT_ARGB);
    }

    /** 64-bit hash of an image's pixels, to compare renders. */
    static long hash(BufferedImage image) {
        int w = image.getWidth(), h = image.getHeight();
        int[] pixels = image.getRGB(0, 0, w, h, null, 0, w);
        long hash = 0xcbf29ce484222325L;
        for (int p : pixels) {
            hash = (hash ^ p) * 0x100000001b3L;
        }
        return hash;
    }

    @SuppressWarnings("unchecked")
    private void startAnimations() throws Exception {
        if (clock == null || clock.spriteCount() == 0) {
            finish();
            return;
        }
        // Only icons the lookup points at can ever be shown; the rest (mostly NBT variants that
        // share a key) aren't worth animating.
        Set<String> referenced = new HashSet<>();
        for (Object table : ExportOutput.buildLookup(jobs).values()) {
            referenced.addAll(((Map<?, String>) table).values());
        }
        passJobs = new ArrayList<>();
        for (ExportJob job : jobs) {
            if (job.rendered && referenced.contains(job.imagePath)) {
                passJobs.add(job);
            }
        }
        passNext = 0;
        animationStartedAt = System.currentTimeMillis();
        IconExportMod.LOG.info("Looking for animated icons among {}.", passJobs.size());
        enterStage(Stage.DETECTING);
    }

    /** DETECTING: which icons change when the animations move on. VERIFYING: which of those are
     *  the same as the still render again when the animations go back. */
    private void animationBatch() throws Exception {
        boolean detecting = stage == Stage.DETECTING;
        if (detecting) {
            clock.seekChanged();
        } else {
            clock.seek(0);
        }
        renderer.begin();
        try {
            int end = Math.min(passJobs.size(), passNext + config.renderBatchSize);
            for (; passNext < end; passNext++) {
                ExportJob job = passJobs.get(passNext);
                BufferedImage image = renderQuietly(job);
                if (image == null) {
                    continue;
                }
                boolean same = hash(image) == job.stillHash;
                if (detecting && !same) {
                    changed.add(job);
                } else if (!detecting) {
                    if (same) {
                        captures.add(new Capture(job, config.maxAnimationTicks));
                    } else {
                        randomIcons++;
                    }
                }
            }
        } finally {
            renderer.end();
        }
        if (passNext < passJobs.size()) {
            return;
        }
        if (detecting) {
            IconExportMod.LOG.info("{} icons change with the animations; checking they're stable.", changed.size());
            passJobs = changed;
            passNext = 0;
            enterStage(Stage.VERIFYING);
        } else {
            IconExportMod.LOG.info(
                    "{} animated icons ({} change on every render and stay still); capturing {} ticks.",
                    captures.size(),
                    randomIcons,
                    config.maxAnimationTicks);
            passNext = 0;
            captureTick = 0;
            if (captures.isEmpty()) {
                finish();
            } else {
                enterStage(Stage.CAPTURING);
            }
        }
    }

    /** CAPTURING: every animated icon at captureTick, then the next tick. */
    private void captureBatch() throws Exception {
        clock.seek(captureTick);
        renderer.begin();
        try {
            int end = Math.min(captures.size(), passNext + config.renderBatchSize);
            for (; passNext < end; passNext++) {
                Capture capture = captures.get(passNext);
                if (capture.failed) {
                    continue;
                }
                BufferedImage image = renderQuietly(capture.job);
                if (image == null) {
                    capture.failed = true;
                    continue;
                }
                long frame = hash(image);
                capture.ticks[captureTick] = frame;
                if (capture.written.add(frame)) {
                    output.writeFrame(capture.job.imagePath, Long.toHexString(frame), image);
                    animationFrames++;
                }
            }
        } finally {
            renderer.end();
        }
        if (passNext < captures.size()) {
            return;
        }
        passNext = 0;
        captureTick++;
        if (captureTick % 20 == 0 || captureTick == config.maxAnimationTicks) {
            IconExportMod.LOG.info(
                    "Captured {}/{} ticks of {} animated icons.",
                    captureTick,
                    config.maxAnimationTicks,
                    captures.size());
        }
        if (captureTick >= config.maxAnimationTicks) {
            passNext = 0;
            enterStage(Stage.RECHECKING);
        }
    }

    /** RECHECKING: animated icons at the first frame again, minutes after their still render. */
    private void recheckBatch() throws Exception {
        clock.seek(0);
        renderer.begin();
        try {
            int end = Math.min(captures.size(), passNext + config.renderBatchSize);
            for (; passNext < end; passNext++) {
                Capture capture = captures.get(passNext);
                if (capture.failed) {
                    continue;
                }
                BufferedImage image = renderQuietly(capture.job);
                if (image == null || hash(image) != capture.job.stillHash) {
                    capture.failed = true;
                    clockIcons++;
                }
            }
        } finally {
            renderer.end();
        }
        if (passNext >= captures.size()) {
            IconExportMod.LOG.info("{} animated icons follow the clock instead; they stay still.", clockIcons);
            finish();
        }
    }

    /** Each capture as [frame, ticks] runs, for animations.json. */
    private Map<String, List<Object[]>> animationSequences() {
        Map<String, List<Object[]>> sequences = new LinkedHashMap<>();
        for (Capture capture : captures) {
            if (capture.failed || capture.written.size() < 2) {
                continue;
            }
            List<Object[]> runs = new ArrayList<>();
            for (long frame : capture.ticks) {
                Object[] last = runs.isEmpty() ? null : runs.get(runs.size() - 1);
                String id = Long.toHexString(frame);
                if (last != null && last[0].equals(id)) {
                    last[1] = (Integer) last[1] + 1;
                } else {
                    runs.add(new Object[] { id, 1 });
                }
            }
            sequences.put(capture.job.imagePath, runs);
        }
        return sequences;
    }

    private void finish() throws Exception {
        enterStage(Stage.DONE);
        renderer.destroy();

        Map<String, Object> report = new LinkedHashMap<>();
        report.put("iconSize", config.iconSize);
        report.put("seconds", (System.currentTimeMillis() - startedAt) / 1000);
        report.put("renderSeconds", (System.currentTimeMillis() - renderStartedAt) / 1000);
        report.put("itemsRendered", itemsRendered);
        report.put("itemsBlank", itemsBlank);
        report.put("itemsFailed", itemsFailed);
        report.put("fluidsRendered", fluidsRendered);
        report.put("fluidsBlank", fluidsBlank);
        report.put("fluidsFailed", fluidsFailed);
        Map<String, List<Object[]>> sequences = animationSequences();
        report.put("animatedTextures", clock != null ? clock.spriteCount() : 0);
        report.put("animatedIcons", sequences.size());
        report.put("randomIcons", randomIcons);
        report.put("clockIcons", clockIcons);
        report.put("animationFrames", animationFrames);
        report.put("animationTicks", config.maxAnimationTicks);
        report.put(
                "animationSeconds",
                animationStartedAt > 0 ? (System.currentTimeMillis() - animationStartedAt) / 1000 : 0);
        report.put("failed", failed);
        report.put("blank", blank);
        output.finishAnimations(sequences, 50);
        output.finish(jobs, report);

        IconExportMod.LOG.info("Icon export complete: {}", config.outDir.getAbsolutePath());
        exit(0);
    }

    private void note(List<String> list, String entry) {
        if (list.size() < MAX_REPORTED_PROBLEMS) {
            list.add(entry);
        }
    }

    private void enterStage(Stage next) {
        stage = next;
        stageStartedAt = System.currentTimeMillis();
    }

    private long stageSeconds() {
        return (System.currentTimeMillis() - stageStartedAt) / 1000;
    }

    private void fail(String message) {
        fail(message, null);
    }

    private void fail(String message, Throwable t) {
        stage = Stage.DONE;
        IconExportMod.LOG.error(message, t);
        exit(2);
    }

    /** Exit the game; the watchdog covers a mod's shutdown hook hanging. */
    private static void exit(int code) {
        Thread watchdog = new Thread(() -> {
            try {
                Thread.sleep(60_000L);
            } catch (InterruptedException ignored) {}
            Runtime.getRuntime().halt(code);
        }, "gcm-iconexport-exit-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
        FMLCommonHandler.instance().exitJava(code, false);
    }
}

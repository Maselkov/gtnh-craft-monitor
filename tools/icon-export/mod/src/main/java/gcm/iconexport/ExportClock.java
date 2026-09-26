package gcm.iconexport;

import java.util.Random;

import net.minecraft.client.Minecraft;

import org.lwjgl.Sys;

/**
 * The time some renderers see during the export, in place of the system clock. Calls to the
 * clock are redirected here by {@link gcm.iconexport.core.ClockTransformer}.
 *
 * <p>GTNHLib's cosmic shader (Eternal Singularity, Avaritia's infinity gear), GT's wireframe
 * tesseract and glitch effect and Galacticraft's rockets animate by the system clock, so every
 * render of them differed and the export kept them still. They now see the capture tick as time, so they're captured frame by frame like texture
 * animations. The enchantment glint also follows the system clock; it's frozen instead, since
 * glinting items are many and a glint changes the whole icon every frame (hundreds of MB as
 * animations). Until the export sets a tick, everything passes through to the real clock.
 *
 * <p>Randomness in those renderers is redirected here too. Each draw starts a fresh sequence seeded
 * by the tick ({@link #beginDraw()}), so a renderer that jitters on every draw draws the same
 * jitter for the same tick and a different one each tick: a shimmer the export captures like any
 * animation, instead of an icon that never matches itself.
 */
public final class ExportClock {

    /** The glint's frozen position: its offsets are this modulo 3000 and 4873 ms. */
    private static final long GLINT_MILLIS = 0L;

    private static volatile boolean active;
    private static volatile int tick;
    private static final Random DRAW = new Random();

    private ExportClock() {}

    static void set(int tick) {
        ExportClock.tick = tick;
        active = true;
    }

    static int tick() {
        return tick;
    }

    /** Called before every draw: the random calls it makes start from the tick's own seed. */
    static void beginDraw() {
        DRAW.setSeed(0x6763_6D00L ^ tick);
    }

    public static double randomGaussian(Random own) {
        return active ? DRAW.nextGaussian() : own.nextGaussian();
    }

    public static double randomDouble(Random own) {
        return active ? DRAW.nextDouble() : own.nextDouble();
    }

    public static float randomFloat(Random own) {
        return active ? DRAW.nextFloat() : own.nextFloat();
    }

    public static int randomInt(Random own) {
        return active ? DRAW.nextInt() : own.nextInt();
    }

    public static int randomInt(Random own, int bound) {
        return active ? DRAW.nextInt(bound) : own.nextInt(bound);
    }

    public static long randomLong(Random own) {
        return active ? DRAW.nextLong() : own.nextLong();
    }

    public static boolean randomBoolean(Random own) {
        return active ? DRAW.nextBoolean() : own.nextBoolean();
    }

    /** Replaces {@code Math.random()}. */
    public static double mathRandom() {
        return active ? DRAW.nextDouble() : Math.random();
    }

    /** Replaces {@code System.currentTimeMillis()} for animations. */
    public static long animationMillis() {
        return active ? tick * 50L : System.currentTimeMillis();
    }

    /** Replaces {@code System.nanoTime()} for animations. */
    public static long animationNanos() {
        return active ? tick * 50_000_000L : System.nanoTime();
    }

    /** Replaces LWJGL's {@code Sys.getTime()} (in {@code Sys.getTimerResolution()} units). */
    public static long animationSysTime() {
        return active ? tick * 50L * Sys.getTimerResolution() / 1000L : Sys.getTime();
    }

    /** Replaces {@code Minecraft.getSystemTime()} for animations. */
    public static long animationSystemTime() {
        return active ? tick * 50L : Minecraft.getSystemTime();
    }

    /** Replaces {@code Minecraft.getSystemTime()} for the enchantment glint. */
    public static long glintSystemTime() {
        return active ? GLINT_MILLIS : Minecraft.getSystemTime();
    }
}

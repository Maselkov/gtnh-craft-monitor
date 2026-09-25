package gcm.iconexport;

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
 */
public final class ExportClock {

    /** The glint's frozen position: its offsets are this modulo 3000 and 4873 ms. */
    private static final long GLINT_MILLIS = 0L;

    private static volatile boolean active;
    private static volatile int tick;

    private ExportClock() {}

    static void set(int tick) {
        ExportClock.tick = tick;
        active = true;
    }

    static int tick() {
        return tick;
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

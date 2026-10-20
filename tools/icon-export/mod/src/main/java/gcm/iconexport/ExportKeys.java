package gcm.iconexport;

import org.lwjgl.input.Keyboard;

/**
 * Where {@link gcm.iconexport.core.KeyTransformer} points the game's and mods' keyboard checks: the
 * real keyboard, except that Shift reads as held while {@link #shift} is set. Tooltips that hide
 * a section behind Shift (GregTech's multiblock structures) then show it to {@link Tooltips}.
 *
 * <p>Public because the transformed classes call it from other packages.
 */
public final class ExportKeys {

    private static final int LSHIFT = 42;
    private static final int RSHIFT = 54;

    static boolean shift;

    private ExportKeys() {}

    public static boolean isKeyDown(int key) {
        if (shift && (key == LSHIFT || key == RSHIFT)) {
            return true;
        }
        return Keyboard.isKeyDown(key);
    }
}

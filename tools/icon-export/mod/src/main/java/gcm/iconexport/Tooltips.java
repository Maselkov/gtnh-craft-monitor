package gcm.iconexport;

import java.util.ArrayList;
import java.util.List;

import net.minecraft.item.ItemStack;

/**
 * An item's tooltip below its name, the way the game shows it with no key held: GregTech's
 * multiblocks show their machine type and info lines, and keep the structure for Shift.
 * Formatting codes ({@code §e}) are kept, for the page to colour the text as the game does.
 * {@link #withShift} reads it again with Shift held (see ExportKeys).
 */
final class Tooltips {

    private Tooltips() {}

    /** The tooltip with Shift held, or null if it's no different from {@code plain}. */
    static String withShift(ItemStack stack, String plain) {
        ExportKeys.shift = true;
        try {
            String shifted = of(stack);
            return shifted == null || shifted.equals(plain) ? null : shifted;
        } finally {
            ExportKeys.shift = false;
        }
    }

    /** The formatting code its name is coloured with by rarity, or 0 for common items. */
    static char rarity(ItemStack stack) {
        try {
            // EnumChatFormatting's toString() is its code: "§e".
            String code = stack.getRarity().rarityColor.toString();
            char c = code.length() == 2 ? code.charAt(1) : 0;
            return c == 'f' ? 0 : c;
        } catch (Throwable t) {
            return 0;
        }
    }

    /** The lines after the name joined with newlines, or null if there are none. */
    @SuppressWarnings("unchecked")
    static String of(ItemStack stack) {
        List<String> lines;
        try {
            // No player at the main menu. getTooltip() also fires ItemTooltipEvent, where other
            // mods add lines; the first line is the display name.
            List<String> all = stack.getTooltip(null, false);
            lines = all.isEmpty() ? all : all.subList(1, all.size());
        } catch (Throwable t) {
            // Some handler wanted the player; the item's own lines may still come.
            lines = new ArrayList<>();
            try {
                stack.getItem().addInformation(stack, null, lines, false);
            } catch (Throwable ignored) {
                return null;
            }
        }
        StringBuilder text = new StringBuilder();
        int blankRun = 0;
        for (Object line : lines) {
            String s = line == null ? "" : line.toString();
            if (s.trim().startsWith("§!")) {
                // GTNH's "§!shift"/"§!ctrl": a marker its tooltip renderer draws as "[shift]" under
                // the name. The page shows that itself where it has the Shift text.
                continue;
            }
            if (s.replaceAll("§.", "").trim().isEmpty()) {
                blankRun++;
                continue;
            }
            if (text.length() > 0) {
                // Blank lines in the middle separate sections; keep one.
                text.append(blankRun > 0 ? "\n\n" : "\n");
            }
            blankRun = 0;
            text.append(s);
        }
        return text.length() == 0 ? null : text.toString();
    }
}

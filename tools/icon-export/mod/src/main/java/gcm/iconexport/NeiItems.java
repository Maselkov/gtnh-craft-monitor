package gcm.iconexport;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedList;
import java.util.List;
import java.util.Set;

import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.util.IIcon;

import codechicken.nei.NEIClientConfig;
import codechicken.nei.api.ItemInfo;

/**
 * NEI's item list, built from the main menu without a world, and including the items NEI hides.
 *
 * <p>NEI only builds {@code ItemList.items} once a player exists, and only loads its plugin configs
 * (which add item variants and override sub-item lists) on world load. {@link #boot()} triggers
 * that config load directly, and {@link #collect()} applies the rules of NEI's
 * {@code ItemListLoader.getPermutations} (NotEnoughItems 2.8.x) to the item registry.
 *
 * <p>Except one: NEI's hidden-item filter is not applied. GTNH hides thousands of items that
 * players do end up holding - GregTech ores in other stone types, tiny/impure dust piles, crushed
 * ores - and they need icons and catalog entries like anything else. NESQL-based exports always had
 * them, since NESQL also took items from recipes.
 */
final class NeiItems {

    private NeiItems() {}

    /**
     * Starts NEI's config/plugin loading. {@code loadWorld} is what NEI calls on world join; it only
     * needs a directory for NEI's per-world settings file, not an actual world.
     */
    static void boot() {
        try {
            NEIClientConfig.loadWorld("gcm-iconexport");
        } catch (Throwable t) {
            // Its last step reads per-world info from the (absent) world; boot has already started
            // by then, which is all that's needed.
            IconExportMod.LOG.debug("NEI world-load tail failed without a world (expected).", t);
        }
    }

    /** True once NEI's plugin configs (overrides, variants) have all loaded. */
    static boolean ready() {
        return NEIClientConfig.isLoaded();
    }

    static List<ItemStack> collect() {
        List<ItemStack> stacks = new ArrayList<>();
        for (Object entry : Item.itemRegistry) {
            Item item = (Item) entry;
            if (item == null || item.delegate.name() == null) {
                continue;
            }
            try {
                stacks.addAll(permutations(item));
            } catch (Throwable t) {
                // NEI drops items that throw here, too.
                IconExportMod.LOG.warn("Skipping {}: {}", item.delegate.name(), t.toString());
            }
        }
        return stacks;
    }

    @SuppressWarnings("unchecked")
    private static List<ItemStack> permutations(Item item) {
        List<ItemStack> permutations = new LinkedList<>(ItemInfo.itemOverrides.get(item));
        if (permutations.isEmpty()) {
            item.getSubItems(item, null, permutations);
        }
        if (permutations.isEmpty()) {
            damageSearch(item, permutations);
        }
        permutations.addAll(ItemInfo.itemVariants.get(item));

        List<ItemStack> valid = new ArrayList<>();
        for (ItemStack stack : permutations) {
            if (stack != null && stack.getItem() != null && stack.getItem().delegate.name() != null) {
                valid.add(stack);
            }
        }
        return valid;
    }

    /** For items without sub-items: damage 0-15, keeping only values that look distinct. */
    @SuppressWarnings("unchecked")
    private static void damageSearch(Item item, List<ItemStack> permutations) {
        Set<String> seen = new HashSet<>();
        for (int damage = 0; damage < 16; damage++) {
            try {
                ItemStack stack = new ItemStack(item, 1, damage);
                IIcon icon = item.getIconIndex(stack);
                List<String> tooltip = new ArrayList<>();
                try {
                    item.addInformation(stack, null, tooltip, false);
                } catch (Throwable ignored) {}
                String key = stack.getDisplayName() + "@" + (icon == null ? 0 : icon.hashCode()) + "@"
                        + String.join("\n", tooltip);
                if (seen.add(key)) {
                    permutations.add(stack);
                }
            } catch (Throwable ignored) {}
        }
    }
}

package gcm.iconexport;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import net.minecraft.item.ItemStack;

import codechicken.nei.CollapsibleItems;
import codechicken.nei.ItemSorter;

/**
 * Where NEI puts each catalog item in its item list, for the recipe book: its position under NEI's
 * sort ({@code ItemSorter}, with the pack's configured chain: in GTNH, vanilla first, then by mod,
 * item ID, NEI's list order, damage and name), and which collapsible group it falls in
 * ({@code CollapsibleItems}: config/NEI/collapsibleitems.cfg plus groups mods register). Asking NEI
 * rather than copying its rules keeps both exact for the pack being exported.
 *
 * <p>Each catalog key is stood for by the first stack in NEI's list with that key; a wildcard key
 * by the first with that item; a key NEI doesn't list by the stack the recipes had, which sorts
 * after NEI's own items of the same mod and ID ({@code default} has no position for it).
 *
 * <p>Adds to recipes.json: {@code "item_rank"} (each of {@code items}' position, 0 first),
 * {@code "item_group"} (each one's group index, or -1) and {@code "item_groups"} ([{"name",
 * "expanded"}], a group's name "" where it has none). Left out when NEI can't sort or group.
 */
final class CatalogOrder {

    private CatalogOrder() {}

    static void addTo(Map<String, Object> file, List<String> keys, List<ItemStack> recipeStacks, List<ItemStack> nei) {
        Map<String, ItemStack> byKey = new HashMap<>();
        Map<String, ItemStack> byItem = new HashMap<>();
        IdentityHashMap<ItemStack, Integer> neiOrder = new IdentityHashMap<>();
        for (ItemStack stack : nei) {
            String key = Recipes.keyOf(stack);
            if (key == null) {
                continue;
            }
            neiOrder.put(stack, neiOrder.size());
            byKey.putIfAbsent(key, stack);
            byItem.putIfAbsent(key.substring(0, key.lastIndexOf(':')), stack);
        }
        List<ItemStack> standIns = new ArrayList<>(keys.size());
        int listed = 0;
        for (int i = 0; i < keys.size(); i++) {
            String key = keys.get(i);
            ItemStack stack = byKey.get(key);
            if (stack == null && key.endsWith(":*")) {
                stack = byItem.get(key.substring(0, key.length() - 2));
            }
            if (stack != null) {
                listed++;
            } else {
                stack = recipeStacks.get(i);
            }
            standIns.add(stack);
        }

        Map<String, Object> report = new LinkedHashMap<>();
        report.put("listed_by_nei", listed);
        report.put("not_listed", keys.size() - listed);
        List<Integer> ranks = rank(standIns, neiOrder, report);
        if (ranks != null) {
            file.put("item_rank", ranks);
        }
        group(file, standIns, report);
        IconExportMod.LOG.info("Catalog order and groups: {}", report);
    }

    private static List<Integer> rank(List<ItemStack> standIns, IdentityHashMap<ItemStack, Integer> neiOrder,
            Map<String, Object> report) {
        List<String> chain = new ArrayList<>();
        for (ItemSorter.SortEntry entry : ItemSorter.list) {
            chain.add(entry.name);
        }
        report.put("sort", chain);
        if (chain.isEmpty()) {
            IconExportMod.LOG.warn("NEI has no item sort; the catalog keeps its alphabetical order.");
            return null;
        }
        // NEI's "default" sort is the order its item list loader found stacks in; put ours there.
        Map<ItemStack, Integer> saved = ItemSorter.instance.ordering;
        ItemSorter.instance.ordering = neiOrder;
        List<Integer> order = new ArrayList<>(standIns.size());
        for (int i = 0; i < standIns.size(); i++) {
            order.add(i);
        }
        try {
            order.sort((a, b) -> {
                try {
                    return ItemSorter.instance.compare(standIns.get(a), standIns.get(b));
                } catch (Throwable t) {
                    return 0;
                }
            });
        } catch (IllegalArgumentException e) {
            IconExportMod.LOG.warn("NEI's item sort isn't consistent ({}); the catalog keeps its alphabetical order.",
                    e.toString());
            return null;
        } finally {
            ItemSorter.instance.ordering = saved;
        }
        Integer[] ranks = new Integer[standIns.size()];
        for (int position = 0; position < order.size(); position++) {
            ranks[order.get(position)] = position;
        }
        List<Integer> out = new ArrayList<>(ranks.length);
        for (Integer rank : ranks) {
            out.add(rank);
        }
        return out;
    }

    private static void group(Map<String, Object> file, List<ItemStack> standIns, Map<String, Object> report) {
        if (CollapsibleItems.isEmpty()) {
            report.put("groups", 0);
            return;
        }
        List<Integer> groups = new ArrayList<>(standIns.size());
        int grouped = 0, highest = -1;
        for (ItemStack stack : standIns) {
            int index = -1;
            try {
                CollapsibleItems.putItem(stack);
                index = CollapsibleItems.getGroupIndex(stack);
            } catch (Throwable ignored) {
                // A filter that throws on this stack (an NBT read): NEI wouldn't group it either.
            }
            groups.add(index);
            if (index >= 0) {
                grouped++;
                highest = Math.max(highest, index);
            }
        }
        List<Object> described = new ArrayList<>();
        for (int i = 0; i <= highest; i++) {
            Map<String, Object> group = new LinkedHashMap<>();
            String name;
            try {
                name = CollapsibleItems.getDisplayName(i);
            } catch (Throwable t) {
                name = null;
            }
            group.put("name", name == null ? "" : name);
            group.put("expanded", CollapsibleItems.isExpanded(i));
            described.add(group);
        }
        CollapsibleItems.clearCache();
        report.put("groups", described.size());
        report.put("grouped_items", grouped);
        file.put("item_group", groups);
        file.put("item_groups", described);
    }
}

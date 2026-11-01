package gcm.iconexport;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.function.Supplier;

import net.minecraft.init.Items;
import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.oredict.OreDictionary;

import codechicken.nei.PositionedStack;
import codechicken.nei.recipe.RecipeCatalysts;
import cpw.mods.fml.common.registry.GameRegistry;

/**
 * recipes.json.gz: GregTech's recipe maps, for the server's check of the network's patterns
 * against another GTNH version's recipes (server/gcm/recipes.py).
 *
 * <p>{@code {"format": 1, "items": [key, ...], "item_names": [...], "fluids": [name, ...],
 * "fluid_names": [...], "maps": {name: {"machines": [{"name", "key"}, ...], "recipes": [...]}}}}.
 * Items are keyed as ExportJob keys them ({@code mod:internal:damage}, damage {@code *} for any),
 * fluids by registry name, and recipes refer to both by index; the names lists hold each one's
 * display name (null where it has none). Each recipe is {@code {"i": [[item, n], ...], "f": [[fluid, mB], ...],
 * "o": [[item, n(, chance)], ...], "g": [[fluid, mB], ...], "e": EU/t, "d": ticks, "s": special}},
 * empty lists and a zero special value left out. n is 0 for an input the recipe doesn't use up (a
 * programmed circuit, a mold); a chance, in 1/10000, only where it's under 100%. The special value
 * is what the map makes of it: an EBF recipe's coil heat, for one.
 *
 * <p>A map's machines are what NEI lists as catalysts for its recipe categories: their names are
 * what the ME Interface Terminal calls a machine, which tells the server which map a pattern is
 * for. Fake recipes (NEI-only displays) and disabled ones are left out.
 *
 * <p>GregTech is read by reflection, so the mod still compiles against NEI alone and runs
 * without GregTech; field names that changed between GTNH versions have fallbacks.
 */
final class Recipes {

    private final Map<String, Integer> items = new LinkedHashMap<>();
    private final List<String> itemNames = new ArrayList<>();
    private final Map<String, Integer> fluids = new LinkedHashMap<>();
    private final List<String> fluidNames = new ArrayList<>();
    int recipeCount;
    int mapsWithMachines;

    /** The whole file's contents, or null without GregTech. */
    Map<String, Object> collect() {
        Map<?, ?> all;
        try {
            Field field = Class.forName("gregtech.api.recipe.RecipeMap").getField("ALL_RECIPE_MAPS");
            all = (Map<?, ?>) field.get(null);
        } catch (Throwable t) {
            IconExportMod.LOG.info("No GregTech recipe maps found ({}); no recipes.json.gz.", t.toString());
            return null;
        }
        Map<String, Object> maps = new TreeMap<>();
        boolean catalystsLoaded = false;
        for (Map.Entry<?, ?> entry : all.entrySet()) {
            Object map = entry.getValue();
            String name = String.valueOf(entry.getKey());
            List<Object> recipes = new ArrayList<>();
            try {
                Collection<?> list = (Collection<?>) map.getClass().getMethod("getAllRecipes").invoke(map);
                for (Object recipe : list) {
                    Map<String, Object> out = recipe(recipe);
                    if (out != null) {
                        recipes.add(out);
                    }
                }
            } catch (Throwable t) {
                IconExportMod.LOG.warn("Couldn't read GregTech recipe map {}: {}", name, t.toString());
                continue;
            }
            if (recipes.isEmpty()) {
                continue;
            }
            List<Object> machines = machines(map, name);
            if (machines.isEmpty() && !catalystsLoaded) {
                // NEI fills its catalyst table when a world loads; the export never loads one.
                catalystsLoaded = true;
                try {
                    RecipeCatalysts.loadCatalystInfo();
                    machines = machines(map, name);
                } catch (Throwable t) {
                    IconExportMod.LOG.warn("Couldn't load NEI's recipe catalysts: {}", t.toString());
                }
            }
            if (!machines.isEmpty()) {
                mapsWithMachines++;
            }
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("machines", machines);
            out.put("recipes", recipes);
            maps.put(name, out);
            recipeCount += recipes.size();
        }
        IconExportMod.LOG.info("Exported {} GregTech recipes from {} maps, {} with machines.", recipeCount,
                maps.size(), mapsWithMachines);
        Map<String, Object> file = new LinkedHashMap<>();
        file.put("format", 1);
        file.put("items", new ArrayList<>(items.keySet()));
        file.put("item_names", itemNames);
        file.put("fluids", new ArrayList<>(fluids.keySet()));
        file.put("fluid_names", fluidNames);
        file.put("maps", maps);
        return file;
    }

    private Map<String, Object> recipe(Object recipe) {
        if (flag(recipe, "mFakeRecipe", false) || !flag(recipe, "mEnabled", true)) {
            return null;
        }
        ItemStack[] inputs = (ItemStack[]) field(recipe, "mInputs");
        ItemStack[] outputs = (ItemStack[]) field(recipe, "mOutputs");
        FluidStack[] fluidInputs = (FluidStack[]) field(recipe, "mFluidInputs");
        FluidStack[] fluidOutputs = (FluidStack[]) field(recipe, "mFluidOutputs");
        // mChances before 2.9.0.
        Object chances = field(recipe, "mOutputChances");
        if (chances == null) {
            chances = field(recipe, "mChances");
        }
        Map<String, Object> out = new LinkedHashMap<>();
        putIfAny(out, "i", stacks(inputs, null));
        putIfAny(out, "f", fluidStacks(fluidInputs));
        putIfAny(out, "o", stacks(outputs, chances instanceof int[] ? (int[]) chances : null));
        putIfAny(out, "g", fluidStacks(fluidOutputs));
        if (!out.containsKey("o") && !out.containsKey("g")) {
            return null;
        }
        out.put("e", intField(recipe, "mEUt"));
        out.put("d", intField(recipe, "mDuration"));
        int special = intField(recipe, "mSpecialValue");
        if (special != 0) {
            out.put("s", special);
        }
        return out;
    }

    private List<Object> stacks(ItemStack[] stacks, int[] chances) {
        List<Object> out = new ArrayList<>();
        if (stacks == null) {
            return out;
        }
        for (int i = 0; i < stacks.length; i++) {
            Integer index = itemIndex(stacks[i]);
            if (index == null) {
                continue;
            }
            List<Integer> entry = new ArrayList<>(3);
            entry.add(index);
            entry.add(stacks[i].stackSize);
            if (chances != null && i < chances.length && chances[i] > 0 && chances[i] < 10000) {
                entry.add(chances[i]);
            }
            out.add(entry);
        }
        return out;
    }

    private List<Object> fluidStacks(FluidStack[] stacks) {
        List<Object> out = new ArrayList<>();
        if (stacks == null) {
            return out;
        }
        for (FluidStack stack : stacks) {
            if (stack == null || stack.getFluid() == null) {
                continue;
            }
            String name = stack.getFluid().getName();
            Integer index = fluids.get(name);
            if (index == null) {
                index = fluids.size();
                fluids.put(name, index);
                fluidNames.add(nameOf(() -> stack.getLocalizedName()));
            }
            List<Integer> entry = new ArrayList<>(2);
            entry.add(index);
            entry.add(stack.amount);
            out.add(entry);
        }
        return out;
    }

    private Integer itemIndex(ItemStack stack) {
        String key = keyOf(stack);
        if (key == null) {
            return null;
        }
        Integer index = items.get(key);
        if (index == null) {
            index = items.size();
            items.put(key, index);
            itemNames.add(nameOf(stack::getDisplayName));
        }
        return index;
    }

    /** As ExportJob keys items: the raw damage, which is what AE2 reports; "*" for any. */
    static String keyOf(ItemStack stack) {
        if (stack == null || stack.getItem() == null) {
            return null;
        }
        GameRegistry.UniqueIdentifier id;
        try {
            id = GameRegistry.findUniqueIdentifierFor(stack.getItem());
        } catch (Throwable t) {
            return null;
        }
        if (id == null) {
            return null;
        }
        int damage = Items.feather.getDamage(stack);
        return id.modId + ":" + id.name + ":"
                + (damage == OreDictionary.WILDCARD_VALUE ? "*" : Integer.toString(damage));
    }

    /**
     * NEI's catalysts for each of the map's recipe categories (whose unlocalized name is the NEI
     * handler's id), by name, without repeats.
     */
    private static List<Object> machines(Object map, String mapName) {
        Set<String> ids = new LinkedHashSet<>();
        ids.add(mapName);
        try {
            for (Object category : (Collection<?>) map.getClass().getMethod("getAssociatedCategories").invoke(map)) {
                ids.add((String) category.getClass().getField("unlocalizedName").get(category));
            }
        } catch (Throwable ignored) {}
        Map<String, Object> byName = new LinkedHashMap<>();
        for (String id : ids) {
            List<PositionedStack> catalysts;
            try {
                catalysts = RecipeCatalysts.getRecipeCatalysts(id);
            } catch (Throwable t) {
                continue;
            }
            for (PositionedStack positioned : catalysts) {
                ItemStack stack = positioned.item != null ? positioned.item
                        : positioned.items != null && positioned.items.length > 0 ? positioned.items[0] : null;
                String key = keyOf(stack);
                String name;
                try {
                    name = stack.getDisplayName();
                } catch (Throwable t) {
                    continue;
                }
                if (key == null || name == null || byName.containsKey(name)) {
                    continue;
                }
                Map<String, String> machine = new LinkedHashMap<>();
                machine.put("name", name);
                machine.put("key", key);
                byName.put(name, machine);
            }
        }
        return new ArrayList<>(byName.values());
    }

    /** A display name, or null where getting it throws (some need a world) or there's none. */
    private static String nameOf(Supplier<String> name) {
        try {
            String value = name.get();
            return value == null || value.isEmpty() ? null : value;
        } catch (Throwable t) {
            return null;
        }
    }

    private static void putIfAny(Map<String, Object> out, String key, List<Object> list) {
        if (!list.isEmpty()) {
            out.put(key, list);
        }
    }

    private static Object field(Object target, String name) {
        try {
            return target.getClass().getField(name).get(target);
        } catch (Throwable t) {
            return null;
        }
    }

    private static int intField(Object target, String name) {
        Object value = field(target, name);
        return value instanceof Integer ? (Integer) value : 0;
    }

    private static boolean flag(Object target, String name, boolean otherwise) {
        Object value = field(target, name);
        return value instanceof Boolean ? (Boolean) value : otherwise;
    }
}

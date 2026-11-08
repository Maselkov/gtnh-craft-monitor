package gcm.iconexport;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.function.Function;

import net.minecraft.item.ItemStack;
import net.minecraft.item.crafting.CraftingManager;
import net.minecraft.item.crafting.FurnaceRecipes;
import net.minecraft.item.crafting.IRecipe;
import net.minecraft.item.crafting.ShapedRecipes;
import net.minecraft.item.crafting.ShapelessRecipes;
import net.minecraftforge.oredict.OreDictionary;
import net.minecraftforge.oredict.ShapedOreRecipe;
import net.minecraftforge.oredict.ShapelessOreRecipe;

/**
 * Format-2 crafting/furnace maps. Slots retain every alternative, with null only for a shaped
 * grid's holes. Item identity deliberately remains registry id + damage, as in the GT maps;
 * NBT and subclass matching rules are marked approximate, never claimed to be exact NEI data.
 * Optional mod adapters use their public getters, by reflection, without compile dependencies.
 */
final class CraftingRecipes {
    private final Function<ItemStack, Integer> itemIndex;
    private final IdentityHashMap<Object, String> oreNames = new IdentityHashMap<>();
    int craftingCount;
    int furnaceCount;

    private final Function<ItemStack, List<String>> displays;

    CraftingRecipes(Function<ItemStack, Integer> itemIndex, Function<ItemStack, List<String>> displays) {
        this.displays = displays;
        this.itemIndex = itemIndex;
        for (String name : OreDictionary.getOreNames()) {
            oreNames.put(OreDictionary.getOres(name), name);
        }
    }

    Map<String, Object> collect(Map<String, Object> maps) {
        List<Object> crafting = new ArrayList<>();
        List<Object> archive = new ArrayList<>();
        Map<String, Object> craftingReport = report();
        @SuppressWarnings("unchecked")
        Map<String, Object> classes = (Map<String, Object>) craftingReport.get("classes");
        for (Object entry : CraftingManager.getInstance().getRecipeList()) {
            String cls = entry.getClass().getName();
            @SuppressWarnings("unchecked")
            Map<String, Object> classReport = (Map<String, Object>) classes.computeIfAbsent(cls, k -> report());
            increment(craftingReport, "registered");
            increment(classReport, "registered");
            try {
                Map<String, Object> body = crafting((IRecipe) entry);
                crafting.add(body);
                increment(craftingReport, "exported");
                increment(classReport, "exported");
            } catch (Skipped e) {
                archive.add(java.util.Arrays.asList(e.getMessage(), RecipeSnapshot.of(entry)));
                skip(craftingReport, e.getMessage());
                skip(classReport, e.getMessage());
            } catch (Throwable e) {
                skip(craftingReport, "adapter_error");
                skip(classReport, "adapter_error");
                IconExportMod.LOG.warn("Couldn't export crafting recipe {}: {}", cls, e.toString());
            }
        }
        craftingCount = crafting.size();
        Map<String,Object> craftingMap = map("Crafting Table", "minecraft:crafting_table:0", crafting);
        craftingMap.put("archive", archive);
        craftingReport.put("archived", archive.size());
        maps.put("crafting", craftingMap);

        List<Object> furnace = new ArrayList<>();
        Map<String, Object> furnaceReport = report();
        for (Object entry : FurnaceRecipes.smelting().getSmeltingList().entrySet()) {
            increment(furnaceReport, "registered");
            try {
                Map.Entry<?, ?> r = (Map.Entry<?, ?>) entry;
                TreeSet<String> approximate = new TreeSet<>();
                Map<String, Object> body = new LinkedHashMap<>();
                body.put("type", "furnace");
                body.put("i", Collections.singletonList(stack((ItemStack) r.getKey(), 1, approximate)));
                ItemStack output = (ItemStack) r.getValue();
                body.put("o", Collections.singletonList(stack(output, output.stackSize, approximate)));
                // Standard FurnaceRecipes smelting: 200 ticks. No fuel/XP claim is made.
                body.put("d", 200);
                if (!approximate.isEmpty()) body.put("approximate", new ArrayList<>(approximate));
                furnace.add(body);
                increment(furnaceReport, "exported");
            } catch (Skipped e) {
                skip(furnaceReport, e.getMessage());
            } catch (Throwable e) {
                skip(furnaceReport, "adapter_error");
                IconExportMod.LOG.warn("Couldn't export furnace recipe: {}", e.toString());
            }
        }
        furnaceCount = furnace.size();
        maps.put("furnace", map("Furnace", "minecraft:furnace:0", furnace));
        Map<String, Object> coverage = new LinkedHashMap<>();
        coverage.put("handlers", Arrays.asList("gregtech", "crafting", "furnace"));
        coverage.put("identity", "item_id_damage");
        coverage.put("limitations", Arrays.asList("NBT constraints and variants are not indexed",
                "Custom matching, tool damage and returned containers may differ",
                "NEI displays may omit costs or conditions; unavailable/non-enumerable handlers and dynamic crafting remain limited"));
        coverage.put("crafting", craftingReport);
        coverage.put("furnace", furnaceReport);
        IconExportMod.LOG.info("Exported {} crafting and {} furnace recipes.", craftingCount, furnaceCount);
        return coverage;
    }

    private Map<String, Object> crafting(IRecipe recipe) throws Exception {
        ItemStack output = recipe.getRecipeOutput();
        if (output == null) throw new Skipped("null_output");
        Object input;
        int width = 0, height = 0;
        boolean shaped = false;
        boolean mirrored = false;
        String cls = recipe.getClass().getName();
        if (Boolean.TRUE.equals(field(recipe, "disable")) || Boolean.TRUE.equals(field(recipe, "hidden"))) {
            throw new Skipped("disabled");
        }
        if (recipe instanceof ShapedRecipes) {
            ShapedRecipes r = (ShapedRecipes) recipe;
            input = r.recipeItems;
            width = r.recipeWidth;
            height = r.recipeHeight;
            shaped = true;
            mirrored = true;
        } else if (recipe instanceof ShapelessRecipes) {
            input = ((ShapelessRecipes) recipe).recipeItems;
        } else if (recipe instanceof ShapedOreRecipe) {
            input = ((ShapedOreRecipe) recipe).getInput(); // DreamCraft overrides this.
            width = number(recipe, "width");
            height = number(recipe, "height");
            shaped = true;
            mirrored = !cls.equals("com.dreammaster.recipes.ShapedUniversalRecipe")
                    && Boolean.TRUE.equals(field(recipe, "mirrored"));
        } else if (recipe instanceof ShapelessOreRecipe) {
            input = ((ShapelessOreRecipe) recipe).getInput();
        } else if (cls.equals("appeng.recipes.game.ShapedRecipe") || cls.equals("appeng.recipes.game.ShapelessRecipe")) {
            input = recipe.getClass().getMethod("getInput").invoke(recipe);
            if (cls.endsWith(".ShapedRecipe")) {
                width = ((Number) recipe.getClass().getMethod("getWidth").invoke(recipe)).intValue();
                height = ((Number) recipe.getClass().getMethod("getHeight").invoke(recipe)).intValue();
                shaped = true;
                mirrored = Boolean.TRUE.equals(field(recipe, "mirrored"));
            }
        } else if (cls.equals("ic2.core.AdvRecipe") || cls.equals("ic2.core.AdvShapelessRecipe")) {
            input = field(recipe, "input");
            if (cls.equals("ic2.core.AdvRecipe")) {
                // IC2 compacts occupied positions. Its masks are nine bits, MSB top-left.
                int[] masks = (int[]) field(recipe, "masks");
                List<Object> compact = elements(input);
                if (masks == null || masks.length == 0 || Integer.bitCount(masks[0]) != compact.size()) {
                    throw new Skipped("unsupported_layout");
                }
                Object[] slots = new Object[9];
                int next = 0;
                for (int n = 0; n < 9; n++) {
                    if ((masks[0] & (1 << (8 - n))) != 0) slots[n] = compact.get(next++);
                }
                input = slots;
                width = height = 3;
                shaped = true;
                mirrored = field(recipe, "masksMirrored") != null;
            }
            List<Object> expanded = new ArrayList<>();
            for (Object slot : elements(input)) {
                expanded.add(slot == null ? null : Class.forName("ic2.core.AdvRecipe")
                        .getMethod("expand", Object.class).invoke(null, slot));
            }
            input = expanded;
        } else {
            // Dynamic recipes (including NBT-dependent Shulker conversions) have no static grid.
            throw new Skipped("unsupported_class");
        }

        List<Object> rawSlots = elements(input);
        if (shaped ? width < 1 || width > 3 || height < 1 || height > 3 || rawSlots.size() != width * height
                : rawSlots.isEmpty() || rawSlots.size() > 9) throw new Skipped("unsupported_layout");
        TreeSet<String> approximate = new TreeSet<>();
        if (!Arrays.asList(ShapedRecipes.class.getName(), ShapelessRecipes.class.getName(),
                ShapedOreRecipe.class.getName(), ShapelessOreRecipe.class.getName()).contains(cls)) {
            approximate.add("custom_matching");
        }
        List<Object> slots = new ArrayList<>();
        boolean occupied = false;
        for (Object slot : rawSlots) {
            if (slot == null) {
                if (!shaped) throw new Skipped("unresolved_ingredient");
                slots.add(null);
            } else {
                slots.add(slot(slot, approximate));
                occupied = true;
            }
        }
        if (!occupied) throw new Skipped("unresolved_ingredient");
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("type", shaped ? "shaped" : "shapeless");
        if (shaped) {
            body.put("w", width);
            body.put("h", height);
            body.put("mirrored", mirrored);
        }
        body.put("slots", slots);
        body.put("o", Collections.singletonList(stack(output, output.stackSize, approximate)));
        if (!approximate.isEmpty()) body.put("approximate", new ArrayList<>(approximate));
        return body;
    }

    private Map<String, Object> slot(Object raw, TreeSet<String> approximate) throws Exception {
        String oreName = raw instanceof String ? (String) raw : oreNames.get(raw);
        Object input = raw instanceof String ? OreDictionary.getOres((String) raw) : raw;
        // AE2 ingredients expose their full baked alternatives through the public API.
        if (implementsInterface(input.getClass(), "appeng.api.recipes.IIngredient")) {
            if (Boolean.TRUE.equals(input.getClass().getMethod("isAir").invoke(input))) {
                throw new Skipped("unresolved_ingredient");
            }
            input = input.getClass().getMethod("getItemStackSet").invoke(input);
        }
        List<Object> choices = input instanceof ItemStack ? Collections.singletonList(input) : elements(input);
        Map<String, Object> unique = new LinkedHashMap<>();
        for (Object choice : choices) {
            if (!(choice instanceof ItemStack)) throw new Skipped("unsupported_ingredient");
            ItemStack stack = (ItemStack) choice;
            List<Object> entry = stack(stack, 1, approximate);
            unique.putIfAbsent(Recipes.keyOf(stack) + "|" + (stack.hasTagCompound() ? stack.getTagCompound().toString() : ""), entry);
        }
        if (unique.isEmpty()) throw new Skipped("unresolved_ingredient");
        Map<String, Object> slot = new LinkedHashMap<>();
        slot.put("alts", new ArrayList<>(unique.values()));
        if (oreName != null) slot.put("od", oreName);
        return slot;
    }

    private List<Object> stack(ItemStack stack, int amount, TreeSet<String> approximate) {
        Integer index = stack == null ? null : itemIndex.apply(stack);
        if (index == null || amount <= 0) throw new Skipped("unresolved_stack");
        if (stack.hasTagCompound()) approximate.add("nbt");
        return Arrays.asList(index, amount, null, displays.apply(stack));
    }

    private static boolean implementsInterface(Class<?> cls, String name) {
        if (cls == null) return false;
        if (cls.getName().equals(name)) return true;
        for (Class<?> iface : cls.getInterfaces()) if (implementsInterface(iface, name)) return true;
        return implementsInterface(cls.getSuperclass(), name);
    }

    private static List<Object> elements(Object input) {
        List<Object> out = new ArrayList<>();
        if (input instanceof Iterable) for (Object obj : (Iterable<?>) input) out.add(obj);
        else if (input != null && input.getClass().isArray()) {
            for (int n = 0; n < Array.getLength(input); n++) out.add(Array.get(input, n));
        } else throw new Skipped("unsupported_ingredient");
        return out;
    }

    private static Object field(Object target, String name) {
        for (Class<?> cls = target.getClass(); cls != null; cls = cls.getSuperclass()) {
            try {
                Field f = cls.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(target);
            } catch (NoSuchFieldException ignored) {
                // Try the superclass: width/height and mirrored are Forge's private fields.
            } catch (ReflectiveOperationException e) {
                throw new IllegalStateException(e);
            }
        }
        return null;
    }

    private static int number(Object target, String name) {
        Object value = field(target, name);
        if (!(value instanceof Number)) throw new Skipped("unsupported_layout");
        return ((Number) value).intValue();
    }

    private static Map<String, Object> map(String name, String key, List<Object> recipes) {
        Map<String, Object> machine = new LinkedHashMap<>();
        machine.put("name", name);
        machine.put("key", key);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("machines", Collections.singletonList(machine));
        out.put("recipes", recipes);
        return out;
    }

    private static Map<String, Object> report() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("registered", 0);
        out.put("exported", 0);
        out.put("skipped", new TreeMap<String, Integer>());
        out.put("classes", new TreeMap<String, Object>());
        return out;
    }

    private static void increment(Map<String, Object> report, String key) {
        report.put(key, ((Number) report.get(key)).intValue() + 1);
    }

    private static void skip(Map<String, Object> report, String reason) {
        @SuppressWarnings("unchecked")
        Map<String, Integer> skipped = (Map<String, Integer>) report.get("skipped");
        skipped.put(reason, skipped.getOrDefault(reason, 0) + 1);
    }

    private static final class Skipped extends RuntimeException {
        Skipped(String reason) { super(reason); }
    }
}

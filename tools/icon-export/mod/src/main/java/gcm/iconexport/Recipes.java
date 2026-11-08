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
 * recipes.json.gz: native GT/crafting/assembly-line recipes and NEI displays/archives,
 * for the recipe viewer and the native-GT processing-pattern check.
 *
 * <p>{@code {"format": 3, "items": [key, ...], "item_names": [...], "fluids": [name, ...],
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
 * for. NEI-only displays are separate from production; disabled and outputless records stay in archives.
 *
 * <p>GregTech is read by reflection, so the mod still compiles against NEI alone and runs
 * without GregTech; field names that changed between GTNH versions have fallbacks.
 */
final class Recipes {

    private final Map<String, Integer> items = new LinkedHashMap<>();
    private final List<String> itemNames = new ArrayList<>();
    private final Map<String, Integer> fluids = new LinkedHashMap<>();
    private final List<String> fluidNames = new ArrayList<>();
    final RecipePresentation presentation = new RecipePresentation();
    int recipeCount;
    int mapsWithMachines;
    Map<String, Object> coverage;
    int craftingCount;
    int furnaceCount;

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
        Map<String, Object> mapAudit = new TreeMap<>();
        boolean catalystsLoaded = false;
        for (Map.Entry<?, ?> entry : all.entrySet()) {
            Object map = entry.getValue();
            String name = String.valueOf(entry.getKey());
            List<Object> recipes = new ArrayList<>();
            List<Object> displays = new ArrayList<>();
            List<Object> archive = new ArrayList<>();
            Map<String, Integer> counts = new TreeMap<>();
            mapAudit.put(name, counts);
            try {
                Collection<?> list = (Collection<?>) map.getClass().getMethod("getAllRecipes").invoke(map);
                for (Object recipe : list) {
                    counts.merge("registered", 1, Integer::sum);
                    if (flag(recipe, "mFakeRecipe", false)) counts.merge("fake", 1, Integer::sum);
                    else if (!flag(recipe, "mEnabled", true)) counts.merge("disabled", 1, Integer::sum);
                    Map<String, Object> out = recipe(recipe);
                    if (out == null && flag(recipe, "mFakeRecipe", false) && flag(recipe, "mEnabled", true)) {
                        out = recipe(recipe, true);
                        if (out != null) {
                            presentation(map, recipe, out);
                            out.put("type", "nei"); out.put("groups", java.util.Collections.emptyList());
                            out.put("approximate", java.util.Collections.singletonList("nei_display"));
                            displays.add(out); counts.merge("display_exported", 1, Integer::sum);
                        } else {
                            archive.add(recipe(recipe, true, true)); counts.merge("archived", 1, Integer::sum);
                        }
                        continue;
                    }
                    if (out != null) {
                        presentation(map, recipe, out);
                        recipes.add(out);
                        counts.merge("exported", 1, Integer::sum);
                    } else {
                        archive.add(recipe(recipe, true, true)); counts.merge("archived", 1, Integer::sum);
                    }
                }
            } catch (Throwable t) {
                throw new IllegalStateException("Could not read GregTech recipe map " + name, t);
            }
            if (recipes.isEmpty() && displays.isEmpty() && archive.isEmpty()) {
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
            out.put("preview", presentation.gt(map));
            out.put("recipes", recipes);
            if (!archive.isEmpty()) out.put("archive", archive);
            if (!recipes.isEmpty() || !archive.isEmpty()) maps.put(name, out);
            if (!displays.isEmpty()) {
                Map<String, Object> displayMap = new LinkedHashMap<>();
                displayMap.put("machines", machines); displayMap.put("recipes", displays);
                displayMap.put("preview", out.get("preview"));
                maps.put("nei.gt." + name, displayMap);
            }
            recipeCount += recipes.size();
        }
        IconExportMod.LOG.info("Exported {} GregTech recipes from {} maps, {} with machines.", recipeCount,
                maps.size(), mapsWithMachines);
        CraftingRecipes crafting = new CraftingRecipes(this::itemIndex, presentation::variants);
        coverage = crafting.collect(maps);
        coverage.put("assembly_line", new AssemblyRecipes(this).collect(maps));
        coverage.put("gt_maps", mapAudit);
        coverage.put("thaumcraft_infusion", new ThaumcraftRecipes(this).collect(maps));
        coverage.put("nei_handlers", new NeiRecipes(this).collect(maps));
        coverage.put("handlers", java.util.Arrays.asList("gregtech", "crafting", "furnace", "assembly_line", "nei"));
        craftingCount = crafting.craftingCount;
        furnaceCount = crafting.furnaceCount;
        Map<String, Object> file = new LinkedHashMap<>();
        file.put("format", 4);
        presentation.aspects();
        file.put("display_stacks", presentation.variants);
        file.put("ui_assets", presentation.assets);
        file.put("research_info", presentation.research);
        file.put("aspects", presentation.aspects);
        file.put("coverage", coverage);
        file.put("items", new ArrayList<>(items.keySet()));
        file.put("item_names", itemNames);
        file.put("fluids", new ArrayList<>(fluids.keySet()));
        file.put("fluid_names", fluidNames);
        file.put("maps", maps);
        return file;
    }

    /** NEI can substitute metadata-backed stacks without changing the production recipe. */
    @SuppressWarnings("unchecked")
    private void presentation(Object map, Object recipe, Map<String,Object> out) throws ReflectiveOperationException {
        Object frontend=map.getClass().getMethod("getFrontend").invoke(map);
        Object nei=frontend.getClass().getMethod("getNEIProperties").invoke(frontend);
        Map<String,Object> overrides=new LinkedHashMap<>();
        String[] sides={"i","o","f","g"};
        String[] getters={"itemInputsGetter","itemOutputsGetter","fluidInputsGetter","fluidOutputsGetter"};
        for(int k=0;k<sides.length;k++) {
            Object getter=field(nei,getters[k]);
            if(!(getter instanceof java.util.function.Function)) continue;
            Object values=((java.util.function.Function<Object,Object>)getter).apply(recipe);
            // Keep the native chance values when the getter merely returns the native array.
            String nativeField=new String[]{"mInputs","mOutputs","mFluidInputs","mFluidOutputs"}[k];
            if(values==field(recipe,nativeField)) continue;
            Object chances=field(recipe,k==1?"mOutputChances":"mFluidOutputChances");
            if(k==1 && chances==null)chances=field(recipe,"mChances");
            List<Object> display=k<2 ? stacks((ItemStack[])values,k==1 && chances instanceof int[]?(int[])chances:null)
                : fluidStacks((FluidStack[])values,k==3?chances:null);
            overrides.put(sides[k],display);
            List<Integer> indices=new ArrayList<>();
            if(values!=null) for(int n=0;n<java.lang.reflect.Array.getLength(values);n++)
                if(java.lang.reflect.Array.get(values,n)!=null) indices.add(n);
            ((Map<String,Object>)out.get("preview_indices")).put(sides[k],indices);
        }
        if(!overrides.isEmpty()) out.put("preview_stacks",overrides);
        Map<String,Object> overflow=presentation.overflow(map,recipe);
        if(overflow!=null)out.put("preview",overflow);
        // BECAssemblyFrontend prints the maximum tier; the per-slot array is tooltip data.
        Map<String,Object> requirements=(Map<String,Object>)out.get("requirements");
        if(requirements!=null && frontend.getClass().getName().equals("tectech.recipe.BECAssemblyFrontend")) {
            Object storage=recipe.getClass().getMethod("getMetadataStorage").invoke(recipe);
            if(storage==null)return;
            for(Object entry:(java.util.Set<?>)storage.getClass().getMethod("getEntries").invoke(storage)) {
                Map.Entry<?,?> e=(Map.Entry<?,?>)entry;
                if(!metadataName(e.getKey()).equals("nanite_tiers") || e.getValue()==null) continue;
                Object max=null;
                for(int n=0;n<java.lang.reflect.Array.getLength(e.getValue());n++) {
                    Object tier=java.lang.reflect.Array.get(e.getValue(),n);
                    if(tier!=null && (max==null || intField(tier,"tier")>intField(max,"tier"))) max=tier;
                }
                if(max!=null) requirements.put("max_nanite_tier",max.getClass().getMethod("describe").invoke(max));
            }
        }
    }

    private Map<String, Object> recipe(Object recipe) { return recipe(recipe, false); }

    private Map<String, Object> recipe(Object recipe, boolean display) { return recipe(recipe, display, false); }

    private Map<String, Object> recipe(Object recipe, boolean display, boolean archive) {
        if (!archive && ((!display && flag(recipe, "mFakeRecipe", false)) || !flag(recipe, "mEnabled", true))) {
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
        out.put("type", "gt");
        if (archive) {
            out.put("source_enabled", flag(recipe, "mEnabled", true));
            out.put("source_fake", flag(recipe, "mFakeRecipe", false));
        }
        putIfAny(out, "i", stacks(inputs, null));
        putIfAny(out, "f", fluidStacks(fluidInputs));
        putIfAny(out, "o", stacks(outputs, chances instanceof int[] ? (int[]) chances : null));
        putIfAny(out, "g", fluidStacks(fluidOutputs, field(recipe, "mFluidOutputChances")));
        if (!archive && !out.containsKey("o") && !out.containsKey("g")) {
            return null;
        }
        out.put("e", intField(recipe, "mEUt"));
        out.put("d", intField(recipe, "mDuration"));
        int special = intField(recipe, "mSpecialValue");
        if (special != 0) {
            out.put("s", special);
        }
        Map<String, Object> details = new LinkedHashMap<>();
        try {
            Object storage = recipe.getClass().getMethod("getMetadataStorage").invoke(recipe);
            if (storage != null) {
                Map<String, Object> metadata = new TreeMap<>();
                for (Object entry : (java.util.Set<?>) storage.getClass().getMethod("getEntries").invoke(storage)) {
                    Map.Entry<?, ?> e = (Map.Entry<?, ?>) entry;
                    metadata.put(metadataName(e.getKey()), RecipeSnapshot.of(e.getValue()));
                }
                if (!metadata.isEmpty()) details.put("metadata", metadata);
            }
            Object description = recipe.getClass().getMethod("getNeiDesc").invoke(recipe);
            if (description != null) details.put("description", RecipeSnapshot.of(description));
        } catch (NoSuchMethodException olderVersion) {
            // Older GT versions carry these in the special value instead.
        } catch (ReflectiveOperationException e) { throw new IllegalStateException(e); }
        for (String field : new String[] { "mSpecialItems", "mAltFluidInputs", "mInputChances", "mFluidInputChances", "mFluidOutputChances" }) {
            Object value = field(recipe, field);
            if (value != null) details.put(field, RecipeSnapshot.of(value));
        }
        boolean nbt = flag(recipe, "isNBTSensitive", false);
        if (inputs != null) for (ItemStack stack : inputs) nbt |= stack != null && stack.hasTagCompound();
        if (outputs != null) for (ItemStack stack : outputs) nbt |= stack != null && stack.hasTagCompound();
        if (nbt) {
            details.put("nbt_sensitive", true);
            details.put("nbt_inputs", RecipeSnapshot.of(inputs));
            details.put("nbt_outputs", RecipeSnapshot.of(outputs));
            out.put("approximate", java.util.Collections.singletonList("nbt"));
        }
        if (flag(recipe, "mNeedsEmptyOutput", false)) details.put("needs_empty_output", true);
        Object specialItems=field(recipe,"mSpecialItems");
        if(specialItems instanceof ItemStack) out.put("aux",stacks(new ItemStack[]{(ItemStack)specialItems},null));
        else if(specialItems instanceof ItemStack[]) out.put("aux",stacks((ItemStack[])specialItems,null));
        Map<String,Object> indices=new LinkedHashMap<>();
        for(Object[] side:new Object[][]{{"i",inputs},{"o",outputs},{"f",fluidInputs},{"g",fluidOutputs}}) {
            List<Integer> positions=new ArrayList<>();
            if(side[1]!=null) for(int n=0;n<java.lang.reflect.Array.getLength(side[1]);n++) if(java.lang.reflect.Array.get(side[1],n)!=null) positions.add(n);
            indices.put((String)side[0],positions);
        }
        out.put("preview_indices",indices);
        if (!details.isEmpty()) out.put("requirements", details);
        return out;
    }

    List<Object> stacks(ItemStack[] stacks, int[] chances) {
        List<Object> out = new ArrayList<>();
        if (stacks == null) {
            return out;
        }
        for (int i = 0; i < stacks.length; i++) {
            Integer index = itemIndex(stacks[i]);
            if (index == null) {
                continue;
            }
            List<Object> entry = new ArrayList<>(4);
            entry.add(index);
            entry.add(stacks[i].stackSize);
            if (chances != null && i < chances.length && chances[i] > 0 && chances[i] < 10000) {
                entry.add(chances[i]);
            } else entry.add(null);
            entry.add(presentation.variants(stacks[i]));
            out.add(entry);
        }
        return out;
    }

    List<Object> fluidStacks(FluidStack[] stacks) { return fluidStacks(stacks, null); }

    List<Object> fluidStacks(FluidStack[] stacks, Object rawChances) {
        int[] chances = rawChances instanceof int[] ? (int[]) rawChances : null;
        int at = -1;
        List<Object> out = new ArrayList<>();
        if (stacks == null) {
            return out;
        }
        for (FluidStack stack : stacks) {
            at++;
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
            if (chances != null && at < chances.length && chances[at] > 0 && chances[at] < 10000) entry.add(chances[at]);
            out.add(entry);
        }
        return out;
    }

    Integer itemIndex(ItemStack stack) {
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

    private static String metadataName(Object key) {
        for (Class<?> c=key.getClass();c!=null;c=c.getSuperclass()) {
            try { Field f=c.getDeclaredField("identifier");f.setAccessible(true);return String.valueOf(f.get(key)); }
            catch(NoSuchFieldException absent) { /* Try superclass. */ }
            catch(IllegalAccessException e) { throw new IllegalStateException(e); }
        }
        return String.valueOf(key);
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

    static Object field(Object target, String name) {
        try {
            return target.getClass().getField(name).get(target);
        } catch (Throwable t) {
            return null;
        }
    }

    static int intField(Object target, String name) {
        Object value = field(target, name);
        return value instanceof Integer ? (Integer) value : 0;
    }

    private static boolean flag(Object target, String name, boolean otherwise) {
        Object value = field(target, name);
        return value instanceof Boolean ? (Boolean) value : otherwise;
    }
}

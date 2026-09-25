package gcm.iconexport;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;

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
 * Items that exist in the game but that NEI's item list doesn't include.
 *
 * <p>GregTech in particular only lists part of what it registers: ores in most stone types,
 * centrifuged/impure ore products, many material tool heads and the like are missing from NEI
 * entirely (not merely hidden), yet players do mine and craft them. NESQL picked them up from
 * recipes, so this does the same: the ore dictionary, vanilla crafting and smelting, and
 * GregTech's recipe maps (by reflection, so there's no compile dependency on GregTech).
 */
final class RecipeItems {

    private RecipeItems() {}

    static List<ItemStack> collect() {
        List<ItemStack> stacks = new ArrayList<>();
        addOreDictionary(stacks);
        addCrafting(stacks);
        addSmelting(stacks);
        addGregTech(stacks);
        return stacks;
    }

    private static void addOreDictionary(List<ItemStack> stacks) {
        for (String name : OreDictionary.getOreNames()) {
            addAll(stacks, OreDictionary.getOres(name));
        }
    }

    private static void addCrafting(List<ItemStack> stacks) {
        for (Object entry : CraftingManager.getInstance().getRecipeList()) {
            try {
                IRecipe recipe = (IRecipe) entry;
                add(stacks, recipe.getRecipeOutput());
                if (recipe instanceof ShapedRecipes) {
                    addAll(stacks, ((ShapedRecipes) recipe).recipeItems);
                } else if (recipe instanceof ShapelessRecipes) {
                    addAll(stacks, ((ShapelessRecipes) recipe).recipeItems);
                } else if (recipe instanceof ShapedOreRecipe) {
                    addAll(stacks, ((ShapedOreRecipe) recipe).getInput());
                } else if (recipe instanceof ShapelessOreRecipe) {
                    addAll(stacks, ((ShapelessOreRecipe) recipe).getInput());
                }
            } catch (Throwable ignored) {}
        }
    }

    private static void addSmelting(List<ItemStack> stacks) {
        for (Object entry : FurnaceRecipes.smelting().getSmeltingList().entrySet()) {
            Map.Entry<?, ?> smelt = (Map.Entry<?, ?>) entry;
            add(stacks, smelt.getKey());
            add(stacks, smelt.getValue());
        }
    }

    private static void addGregTech(List<ItemStack> stacks) {
        Map<?, ?> maps;
        try {
            Field all = Class.forName("gregtech.api.recipe.RecipeMap").getField("ALL_RECIPE_MAPS");
            maps = (Map<?, ?>) all.get(null);
        } catch (Throwable t) {
            IconExportMod.LOG.info("No GregTech recipe maps found ({}); skipping them.", t.toString());
            return;
        }
        int recipes = 0;
        for (Object map : maps.values()) {
            try {
                Method getAllRecipes = map.getClass().getMethod("getAllRecipes");
                for (Object recipe : (Collection<?>) getAllRecipes.invoke(map)) {
                    addAll(stacks, fieldValue(recipe, "mInputs"));
                    addAll(stacks, fieldValue(recipe, "mOutputs"));
                    recipes++;
                }
            } catch (Throwable t) {
                IconExportMod.LOG.warn("Couldn't read GregTech recipe map {}: {}", map, t.toString());
            }
        }
        IconExportMod.LOG.info("Read {} GregTech recipes from {} maps.", recipes, maps.size());
    }

    private static Object fieldValue(Object target, String name) {
        try {
            return target.getClass().getField(name).get(target);
        } catch (Throwable t) {
            return null;
        }
    }

    /** Arrays and collections, recursively: ore-recipe inputs are lists of alternatives. */
    private static void addAll(List<ItemStack> stacks, Object value) {
        if (value instanceof Object[]) {
            for (Object o : (Object[]) value) {
                addAll(stacks, o);
            }
        } else if (value instanceof Iterable) {
            for (Object o : (Iterable<?>) value) {
                addAll(stacks, o);
            }
        } else {
            add(stacks, value);
        }
    }

    private static void add(List<ItemStack> stacks, Object value) {
        if (!(value instanceof ItemStack)) {
            return;
        }
        ItemStack stack = (ItemStack) value;
        // Wildcard damage means "any variant"; NEI's list already covers those variants.
        if (stack.getItem() == null || stack.getItemDamage() == OreDictionary.WILDCARD_VALUE) {
            return;
        }
        stacks.add(stack);
    }
}

package gcm.iconexport;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidStack;

/** Native assembly-line registries, including TecTech research, not the fake NEI maps. */
final class AssemblyRecipes {
    private final Recipes owner;

    AssemblyRecipes(Recipes owner) { this.owner = owner; }

    Map<String, Object> collect(Map<String, Object> maps) {
        Map<String, Object> report = new LinkedHashMap<>();
        List<Object> bodies = new ArrayList<>();
        Set<Object> seen = Collections.newSetFromMap(new IdentityHashMap<>());
        Map<String, Object> sources = new LinkedHashMap<>();
        int errors = 0;
        List<Object> nativeRecipes = new ArrayList<>(), overlays = new ArrayList<>();
        for (String[] source : new String[][] {
                {"gregtech.api.util.GTRecipe$RecipeAssemblyLine", "sAssemblylineRecipes"},
                {"gregtech.api.util.GTRecipe$GTRecipe_AssemblyLine", "sAssemblylineRecipes"},
                {"gregtech.api.util.GT_Recipe$GT_Recipe_AssemblyLine", "sAssemblylineRecipes"},
                {"tectech.recipe.TecTechRecipeMaps", "researchableALRecipeList"}}) {
            try {
                Collection<?> recipes = (Collection<?>) Class.forName(source[0]).getField(source[1]).get(null);
                sources.put(source[0] + "." + source[1], recipes.size());
                for (Object recipe : recipes) {
                    if (!seen.add(recipe)) continue;
                    if (Recipes.field(recipe, "mComputation") instanceof Number) overlays.add(recipe);
                    else nativeRecipes.add(recipe);
                }
            } catch (ClassNotFoundException | NoSuchFieldException absent) {
                // Different supported GT versions use different class names.
            } catch (ReflectiveOperationException e) { throw new IllegalStateException(e); }
        }
        Map<String, Object> baseBySignature = new LinkedHashMap<>();
        Map<String, Object> overlayBySignature = new LinkedHashMap<>();
        for (Object recipe : nativeRecipes) baseBySignature.put(signature(recipe), recipe);
        for (Object overlay : overlays) {
            if (!baseBySignature.containsKey(signature(overlay))) throw new IllegalStateException("Unmatched TecTech assembly-line research");
            if (overlayBySignature.put(signature(overlay), overlay) != null) throw new IllegalStateException("Ambiguous TecTech assembly-line research");
        }
        for (Object recipe : nativeRecipes) {
            try {
                Map<String, Object> result = body(recipe);
                Object overlay = overlayBySignature.get(signature(recipe));
                if (overlay != null) {
                    result.put("research", research(overlay));
                    result.put("research_snapshot", RecipeSnapshot.of(Recipes.field(overlay, "mResearchItem")));
                }
                bodies.add(result);
            } catch (RuntimeException e) { errors++; IconExportMod.LOG.error("Assembly-line export failed", e); }
        }
        report.put("registered", seen.size());
        report.put("exported", bodies.size());
        Map<String,Integer> skipped = new LinkedHashMap<>();
        if (!overlays.isEmpty()) skipped.put("merged_research_overlay", overlays.size());
        if (errors > 0) skipped.put("adapter_error", errors);
        report.put("skipped", skipped);
        report.put("sources", sources);
        if (!bodies.isEmpty()) {
            Map<String, Object> map = new LinkedHashMap<>();
            map.put("machines", Collections.singletonList(machine("Assembly Line", "gregtech:gt.blockmachines:1170")));
            map.put("recipes", bodies);
            map.put("preview",owner.presentation.assembly());
            Object rawMap=maps.get("nei.gt.gt.recipe.fakeAssemblylineProcess");
            if(rawMap instanceof Map) map.put("machines",((Map<?,?>)rawMap).get("machines"));
            maps.put("assembly_line", map);
        }
        return report;
    }

    static Map<String, String> machine(String name, String key) {
        Map<String, String> m = new LinkedHashMap<>(); m.put("name", name); m.put("key", key); return m;
    }

    private static String signature(Object recipe) {
        List<Object> parts = new ArrayList<>();
        for (String field : new String[] {"mInputs", "mOreDictAlt", "mFluidInputs", "mOutput", "mDuration"}) parts.add(RecipeSnapshot.of(Recipes.field(recipe, field)));
        return new com.google.gson.Gson().toJson(parts);
    }

    private Map<String,Object> research(Object recipe) {
        Map<String,Object> requirement = new LinkedHashMap<>();
        ItemStack item = (ItemStack) Recipes.field(recipe, "mResearchItem");
        requirement.put("item", owner.stacks(new ItemStack[]{item}, null).get(0));
        boolean station = Recipes.field(recipe, "mComputation") instanceof Number;
        requirement.put("method", station ? "research_station" : "scanner");
        // TTResearchStationALRecipe inherits mEUt for RESEARCH voltage; its
        // paired native recipe carries the assembly line's processing EU/t.
        requirement.put("eut", Recipes.intField(recipe, station ? "mEUt" : "mResearchVoltage"));
        requirement.put("ticks", Recipes.intField(recipe, "mResearchTime"));
        if (station) {
            requirement.put("computation", Recipes.field(recipe, "mComputation"));
            requirement.put("amperage", Recipes.field(recipe, "mAmperage"));
            requirement.put("computation_per_second", Recipes.field(recipe, "mComputationRequiredPerSec"));
        }
        return requirement;
    }

    private Map<String, Object> body(Object r) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("type", "assembly_line");
        ItemStack[] inputs = (ItemStack[]) Recipes.field(r, "mInputs");
        ItemStack[][] alternatives = (ItemStack[][]) Recipes.field(r, "mOreDictAlt");
        ItemStack output = (ItemStack) Recipes.field(r, "mOutput");
        if (inputs == null || inputs.length == 0 || output == null || output.getItem() == null) {
            throw new IllegalArgumentException("Missing assembly-line input/output");
        }
        List<Object> groups = new ArrayList<>();
        boolean nbt = output.hasTagCompound();
        for (int i = 0; i < inputs.length; i++) {
            ItemStack[] choices = alternatives != null && i < alternatives.length ? alternatives[i] : null;
            if (choices == null || choices.length == 0) choices = new ItemStack[] { inputs[i] };
            List<Object> alts = owner.stacks(choices, null);
            if (alts.size() != choices.length || alts.isEmpty()) throw new IllegalArgumentException("Unresolved assembly-line slot");
            for (ItemStack stack : choices) nbt |= stack.hasTagCompound();
            groups.add(Collections.singletonMap("alts", alts));
        }
        body.put("groups", groups);
        ItemStack stick=new ItemStack((net.minecraft.item.Item)net.minecraft.item.Item.itemRegistry.getObject("gregtech:gt.metaitem.01"),0,32708);
        body.put("aux",owner.stacks(new ItemStack[]{stick},null));
        body.put("o", owner.stacks(new ItemStack[] { output }, null));
        body.put("f", owner.fluidStacks((FluidStack[]) Recipes.field(r, "mFluidInputs")));
        body.put("e", Recipes.intField(r, "mEUt"));
        body.put("d", Recipes.intField(r, "mDuration"));
        ItemStack research = (ItemStack) Recipes.field(r, "mResearchItem");
        if (research != null) {
            body.put("research", research(r));
            nbt |= research.hasTagCompound();
        }
        Map<String,Object> snapshot=new LinkedHashMap<>();
        snapshot.put("inputs",RecipeSnapshot.of(inputs));snapshot.put("alternatives",RecipeSnapshot.of(alternatives));
        snapshot.put("output",RecipeSnapshot.of(output));snapshot.put("research",RecipeSnapshot.of(research));
        body.put("snapshot",snapshot);
        if (nbt) body.put("approximate", Arrays.asList("nbt"));
        return body;
    }
}

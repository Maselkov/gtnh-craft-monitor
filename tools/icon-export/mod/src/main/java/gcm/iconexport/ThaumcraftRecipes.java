package gcm.iconexport;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.minecraft.nbt.NBTBase;
import net.minecraft.nbt.NBTTagCompound;

import net.minecraft.item.ItemStack;

/** Native infusion fallback: NEI's dynamic runic/enchantment expansion needs a player. */
final class ThaumcraftRecipes {
    private final Recipes owner;
    ThaumcraftRecipes(Recipes owner) { this.owner=owner; }

    private List<Object> ingredients(Object recipe,Class<?> base,ItemStack input) throws ReflectiveOperationException {
        List<Object> out=new ArrayList<>();
        try {
            Class<?> api=Class.forName("net.glease.tc4tweak.api.infusionrecipe.InfusionRecipeExt");
            Class<?> ingredient=Class.forName("net.glease.tc4tweak.api.infusionrecipe.RecipeIngredient");
            Class<?> enhanced=Class.forName("net.glease.tc4tweak.api.infusionrecipe.EnhancedInfusionRecipe");
            Object adapter=api.getMethod("get").invoke(null);
            Object converted=api.getMethod("convert",base).invoke(adapter,recipe);
            List<Object> parts=new ArrayList<>();parts.add(enhanced.getMethod("getCentral").invoke(converted));
            parts.addAll((List<?>)enhanced.getMethod("getComponentsExt").invoke(converted));
            for(Object part:parts) {
                List<?> stacks=(List<?>)ingredient.getMethod("getRepresentativeStacks").invoke(part);
                ItemStack[] choices=stacks.toArray(new ItemStack[0]);
                if(choices.length==0)throw new IllegalStateException("Unresolved enhanced infusion ingredient");
                out.add(Collections.singletonMap("alts",owner.stacks(choices,null)));
            }
        } catch(ClassNotFoundException legacy) {
            out.add(Collections.singletonMap("alts",owner.stacks(new ItemStack[]{input},null)));
            for(ItemStack stack:(ItemStack[])base.getMethod("getComponents").invoke(recipe))
                out.add(Collections.singletonMap("alts",owner.stacks(new ItemStack[]{stack},null)));
        }
        return out;
    }

    Map<String,Object> collect(Map<String,Object> maps) {
        Map<String,Object> report=new LinkedHashMap<>();
        List<?> registry;
        Class<?> infusion;
        try {
            // At the menu there has been no login to initialize the networked mode.
            // Capture the pack's configured rule, as resetServer does on world start.
            try {
                Class<?> config=Class.forName("net.glease.tc4tweak.network.NetworkedConfiguration");
                if(config.getMethod("getInfusionOreDictMode").invoke(null)==null)config.getMethod("resetServer").invoke(null);
            } catch(ClassNotFoundException absentTweaks) { /* Legacy Thaumcraft. */ }
            registry=(List<?>)Class.forName("thaumcraft.api.ThaumcraftApi").getMethod("getCraftingRecipes").invoke(null);
            infusion=Class.forName("thaumcraft.api.crafting.InfusionRecipe");
        } catch(ClassNotFoundException absent) { report.put("status","absent"); return report; }
        catch(ReflectiveOperationException e) { throw new IllegalStateException(e); }
        List<Object> recipes=new ArrayList<>(), archive=new ArrayList<>();
        int registered=0;
        for(Object r:registry) {
            archive.add(RecipeSnapshot.of(r));
            if(!infusion.isInstance(r)) continue;
            registered++;
            try {
                Object output=infusion.getMethod("getRecipeOutput").invoke(r);
                ItemStack input=(ItemStack)infusion.getMethod("getRecipeInput").invoke(r);
                if(input==null)continue;
                boolean transformed=false;
                if(output instanceof Object[]) {
                    Object[] operation=(Object[])output;
                    if(operation.length!=2 || !(operation[0] instanceof String) || !(operation[1] instanceof NBTBase))continue;
                    ItemStack result=input.copy();
                    if(!result.hasTagCompound())result.setTagCompound(new NBTTagCompound());
                    result.getTagCompound().setTag((String)operation[0],((NBTBase)operation[1]).copy());
                    output=result;transformed=true;
                }
                if(!(output instanceof ItemStack))continue;
                List<Object> groups=ingredients(r,infusion,input);
                if(groups.isEmpty())throw new IllegalStateException("Empty infusion ingredients");
                Map<String,Object> body=new LinkedHashMap<>();body.put("type","nei");body.put("groups",groups);
                body.put("o",owner.stacks(new ItemStack[]{(ItemStack)output},null));
                Map<String,Object> requirements=new LinkedHashMap<>();
                requirements.put("research",infusion.getMethod("getResearch").invoke(r));
                requirements.put("instability",infusion.getMethod("getInstability").invoke(r));
                Object aspects=infusion.getMethod("getAspects").invoke(r);
                Map<String,Object> essentia=new LinkedHashMap<>();
                for(Object aspect:(Object[])aspects.getClass().getMethod("getAspects").invoke(aspects)) {
                    if (aspect == null) { essentia.put("Unresolved aspect", true); continue; }
                    String tag=String.valueOf(aspect.getClass().getMethod("getTag").invoke(aspect));
                    essentia.put(tag,aspects.getClass().getMethod("getAmount",Class.forName("thaumcraft.api.aspects.Aspect")).invoke(aspects,aspect));
                }
                requirements.put("essentia",essentia);body.put("requirements",requirements);
                String research=String.valueOf(requirements.get("research"));owner.presentation.research(research);
                body.put("preview",owner.presentation.infusion(groups.size()-1));
                if(transformed)body.put("output_transformation",true);
                body.put("snapshot",RecipeSnapshot.of(r));body.put("approximate",Collections.singletonList("nei_display"));
                recipes.add(body);
            } catch(ReflectiveOperationException e) { throw new IllegalStateException("Infusion adapter failed",e); }
        }
        Map<String,Object> map=new LinkedHashMap<>();
        map.put("machines",Collections.singletonList(AssemblyRecipes.machine("Arcane Infusion","Thaumcraft:blockStoneDevice:2")));
        map.put("recipes",recipes);map.put("archive",archive);maps.put("nei.thaumcraft.infusion",map);
        report.put("enhanced_ingredients",true);
        report.put("registered",registered);report.put("exported",recipes.size());
        report.put("archived_dynamic",registered-recipes.size());report.put("archived_registry",archive.size());
        return report;
    }
}

package gcm.iconexport;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidStack;

import codechicken.nei.PositionedStack;
import codechicken.nei.recipe.GuiCraftingRecipe;
import codechicken.nei.recipe.GuiRecipeTab;
import codechicken.nei.recipe.HandlerInfo;
import codechicken.nei.recipe.GuiUsageRecipe;
import codechicken.nei.recipe.IRecipeHandler;
import codechicken.nei.recipe.IUsageHandler;
import codechicken.nei.recipe.ICraftingHandler;
import codechicken.nei.recipe.RecipeCatalysts;
import codechicken.nei.recipe.StackInfo;
import codechicken.nei.recipe.TemplateRecipeHandler;

/** NEI display recipes for handlers not covered by a native adapter. Never pattern-check input. */
final class NeiRecipes {
    private final Recipes owner;
    final Map<String,Object> audit = new TreeMap<>();

    NeiRecipes(Recipes owner) { this.owner=owner; }

    Map<String,Object> collect(Map<String,Object> maps) {
        List<IRecipeHandler> handlers=new ArrayList<>(GuiCraftingRecipe.craftinghandlers);
        handlers.addAll(GuiCraftingRecipe.serialCraftingHandlers);
        handlers.addAll(GuiUsageRecipe.usagehandlers);
        handlers.addAll(GuiUsageRecipe.serialUsageHandlers);
        for (IRecipeHandler original:handlers) {
            String overlay=original.getOverlayIdentifier();
            String id=original.getClass().getName()+"::"+(overlay==null?"":overlay);
            if (audit.containsKey(id)) continue;
            Map<String,Object> report=new LinkedHashMap<>();
            report.put("class",original.getClass().getName());
            report.put("overlay",overlay);
            audit.put(id,report);
            String cls=original.getClass().getName();
            if (cls.equals("gregtech.nei.GTNEIDefaultHandler") || cls.startsWith("bartworks.neiHandler.")
                    || cls.equals("com.gtnewhorizons.aspectrecipeindex.nei.InfusionRecipeHandler")
                    || cls.equals("codechicken.nei.recipe.ShapedRecipeHandler")
                    || cls.equals("codechicken.nei.recipe.ShapelessRecipeHandler")
                    || cls.equals("codechicken.nei.recipe.FurnaceRecipeHandler")
                    || cls.endsWith(".NEIAEShapedRecipeHandler") || cls.endsWith(".NEIAEShapelessRecipeHandler")
                    || cls.equals("ic2.neiIntegration.core.recipehandler.AdvRecipeHandler")
                    || cls.equals("ic2.neiIntegration.core.recipehandler.AdvShapelessRecipeHandler")) {
                report.put("status","native");
                continue;
            }
            try {
                report.put("name",original.getRecipeName());
                IRecipeHandler h=original instanceof TemplateRecipeHandler ? ((TemplateRecipeHandler)original).getAllRecipeHandler()
                        : original instanceof ICraftingHandler ? ((ICraftingHandler)original).getRecipeHandler("all")
                        : ((IUsageHandler)original).getUsageHandler("all");
                if(h==null){report.put("status","not_enumerable");report.put("registered",0);report.put("exported",0);report.put("skipped",Collections.emptyMap());continue;}
                int count=h.numRecipes();
                report.put("registered",count);
                List<Object> recipes=new ArrayList<>();
                List<Object> archive=new ArrayList<>();
                List<Object> oreGen=new ArrayList<>();
                Map<String,Integer> skipped=new TreeMap<>();
                boolean ores=cls.equals(VEIN_STATS) || cls.equals(SMALL_ORE_STATS);
                for(int i=0;i<count;i++) {
                    if(ores) {
                        try { oreGen.add(oreGen(h,i)); }
                        catch(Throwable e) {
                            try { archive.add(raw(h,i)); skipped.merge("archived_unreadable_display",1,Integer::sum); }
                            catch(Throwable unavailable) { skipped.merge("unreadable_display",1,Integer::sum); }
                        }
                        continue;
                    }
                    try {
                        Map<String,Object> body=body(h,i);
                        if (body!=null) recipes.add(body);
                        else { archive.add(raw(h,i)); skipped.merge("archived_without_primary_output",1,Integer::sum); }
                    } catch(Throwable e) {
                        try { archive.add(raw(h,i)); skipped.merge("archived_unreadable_display",1,Integer::sum); }
                        catch(Throwable unavailable) { skipped.merge("unreadable_display",1,Integer::sum); }
                    }
                }
                report.put("exported",recipes.size()+oreGen.size()); report.put("skipped",skipped);
                report.put("status",count==0 ? "not_enumerable" : recipes.isEmpty() && oreGen.isEmpty() ? "informational_or_unsupported" : "display");
                if (!recipes.isEmpty() || !archive.isEmpty() || !oreGen.isEmpty()) {
                    List<Object> machines=new ArrayList<>();
                    machines.add(AssemblyRecipes.machine(original.getRecipeName(),null));
                    for(PositionedStack catalyst:RecipeCatalysts.getRecipeCatalysts(overlay==null?original.getHandlerId():overlay)) {
                        if(catalyst.item!=null) machines.add(AssemblyRecipes.machine(catalyst.item.getDisplayName(),Recipes.keyOf(catalyst.item)));
                    }
                    Map<String,Object> map=new LinkedHashMap<>();map.put("machines",machines);map.put("recipes",recipes);map.put("archive",archive);
                    if(!oreGen.isEmpty()) map.put("ore_gen",oreGen);
                    String icon=tabIcon(original);
                    if(icon!=null) map.put("icon",icon);
                    maps.put("nei."+id,map);
                }
            } catch(Throwable e) { report.put("status","unavailable"); report.put("error",e.toString()); }
        }
        return audit;
    }

    private Map<String,Object> body(IRecipeHandler h,int index) throws Exception {
        PositionedStack result=h.getResultStack(index);
        if(result==null || result.items==null || result.items.length==0) return null;
        Map<String,Object> body=new LinkedHashMap<>();body.put("type","nei");
        List<Object> groups=new ArrayList<>();
        for(PositionedStack input:h.getIngredientStacks(index)) {
            groups.add(group(input));
        }
        body.put("groups",groups);
        Map<String,Object> preview=RecipePresentation.scene("positioned",166,80);
        int slotIndex=0,maxY=0,maxX=166;
        for(PositionedStack input:h.getIngredientStacks(index)) {
            owner.presentation.slot(preview,"groups",slotIndex++,Math.max(0,input.relx-1),Math.max(0,input.rely-1));
            maxY=Math.max(maxY,input.rely+20);maxX=Math.max(maxX,input.relx+20);
        }
        // NEI Addons' produce pages draw every product, each with its chance
        // as a label, but give NEI only the first as the result.
        List<PositionedStack> products=labeledProducts(h,index);
        List<Object> productOutputs=new ArrayList<>();
        if(products!=null) {
            for(PositionedStack product:products) {
                if(product.items==null || product.items.length==0 || StackInfo.getFluid(product.items[0])!=null) continue;
                List<Object> one=owner.stacks(new ItemStack[]{product.items[0]},new int[]{chance(product)});
                if(one.isEmpty()) continue;
                owner.presentation.slot(preview,"o",productOutputs.size(),Math.max(0,product.relx-1),Math.max(0,product.rely-1));
                productOutputs.add(one.get(0));
                maxX=Math.max(maxX,product.relx+20);maxY=Math.max(maxY,product.rely+20);
            }
        }
        if(productOutputs.isEmpty()) owner.presentation.slot(preview,"o",0,Math.max(0,result.relx-1),Math.max(0,result.rely-1));
        List<Object> extras=new ArrayList<>();
        for(PositionedStack extra:h.getOtherStacks(index)) {
            if(extra==null || extra.items==null || extra.items.length==0)continue;
            // Other stacks can be catalysts, gauges or secondary results. They
            // are display-only and must not become consumed recipe inputs.
            try {
                Map<String,Object> value=group(extra);
                owner.presentation.slot(preview,"extras",extras.size(),Math.max(0,extra.relx-1),Math.max(0,extra.rely-1));
                extras.add(value);maxX=Math.max(maxX,extra.relx+20);maxY=Math.max(maxY,extra.rely+20);
            } catch(IllegalArgumentException unresolvedDisplay) { /* Preserved in the raw snapshot. */ }
        }
        if(!extras.isEmpty())body.put("extras",extras);
        preview.put("width",Math.max(maxX,result.relx+20));
        preview.put("height",Math.max(80,Math.max(maxY,result.rely+20)));
        preview.put("graphics","slots_only");body.put("preview",preview);
        ItemStack output=result.items[0];
        FluidStack fluid=productOutputs.isEmpty() ? StackInfo.getFluid(output) : null;
        if(!productOutputs.isEmpty()) body.put("o",productOutputs);
        else if(fluid!=null) {
            body.put("g",owner.fluidStacks(new FluidStack[]{fluid}));
            @SuppressWarnings("unchecked") List<Map<String,Object>> slots=(List<Map<String,Object>>)preview.get("slots");
            for(Map<String,Object> slot:slots)if("o".equals(slot.get("role")))slot.put("role","g");
        }
        else body.put("o",owner.stacks(new ItemStack[]{output},null));
        if(((List<?>)body.getOrDefault("o",Collections.emptyList())).isEmpty()
                && ((List<?>)body.getOrDefault("g",Collections.emptyList())).isEmpty()) return null;
        Map<String,Object> snapshot=raw(h,index);
        body.put("snapshot",snapshot);
        @SuppressWarnings("unchecked")
        Map<String,Object> details=(Map<String,Object>)snapshot.getOrDefault("details",Collections.emptyMap());
        Map<String,Object> costs=new TreeMap<>();
        for(String key:new String[]{"energy","time","cookTime","mana","manaUsage","lp","lp_amount","tier","consumption","drain","instability","aspects","vis","prereqs","catalyst","requirements","power","processingTime","temperature","chance","outputChance","experience"}) {
            if(details.get(key)!=null) costs.put(key,details.get(key));
        }
        if(!costs.isEmpty()) body.put("requirements",costs);
        body.put("approximate",Collections.singletonList("nei_display"));
        return body;
    }

    // gtneioreplugin's pages: where ores generate. NEI gives them no result
    // and draws their numbers rather than stacking them, so the generic path
    // would only archive them; their cached recipes have it all.
    static final String VEIN_STATS="gtneioreplugin.plugin.gregtech5.PluginGT5VeinStat";
    static final String SMALL_ORE_STATS="gtneioreplugin.plugin.gregtech5.PluginGT5SmallOreStat";
    private static final String[][] VEIN_LAYERS={{"primary","positionedStackPrimary"},{"secondary","positionedStackSecondary"},
            {"between","positionedStackBetween"},{"sporadic","positionedStackSporadic"}};

    /** A vein's or small ore's record for the bundle's ore_gen (README's "Bundle format"). */
    private Map<String,Object> oreGen(IRecipeHandler h,int index) throws ReflectiveOperationException {
        Field f=TemplateRecipeHandler.class.getDeclaredField("arecipes");f.setAccessible(true);
        Object cached=((List<?>)f.get(h)).get(index);
        Map<String,Object> body=new LinkedHashMap<>();
        Object title=field(cached,"title");
        List<String> lines=new ArrayList<>();
        if(title instanceof List) for(Object line:(List<?>)title) if(line!=null) lines.add(line.toString());
        body.put("name",String.join(" ",lines).trim());
        Object vein=fieldOrNull(cached,"oreVein");
        if(vein!=null) {
            body.put("type","ore_vein");
            List<Object> layers=new ArrayList<>();
            for(String[] layer:VEIN_LAYERS) {
                List<Object> alts=alts((PositionedStack)field(cached,layer[1]),false);
                if(alts.isEmpty()) continue;
                Map<String,Object> value=new LinkedHashMap<>();value.put("role",layer[0]);value.put("alts",alts);layers.add(value);
            }
            if(layers.isEmpty()) throw new IllegalStateException("vein without ores");
            body.put("layers",layers);
            body.put("height",String.valueOf(field(vein,"worldGenHeightRange")));
            for(String[] key:new String[][]{{"weight","randomWeight"},{"size","size"},{"density","density"}})
                body.put(key[0],((Number)field(vein,key[1])).intValue());
            Object heights=fieldOrNull(vein,"dimWorldGenHeightRange");
            if(heights instanceof Map && !((Map<?,?>)heights).isEmpty()) {
                Map<String,Object> byDim=new TreeMap<>();
                for(Map.Entry<?,?> e:((Map<?,?>)heights).entrySet()) byDim.put(String.valueOf(e.getKey()),String.valueOf(e.getValue()));
                body.put("dim_heights",byDim);
            }
        } else {
            Object small=field(cached,"oreSmall");
            body.put("type","small_ore");
            List<Object> ores=alts((PositionedStack)field(cached,"positionedStackOreSmall"),false);
            if(ores.isEmpty()) throw new IllegalStateException("small ore without ores");
            body.put("ores",ores);
            Object dust=fieldOrNull(cached,"positionedStackMaterialDust");
            if(dust instanceof PositionedStack) body.put("dust",alts((PositionedStack)dust,false));
            List<Object> drops=new ArrayList<>();
            Object list=fieldOrNull(cached,"positionedDropStackList");
            if(list instanceof List) for(Object drop:(List<?>)list) if(drop instanceof PositionedStack) drops.addAll(alts((PositionedStack)drop,true));
            body.put("drops",drops);
            body.put("height",String.valueOf(field(small,"worldGenHeightRange")));
            body.put("amount",((Number)field(small,"amountPerChunk")).intValue());
        }
        List<Object> dims=new ArrayList<>();
        Object displays=fieldOrNull(cached,"dimensionDisplayItems");
        if(displays instanceof List) for(Object dim:(List<?>)displays) if(dim instanceof PositionedStack) dims.addAll(alts((PositionedStack)dim,false));
        body.put("dims",dims);
        return body;
    }

    /** A stack's item alternatives, as the bundle's stacks; with its labelled chance (drops) when asked. */
    private List<Object> alts(PositionedStack stack,boolean withChance) {
        List<Object> out=new ArrayList<>();
        if(stack==null || stack.items==null) return out;
        int chance=withChance ? chance(stack) : 0;
        for(ItemStack item:stack.items) {
            if(item==null || item.getItem()==null) continue;
            List<Object> one=owner.stacks(new ItemStack[]{item},chance>0 && chance<10000 ? new int[]{chance} : null);
            if(one.size()==1 && !out.contains(one.get(0))) out.add(one.get(0));
        }
        return out;
    }

    private static Object field(Object owner,String name) throws ReflectiveOperationException {
        for(Class<?> c=owner.getClass();c!=null;c=c.getSuperclass()) {
            try { Field f=c.getDeclaredField(name);f.setAccessible(true);return f.get(owner); }
            catch(NoSuchFieldException absent) { /* Try the superclass. */ }
        }
        throw new NoSuchFieldException(name);
    }

    private static Object fieldOrNull(Object owner,String name) {
        try { return field(owner,name); } catch(ReflectiveOperationException absent) { return null; }
    }

    private Map<String,Object> raw(IRecipeHandler h,int index) throws Exception {
        Map<String,Object> snapshot=new LinkedHashMap<>();
        List<Object> inputs=new ArrayList<>();
        for(PositionedStack input:h.getIngredientStacks(index)) inputs.add(snapshot(input));
        snapshot.put("ingredients",inputs);
        PositionedStack result=h.getResultStack(index);
        snapshot.put("result",result==null ? null : snapshot(result));
        List<Object> other=new ArrayList<>();
        for(PositionedStack stack:h.getOtherStacks(index)) other.add(snapshot(stack));
        snapshot.put("other",other);
        if(h instanceof TemplateRecipeHandler) {
            Field f=TemplateRecipeHandler.class.getDeclaredField("arecipes");f.setAccessible(true);
            Object cached=((List<?>)f.get(h)).get(index);
            Map<String,Object> details=new TreeMap<>();
            // Read the cached recipe's fields explicitly: the snapshot walker
            // intentionally refuses handlers, including their inner classes.
            for(Class<?> c=cached.getClass();c!=null && c!=TemplateRecipeHandler.CachedRecipe.class;c=c.getSuperclass()) {
                for(Field cf:c.getDeclaredFields()) {
                    if(java.lang.reflect.Modifier.isStatic(cf.getModifiers()) || cf.isSynthetic()) continue;
                    cf.setAccessible(true);details.put(details.containsKey(cf.getName()) ? c.getName()+"#"+cf.getName() : cf.getName(),RecipeSnapshot.of(cf.get(cached)));
                }
            }
            snapshot.put("details",details);
        }
        return snapshot;
    }

    /** The item NEI draws on the handler's tab (handlers.csv, or a mod's IMC), as an item key; null for none or an image. */
    private static String tabIcon(IRecipeHandler handler) {
        try {
            HandlerInfo info=GuiRecipeTab.getHandlerInfo(handler);
            ItemStack stack=info==null ? null : info.getItemStack();
            return stack==null || stack.getItem()==null ? null : Recipes.keyOf(stack);
        } catch(RuntimeException unavailable) { return null; }
    }

    /** A cached recipe's "products" list (NEI Addons' bee and tree produce), or null. */
    private static List<PositionedStack> labeledProducts(IRecipeHandler h,int index) {
        if(!(h instanceof TemplateRecipeHandler)) return null;
        try {
            Field f=TemplateRecipeHandler.class.getDeclaredField("arecipes");f.setAccessible(true);
            Object cached=((List<?>)f.get(h)).get(index);
            for(Class<?> c=cached.getClass();c!=null && c!=TemplateRecipeHandler.CachedRecipe.class;c=c.getSuperclass()) {
                Field products;
                try { products=c.getDeclaredField("products"); } catch(NoSuchFieldException absent) { continue; }
                products.setAccessible(true);
                Object value=products.get(cached);
                if(!(value instanceof List)) return null;
                List<PositionedStack> out=new ArrayList<>();
                for(Object o:(List<?>)value) if(o instanceof PositionedStack) out.add((PositionedStack)o);
                return out;
            }
        } catch(ReflectiveOperationException | RuntimeException unreadable) { /* The single result stays. */ }
        return null;
    }

    /** A labelled stack's chance ("12.5%", or "12,5%" in some locales) out of 10,000; 0 when it has none. */
    private static int chance(PositionedStack stack) {
        for(Class<?> c=stack.getClass();c!=null && c!=PositionedStack.class;c=c.getSuperclass()) {
            try {
                Field f=c.getDeclaredField("label");f.setAccessible(true);
                Object label=f.get(stack);
                if(label==null) return 0;
                String text=label.toString().replaceAll("\u00a7.","").replace(',','.').replaceAll("[^0-9.]","");
                if(text.isEmpty()) return 0;
                return (int)Math.round(Math.min(100,Double.parseDouble(text))*100);
            } catch(NoSuchFieldException absent) { /* Try the superclass. */ }
            catch(ReflectiveOperationException | NumberFormatException unreadable) { return 0; }
        }
        return 0;
    }

    private Object snapshot(PositionedStack stack) {
        Map<String,Object> out=new LinkedHashMap<>();
        out.put("x",stack.relx);out.put("y",stack.rely);out.put("stacks",RecipeSnapshot.of(stack.items));
        return out;
    }

    private Map<String,Object> group(PositionedStack stack) {
        if(stack.items==null || stack.items.length==0) throw new IllegalArgumentException("Empty NEI ingredient");
        List<Object> alts=new ArrayList<>();
        Boolean fluids=null;
        for(ItemStack item:stack.items) {
            FluidStack fluid=StackInfo.getFluid(item);
            boolean isFluid=fluid!=null;
            if(fluids!=null && fluids!=isFluid) throw new IllegalArgumentException("Mixed item/fluid alternatives");
            fluids=isFluid;
            List<Object> resolved=isFluid ? owner.fluidStacks(new FluidStack[]{fluid}) : owner.stacks(new ItemStack[]{item},null);
            if(resolved.size()!=1) throw new IllegalArgumentException("Unresolved NEI ingredient");
            alts.add(resolved.get(0));
        }
        Map<String,Object> group=new LinkedHashMap<>();group.put("kind",Boolean.TRUE.equals(fluids)?"fluid":"item");group.put("alts",alts);return group;
    }
}

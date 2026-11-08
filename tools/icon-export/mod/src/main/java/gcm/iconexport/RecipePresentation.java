package gcm.iconexport;

import java.awt.image.BufferedImage;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.util.*;
import java.util.function.IntFunction;
import javax.imageio.ImageIO;
import net.minecraft.client.Minecraft;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.oredict.OreDictionary;
import codechicken.nei.PositionedStack;

/** Display identity is deliberately independent of the recipe graph's wildcard/NBT rules. */
final class RecipePresentation {
    final Map<String,Object> variants = new LinkedHashMap<>();
    final Map<String,Object> assets = new LinkedHashMap<>();
    final Map<String,Object> research = new LinkedHashMap<>();
    final Map<String,Object> aspects = new LinkedHashMap<>();
    final Map<String,ExportJob> jobs = new LinkedHashMap<>();
    private final Map<String,BufferedImage> images = new LinkedHashMap<>();
    private final Map<String,List<String>> refs = new HashMap<>();
    private final Map<Item,List<ItemStack>> neiDisplays = new IdentityHashMap<>();

    void registerDisplays(List<ItemStack> stacks) {
        for(ItemStack stack:stacks) {
            if(stack!=null&&stack.getItem()!=null)
                neiDisplays.computeIfAbsent(stack.getItem(),item->new ArrayList<>()).add(stack);
        }
    }

    List<String> variants(ItemStack stack) {
        if (stack == null || stack.getItem() == null) return Collections.emptyList();
        String identity = Recipes.keyOf(stack) + "|" + (stack.hasTagCompound() ? stack.getTagCompound().toString() : "");
        if (refs.containsKey(identity)) return refs.get(identity);
        List<String> out = new ArrayList<>();
        ItemStack[] choices = new ItemStack[]{stack};
        if (net.minecraft.init.Items.feather.getDamage(stack) == OreDictionary.WILDCARD_VALUE) {
            // ItemList.itemMap is empty at the main menu. Use the same NEI
            // overrides/subtypes/variants collected for icon rendering instead
            // of PositionedStack's bare-damage-zero fallback.
            List<ItemStack> samples=neiDisplays.get(stack.getItem());
            if(samples!=null&&!samples.isEmpty())choices=samples.toArray(new ItemStack[0]);
            else {
                PositionedStack positioned = new PositionedStack(stack,0,0);
                choices = positioned.items;
            }
        }
        for (ItemStack choice : choices) {
            if (out.size() == 16) break; // A display sample; semantic alternatives stay complete.
            if (net.minecraft.init.Items.feather.getDamage(stack) == OreDictionary.WILDCARD_VALUE && stack.hasTagCompound()) {
                choice=choice.copy();choice.setTagCompound((net.minecraft.nbt.NBTTagCompound)stack.getTagCompound().copy());
            }
            ExportJob job = ExportJob.forItem(choice);
            if (job == null) continue;
            String id = UUID.nameUUIDFromBytes(job.imagePath.getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString();
            if (!variants.containsKey(id)) {
                Map<String,Object> value = new LinkedHashMap<>();
                value.put("key", Recipes.keyOf(choice)); value.put("name",job.label==null?Recipes.keyOf(choice):job.label);
                value.put("nbt",choice.hasTagCompound()?choice.getTagCompound().toString():null);
                value.put("icon",job.imagePath.replaceAll("\\.png$", ".webp"));
                variants.put(id,value);jobs.put(id,job);
            }
            if (!out.contains(id)) out.add(id);
        }
        refs.put(identity,out);
        return out;
    }

    String asset(String resource) {
        if (assets.containsKey(resource)) return resource;
        try (InputStream in=Minecraft.getMinecraft().getResourceManager().getResource(new ResourceLocation(resource)).getInputStream()) {
            BufferedImage image=ImageIO.read(in);
            if (image == null) return null;
            String path="ui/"+UUID.nameUUIDFromBytes(resource.getBytes(java.nio.charset.StandardCharsets.UTF_8))+".png";
            Map<String,Object> value=new LinkedHashMap<>();value.put("path",path.replaceAll("\\.png$", ".webp"));
            value.put("width",image.getWidth());value.put("height",image.getHeight());
            assets.put(resource,value);images.put(path,image);
            return resource;
        } catch(Exception unavailable) {
            IconExportMod.LOG.warn("Recipe preview texture unavailable: {}",resource);
            return null;
        }
    }

    void writeImages(ExportOutput output) throws java.io.IOException {
        for (Map.Entry<String,BufferedImage> e:images.entrySet()) output.writeImage(e.getKey(),e.getValue());
    }

    Map<String,String> imageLookup(List<ExportJob> rendered) {
        Set<String> paths=new HashSet<>();
        for(ExportJob job:rendered) if(job.rendered) paths.add(job.imagePath);
        Map<String,String> lookup=new LinkedHashMap<>();
        for(Map.Entry<String,ExportJob> e:jobs.entrySet()) {
            if(paths.contains(e.getValue().imagePath)) lookup.put(e.getKey(),e.getValue().imagePath);
            else { @SuppressWarnings("unchecked") Map<String,Object> v=(Map<String,Object>)variants.get(e.getKey());v.put("icon",null);v.put("render_failed",true); }
        }
        return lookup;
    }

    Map<String,String> assetLookup() {
        Map<String,String> out=new LinkedHashMap<>();
        for(String resource:assets.keySet()) {
            @SuppressWarnings("unchecked") Map<String,Object> v=(Map<String,Object>)assets.get(resource);
            out.put(resource,String.valueOf(v.get("path")).replaceAll("\\.webp$", ".png"));
        }
        return out;
    }

    static Map<String,Object> scene(String family,int width,int height) {
        Map<String,Object> out=new LinkedHashMap<>();out.put("family",family);out.put("width",width);out.put("height",height);
        out.put("slots",new ArrayList<>());out.put("layers",new ArrayList<>());return out;
    }

    @SuppressWarnings("unchecked")
    void slot(Map<String,Object> scene,String role,int index,int x,int y) {
        Map<String,Object> s=new LinkedHashMap<>();s.put("role",role);s.put("index",index);
        s.put("x",x);s.put("y",y);s.put("w",18);s.put("h",18);
        s.put("asset",asset("modularui:textures/gui/slot/"+(role.equals("f")||role.equals("g")?"fluid":"item")+".png"));
        ((List<Object>)scene.get("slots")).add(s);
    }

    @SuppressWarnings("unchecked")
    void layer(Map<String,Object> scene,String resource,int x,int y,int w,int h,double... uv) {
        String id=asset(resource);if(id==null)return;
        Map<String,Object> l=new LinkedHashMap<>();l.put("asset",id);l.put("x",x);l.put("y",y);l.put("w",w);l.put("h",h);
        l.put("uv",uv.length==4?uv:new double[]{0,0,1,1});
        ((List<Object>)scene.get("layers")).add(l);
    }

    Map<String,Object> assembly() {
        Map<String,Object> out=scene("assembly_line",170,84);
        layer(out,"gregtech:textures/gui/background/nei_single_recipe.png",0,0,170,84);
        for(int i=0;i<16;i++)slot(out,"groups",i,16+18*(i%4),8+18*(i/4));
        for(int i=0;i<4;i++)slot(out,"f",i,106,8+18*i);
        slot(out,"o",0,142,8);slot(out,"aux",0,142,44);
        layer(out,"gregtech:textures/gui/progressbar/assemblyline_1.png",88,8,17,72,0,0,1,.5);
        layer(out,"gregtech:textures/gui/progressbar/assemblyline_2.png",124,8,18,72,0,0,1,.5);
        layer(out,"gregtech:textures/gui/progressbar/assemblyline_3.png",146,26,10,18,0,0,1,.5);
        layer(out,"gregtech:textures/gui/picture/gt_logo_17x17_transparent.png",147,64,17,17);
        return out;
    }

    Map<String,Object> infusion(int components) {
        Map<String,Object> out=scene("infusion",166,122);
        // These are icon coordinates in NEI; the frame includes one pixel of padding.
        slot(out,"o",0,74,4);slot(out,"groups",0,74,62);
        for(int i=0;i<components;i++) {
            double angle=Math.toRadians(-90+360.0*i/Math.max(components,1));
            slot(out,"groups",i+1,74+(int)(40*Math.cos(angle)),62+(int)(40*Math.sin(angle)));
        }
        // Research-book decoration is drawn as a crop; keep it separate from stacks.
        // GuiDraw uses a 256-unit UV space, even for the 512px source atlas.
        layer(out,"thaumcraft:textures/gui/gui_researchbook_overlay.png",46,35,74,74,207.0/256,78.0/256,249.0/256,120.0/256);
        layer(out,"thaumcraft:textures/gui/gui_researchbook_overlay.png",69,-1,28,30,20.0/256,3.0/256,36.0/256,20.0/256);
        out.put("item_frames",false);
        return out;
    }

    Map<String,Object> gt(Object map) {
        try {
            Object frontend=map.getClass().getMethod("getFrontend").invoke(map);
            Object ui=frontend.getClass().getMethod("getUIProperties").invoke(frontend);
            Object nei=frontend.getClass().getMethod("getNEIProperties").invoke(frontend);
            Object size=Recipes.field(nei,"recipeBackgroundSize");
            Map<String,Object> out=scene("gt",number(size,"width"),number(size,"height"));
            Object name=Recipes.field(map,"unlocalizedName");
            if(name instanceof String) out.put("title",net.minecraft.util.StatCollector.translateToLocal((String)name));
            out.put("frontend",frontend.getClass().getName());
            Object formatter=Recipes.field(nei,"neiSpecialInfoFormatter");
            if(formatter!=null)out.put("special_info_formatter",formatter.getClass().getName());
            List<String> callbacks=new ArrayList<>();
            boolean assembly=false;
            for(Class<?> c=frontend.getClass();c!=null;c=c.getSuperclass()) {
                if(c.getName().equals("gregtech.api.recipe.maps.AssemblyLineFrontend")) assembly=true;
                if(c.getName().equals("gregtech.api.recipe.RecipeMapFrontend")) break;
                for(java.lang.reflect.Method method:c.getDeclaredMethods()) {
                    String m=method.getName();
                    if((m.startsWith("draw") || m.equals("addProgressBar") || m.equals("createNEITemplate")) && !method.isSynthetic())
                        callbacks.add(c.getName()+"#"+m);
                }
            }
            out.put("custom_callbacks",callbacks);
            layer(out,"gregtech:textures/gui/background/nei_single_recipe.png",0,0,number(size,"width"),number(size,"height"));
            String[] roles={"i","o","f","g"};
            String[] fields={"itemInputPositionsGetter","itemOutputPositionsGetter","fluidInputPositionsGetter","fluidOutputPositionsGetter"};
            String[] maxima={"maxItemInputs","maxItemOutputs","maxFluidInputs","maxFluidOutputs"};
            for(int k=0;k<roles.length;k++) {
                @SuppressWarnings("unchecked") IntFunction<List<?>> getter=(IntFunction<List<?>>)Recipes.field(ui,fields[k]);
                List<?> positions=getter.apply(number(ui,maxima[k]));
                for(int i=0;i<positions.size();i++)slot(out,roles[k],i,number(positions.get(i),"x"),number(positions.get(i),"y"));
            }
            if(Boolean.TRUE.equals(Recipes.field(ui,"useSpecialSlot"))) {
                Object pos=((java.util.function.Supplier<?>)Recipes.field(ui,"specialItemPositionGetter")).get();
                slot(out,"aux",0,number(pos,"x"),number(pos,"y"));
            }
            Object logo=Recipes.field(ui,"logo"), pos=Recipes.field(ui,"logoPos"), logoSize=Recipes.field(ui,"logoSize");
            textureLayer(out,logo,pos,logoSize,false);
            if(assembly) {
                layer(out,"gregtech:textures/gui/progressbar/assemblyline_1.png",88,8,17,72,0,0,1,.5);
                layer(out,"gregtech:textures/gui/progressbar/assemblyline_2.png",124,8,18,72,0,0,1,.5);
                layer(out,"gregtech:textures/gui/progressbar/assemblyline_3.png",146,26,10,18,0,0,1,.5);
            } else if(Boolean.TRUE.equals(Recipes.field(ui,"useProgressBar"))) {
                Object progress=Recipes.field(ui,"progressBarTexture");
                if(progress!=null)progress=progress.getClass().getMethod("get").invoke(progress);
                textureLayer(out,progress,Recipes.field(ui,"progressBarPos"),Recipes.field(ui,"progressBarSize"),true);
            }
            out.put("graphics", "standard_widgets"); // Custom Java draw callbacks are not serialized.
            return out;
        } catch(ReflectiveOperationException|RuntimeException unsupported) {
            IconExportMod.LOG.warn("No declarative recipe layout for {}: {}",map,unsupported.toString());return null;
        }
    }

    /** CachedDefaultRecipe appends slots beyond the template's declared IO limits. */
    @SuppressWarnings("unchecked")
    Map<String,Object> overflow(Object map, Object recipe) throws ReflectiveOperationException {
        Object frontend=map.getClass().getMethod("getFrontend").invoke(map);
        Object ui=frontend.getClass().getMethod("getUIProperties").invoke(frontend);
        String[] roles={"i","o","f","g"};
        String[] fields={"mInputs","mOutputs","mFluidInputs","mFluidOutputs"};
        String[] getters={"itemInputPositionsGetter","itemOutputPositionsGetter","fluidInputPositionsGetter","fluidOutputPositionsGetter"};
        String[] maxima={"maxItemInputs","maxItemOutputs","maxFluidInputs","maxFluidOutputs"};
        Map<String,Object> out=null;
        for(int k=0;k<roles.length;k++) {
            Object values=Recipes.field(recipe,fields[k]);
            int count=values==null?0:java.lang.reflect.Array.getLength(values), max=number(ui,maxima[k]);
            if(count<=max)continue;
            if(out==null)out=gt(map);
            if(out==null)return null;
            List<?> positions=((IntFunction<List<?>>)Recipes.field(ui,getters[k])).apply(count);
            for(int n=max;n<Math.min(positions.size(),count);n++) {
                if(java.lang.reflect.Array.get(values,n)==null)continue;
                int x=number(positions.get(n),"x"),y=number(positions.get(n),"y");
                slot(out,roles[k],n,x,y);
                out.put("width",Math.max(((Number)out.get("width")).intValue(),x+20));
                out.put("height",Math.max(((Number)out.get("height")).intValue(),y+20));
            }
        }
        if(out!=null) for(Map<String,Object> layer:(List<Map<String,Object>>)out.get("layers")) {
            if(String.valueOf(layer.get("asset")).endsWith("/nei_single_recipe.png")) {
                layer.put("w",out.get("width"));layer.put("h",out.get("height"));
            }
        }
        return out;
    }

    private void textureLayer(Map<String,Object> scene,Object texture,Object pos,Object size,boolean half) {
        if(texture==null||pos==null||size==null)return;
        Object location=Recipes.field(texture,"location");if(location==null)return;
        double u0=decimal(texture,"u0",0),v0=decimal(texture,"v0",0),u1=decimal(texture,"u1",1),v1=decimal(texture,"v1",1);
        layer(scene,location.toString(),number(pos,"x"),number(pos,"y"),number(size,"width"),number(size,"height"),u0,v0,u1,half?(v0+v1)/2:v1);
    }
    static int number(Object o,String field){Object value=Recipes.field(o,field);return value instanceof Number?((Number)value).intValue():0;}
    private static double decimal(Object o,String field,double fallback){Object v=Recipes.field(o,field);return v instanceof Number?((Number)v).doubleValue():fallback;}

    void research(String key) {
        if(key==null||key.isEmpty()||research.containsKey(key))return;
        try {
            Class<?> categories=Class.forName("thaumcraft.api.research.ResearchCategories");
            Object item=categories.getMethod("getResearch",String.class).invoke(null,key);
            if(item==null)return;
            Map<String,Object> value=new LinkedHashMap<>();value.put("name",item.getClass().getMethod("getName").invoke(item));
            String category=String.valueOf(Recipes.field(item,"category"));
            value.put("category",category);value.put("category_name",categories.getMethod("getCategoryName",String.class).invoke(null,category));
            Object group=categories.getMethod("getResearchList",String.class).invoke(null,category);
            Object icon=Recipes.field(group,"icon");value.put("icon",icon==null?null:asset(icon.toString()));
            value.put("parents",RecipeSnapshot.of(Recipes.field(item,"parents")));research.put(key,value);
        } catch(ReflectiveOperationException e){IconExportMod.LOG.warn("Research preview unavailable: {}",key);}
    }
    void aspects() {
        for(Map.Entry<String,Object> e:Aspects.all().entrySet()) {
            Map<String,Object> v=new LinkedHashMap<>();v.put("name",Aspects.label(e.getValue()));v.put("icon","aspect/"+e.getKey()+".webp");aspects.put(e.getKey(),v);
        }
    }
}

package gcm.iconexport;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidStack;

/** A bounded, cycle-safe archive of recipe data, never of a handler/world/registry. */
final class RecipeSnapshot {
    static Object of(Object value) { return of(value, 0, Collections.newSetFromMap(new IdentityHashMap<>())); }

    private static Object of(Object value, int depth, Set<Object> seen) {
        if (value == null || value instanceof Number || value instanceof Boolean || value instanceof String) return value;
        if (value.getClass().getName().equals("thaumcraft.api.aspects.AspectList")) {
            Map<String,Object> aspects=new TreeMap<>();
            try {
                for(Object aspect:(Object[])value.getClass().getMethod("getAspects").invoke(value)) {
                    if (aspect == null) { aspects.put("Unresolved aspect", true); continue; }
                    aspects.put(String.valueOf(aspect.getClass().getMethod("getTag").invoke(aspect)),
                            value.getClass().getMethod("getAmount",Class.forName("thaumcraft.api.aspects.Aspect")).invoke(value,aspect));
                }
                return aspects;
            } catch(ReflectiveOperationException e) { throw new IllegalStateException(e); }
        }
        if (value instanceof Enum) return ((Enum<?>) value).name();
        if (value instanceof net.minecraft.nbt.NBTBase) return value.toString();
        if (value instanceof ItemStack) {
            ItemStack stack = (ItemStack) value;
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("item", Recipes.keyOf(stack)); out.put("amount", stack.stackSize);
            if (stack.hasTagCompound()) out.put("nbt", stack.getTagCompound().toString());
            return out;
        }
        if (value instanceof FluidStack) {
            FluidStack stack = (FluidStack) value;
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("fluid", stack.getFluid().getName()); out.put("amount", stack.amount);
            if (stack.tag != null) out.put("nbt", stack.tag.toString());
            return out;
        }
        if (depth > 5 || !seen.add(value)) return Collections.singletonMap("unexpanded_class", value.getClass().getName());
        try {
            if (value.getClass().isArray()) {
                List<Object> out = new ArrayList<>();
                for (int i=0;i<Math.min(512,Array.getLength(value));i++) out.add(of(Array.get(value,i),depth+1,seen));
                if (Array.getLength(value)>512) out.add(Collections.singletonMap("truncated_entries",Array.getLength(value)-512));
                return out;
            }
            if (value instanceof Iterable) {
                List<Object> out = new ArrayList<>();
                for (Object v : (Iterable<?>) value) {
                    if (out.size()>=512) { out.add(Collections.singletonMap("truncated",true));break; }
                    out.add(of(v,depth+1,seen));
                }
                return out;
            }
            if (value instanceof Map) {
                Map<String,Object> out = new TreeMap<>();
                for (Map.Entry<?,?> e : ((Map<?,?>) value).entrySet()) {
                    if (out.size()>=512) { out.put("truncated",true);break; }
                    out.put(String.valueOf(e.getKey()),of(e.getValue(),depth+1,seen));
                }
                return out;
            }
            String name = value.getClass().getName();
            // Traverse recipe/value objects only. A captured outer handler points
            // back to all recipes and potentially the live client.
            if (name.startsWith("java.") || name.startsWith("net.minecraft.")
                    || name.contains("Handler") || name.contains("World") || name.contains("Entity")
                    || name.contains("RecipeMap") || name.contains("Registry") || name.contains("MetadataStorage")
                    || !(name.toLowerCase(java.util.Locale.ROOT).contains("recipe")
                         || name.equals("codechicken.nei.PositionedStack") || name.endsWith("FluidTank"))) {
                return Collections.singletonMap("unexpanded_class",name);
            }
            Map<String,Object> fields = new TreeMap<>();
            fields.put("class",name);
            for (Class<?> c=value.getClass();c!=null && c!=Object.class;c=c.getSuperclass()) {
                for (Field f:c.getDeclaredFields()) {
                    if (Modifier.isStatic(f.getModifiers()) || f.isSynthetic()) continue;
                    String key=fields.containsKey(f.getName())?c.getName()+"#"+f.getName():f.getName();
                    try { f.setAccessible(true); fields.put(key,of(f.get(value),depth+1,seen)); }
                    catch (ReflectiveOperationException | RuntimeException inaccessible) { fields.put(key,Collections.singletonMap("unexpanded_class",f.getType().getName())); }
                }
            }
            return fields;
        } finally { seen.remove(value); }
    }
}

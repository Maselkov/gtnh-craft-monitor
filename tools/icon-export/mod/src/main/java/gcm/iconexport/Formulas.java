package gcm.iconexport;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;

import net.minecraft.init.Items;
import net.minecraft.item.ItemStack;
import net.minecraftforge.fluids.FluidContainerRegistry;

import cpw.mods.fml.common.registry.GameRegistry;

/**
 * Chemical formulas, read the way GregTech's own NEI formula search ({@code =}, its
 * ChemicalFormulaFilter) reads them: an item's GregTech material through the ore-dictionary
 * unificator, else the GT++ material it's a component of. A fluid's is that of a container filled
 * with it (a cell), since containers are items with materials. Formulas keep GregTech's own font
 * glyphs (superscripts as {@code }...); the page maps them.
 *
 * <p>Read by reflection, so the mod still compiles against NEI alone and runs without GregTech.
 */
final class Formulas {

    private static Method getAssociation;
    private static Field itemMaterial;
    private static Field stackMaterial;
    private static Method chemicalFormula;
    /** "mod:internal:damage" of each GT++ material component -> its material's formula. */
    private static final Map<String, String> gtPlusPlus = new HashMap<>();
    private static boolean ready;

    private Formulas() {}

    /** Looks GregTech and GT++ up; false if GregTech isn't there. */
    static boolean init() {
        try {
            Class<?> unificator = Class.forName("gregtech.api.util.GTOreDictUnificator");
            getAssociation = unificator.getMethod("getAssociation", ItemStack.class);
            itemMaterial = Class.forName("gregtech.api.objects.ItemData").getField("mMaterial");
            stackMaterial = Class.forName("gregtech.api.objects.MaterialStack").getField("mMaterial");
            chemicalFormula = Class.forName("gregtech.api.enums.Materials").getMethod("getChemicalFormula");
            ready = true;
        } catch (Throwable t) {
            IconExportMod.LOG.info("No GregTech formulas: {}", t.toString());
            return false;
        }
        try {
            Class<?> material = Class.forName("gtPlusPlus.core.material.Material");
            Map<?, ?> components = (Map<?, ?>) material.getField("mComponentMap").get(null);
            Map<?, ?> byName = (Map<?, ?>) material.getField("mMaterialsByName").get(null);
            Field formula = material.getField("chemicalFormula");
            for (Map.Entry<?, ?> entry : components.entrySet()) {
                Object owner = byName.get(entry.getKey());
                String text = owner == null ? null : (String) formula.get(owner);
                if (!valid(text)) {
                    continue;
                }
                for (Object stack : ((Map<?, ?>) entry.getValue()).values()) {
                    String key = keyOf((ItemStack) stack);
                    if (key != null) {
                        gtPlusPlus.put(key, text);
                    }
                }
            }
        } catch (Throwable t) {
            IconExportMod.LOG.info("No GT++ formulas: {}", t.toString());
        }
        return true;
    }

    /** An item's formula, or null. */
    static String of(ItemStack stack) {
        if (!ready || stack == null) {
            return null;
        }
        try {
            Object data = getAssociation.invoke(null, stack);
            Object materialStack = data == null ? null : itemMaterial.get(data);
            Object material = materialStack == null ? null : stackMaterial.get(materialStack);
            if (material != null) {
                String text = (String) chemicalFormula.invoke(material);
                if (valid(text)) {
                    return text;
                }
            }
        } catch (Throwable ignored) {}
        String key = keyOf(stack);
        return key == null ? null : gtPlusPlus.get(key);
    }

    /** Each fluid's formula by its registry name: that of a container filled with it. */
    static Map<String, String> ofFluids() {
        Map<String, String> out = new HashMap<>();
        if (!ready) {
            return out;
        }
        for (FluidContainerRegistry.FluidContainerData data : FluidContainerRegistry
                .getRegisteredFluidContainerData()) {
            try {
                if (data.fluid == null || data.fluid.getFluid() == null || data.filledContainer == null) {
                    continue;
                }
                String name = data.fluid.getFluid().getName();
                if (out.containsKey(name)) {
                    continue;
                }
                String text = of(data.filledContainer);
                if (text != null) {
                    out.put(name, text);
                }
            } catch (Throwable ignored) {}
        }
        return out;
    }

    /** GregTech's own test: "?" and "??" mean unknown. */
    private static boolean valid(String text) {
        return text != null && !text.isEmpty() && !text.equals("?") && !text.equals("??");
    }

    /** As ExportJob keys items: the raw damage, which is what AE2 reports. */
    private static String keyOf(ItemStack stack) {
        if (stack == null || stack.getItem() == null) {
            return null;
        }
        GameRegistry.UniqueIdentifier id = GameRegistry.findUniqueIdentifierFor(stack.getItem());
        return id == null ? null : id.modId + ":" + id.name + ":" + Items.feather.getDamage(stack);
    }
}

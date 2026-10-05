package gcm.iconexport;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

import net.minecraft.item.ItemStack;
import net.minecraftforge.oredict.OreDictionary;

import cpw.mods.fml.common.registry.GameRegistry;

/**
 * The ore dictionary, for the server's crafting plan: AE2 lets a crafting pattern with Substitute
 * ticked use anything sharing an ore-dictionary name with an input (gcm/oredict.py).
 *
 * <p>{name: ["mod:internal:damage", ...]}, damage "*" for every damage of an item. Only names that
 * can stand in for something are kept: more than one entry, or any damage of one item.
 */
final class OreDict {

    private OreDict() {}

    static Map<String, List<String>> collect() {
        Map<String, List<String>> out = new TreeMap<>();
        for (String name : OreDictionary.getOreNames()) {
            if (name == null || name.isEmpty()) {
                continue;
            }
            Set<String> entries = new LinkedHashSet<>();
            boolean wildcard = false;
            for (ItemStack stack : OreDictionary.getOres(name, false)) {
                if (stack == null || stack.getItem() == null) {
                    continue;
                }
                GameRegistry.UniqueIdentifier id;
                try {
                    id = GameRegistry.findUniqueIdentifierFor(stack.getItem());
                } catch (Throwable t) {
                    continue;
                }
                if (id == null) {
                    continue;
                }
                int damage = stack.getItemDamage();
                boolean any = damage == OreDictionary.WILDCARD_VALUE;
                wildcard |= any;
                entries.add(id.modId + ":" + id.name + ":" + (any ? "*" : Integer.toString(damage)));
            }
            if (entries.size() > 1 || wildcard) {
                out.put(name, new ArrayList<>(entries));
            }
        }
        return out;
    }
}

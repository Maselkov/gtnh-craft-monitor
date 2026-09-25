package gcm.iconexport;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.UUID;

import net.minecraft.init.Items;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;
import net.minecraftforge.fluids.FluidStack;

import cpw.mods.fml.common.registry.GameRegistry;

/**
 * One icon to render, plus the identifiers the server looks it up by.
 *
 * <p>Image paths use NESQL-Exporter's scheme exactly ({@code item/<mod>/<name>~<damage>[~<nbt>].png},
 * {@code fluid/<mod>/<name>.png}), so an images.zip from either tool can be swapped for the other
 * and the two can be diffed entry by entry.
 */
final class ExportJob {

    enum Kind {
        ITEM,
        FLUID
    }

    final Kind kind;
    final ItemStack item;
    final FluidStack fluid;
    /** Path inside images.zip. */
    final String imagePath;
    /** {@code mod:internal:damage} for items, the bare Forge fluid name for fluids. */
    final String lookupKey;
    /** {@code mod:internal} for items (an item_catalog.txt line), null for fluids. */
    final String catalogId;
    /** Display name, or null if the item throws when asked for one. */
    final String label;
    final boolean hasNbt;

    boolean rendered;
    /** Hash of the still render's pixels (see ExportDriver.hash). */
    long stillHash;

    private ExportJob(Kind kind, ItemStack item, FluidStack fluid, String imagePath, String lookupKey,
            String catalogId, String label, boolean hasNbt) {
        this.kind = kind;
        this.item = item;
        this.fluid = fluid;
        this.imagePath = imagePath;
        this.lookupKey = lookupKey;
        this.catalogId = catalogId;
        this.label = label;
        this.hasNbt = hasNbt;
    }

    /** Returns null for stacks whose item isn't in the registry (some recipes use such items). */
    static ExportJob forItem(ItemStack listed) {
        Item item = listed.getItem();
        if (Item.itemRegistry.getNameForObject(item) == null) {
            return null;
        }
        GameRegistry.UniqueIdentifier id = GameRegistry.findUniqueIdentifierFor(item);
        if (id == null) {
            return null;
        }

        ItemStack stack = listed.copy();
        stack.stackSize = 1;
        // Not getItemDamage(): items can override that. The raw field is what AE2 reports.
        int damage = Items.feather.getDamage(stack);
        NBTTagCompound nbt = stack.getTagCompound();

        String fileId = sanitize(id.modId + "~" + id.name) + "~" + damage;
        if (nbt != null) {
            fileId += "~" + encodeNbt(nbt);
        }

        return new ExportJob(
                Kind.ITEM,
                stack,
                null,
                "item/" + splitFirst(fileId) + ".png",
                id.modId + ":" + id.name + ":" + damage,
                id.modId + ":" + id.name,
                safeLabel(stack),
                nbt != null);
    }

    /** Returns null for fluids the registry can't name. */
    static ExportJob forFluid(Fluid fluid) {
        String uniqueName = FluidRegistry.getDefaultFluidName(fluid);
        if (uniqueName == null || uniqueName.indexOf(':') < 0) {
            return null;
        }
        int colon = uniqueName.indexOf(':');
        String modId = uniqueName.substring(0, colon);
        String name = uniqueName.substring(colon + 1);
        FluidStack stack = new FluidStack(fluid, 1000);

        String label;
        try {
            label = stack.getLocalizedName();
        } catch (Throwable t) {
            label = null;
        }

        return new ExportJob(
                Kind.FLUID,
                null,
                stack,
                "fluid/" + splitFirst(sanitize(modId + "~" + name)) + ".png",
                name,
                null,
                label,
                false);
    }

    private static String safeLabel(ItemStack stack) {
        try {
            String label = stack.getDisplayName();
            return label == null || label.isEmpty() ? null : label;
        } catch (Throwable t) {
            return null;
        }
    }

    /** "mod~rest" -> "mod/rest", splitting on the first separator only. */
    private static String splitFirst(String fileId) {
        int sep = fileId.indexOf('~');
        return fileId.substring(0, sep) + "/" + fileId.substring(sep + 1);
    }

    /** NESQL's IdUtil.sanitize: strip characters that aren't file-system safe. */
    private static String sanitize(String s) {
        return s.replaceAll("[<>:\"/\\\\|?*]", "");
    }

    /** NESQL's StringUtil.encodeNbt: base64url of the name-based UUID of the NBT's string form. */
    private static String encodeNbt(NBTTagCompound nbt) {
        UUID uuid = UUID.nameUUIDFromBytes(nbt.toString().getBytes(StandardCharsets.UTF_8));
        ByteBuffer bytes = ByteBuffer.allocate(16);
        bytes.putLong(uuid.getMostSignificantBits());
        bytes.putLong(uuid.getLeastSignificantBits());
        return Base64.getUrlEncoder().encodeToString(bytes.array());
    }
}

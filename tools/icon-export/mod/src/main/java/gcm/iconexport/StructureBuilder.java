package gcm.iconexport;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import net.minecraft.block.Block;
import net.minecraft.block.BlockLiquid;
import net.minecraft.init.Blocks;
import net.minecraft.init.Items;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.util.IIcon;
import net.minecraftforge.common.util.ForgeDirection;
import net.minecraftforge.fluids.IFluidBlock;

import com.gtnewhorizon.gtnhlib.util.CoordinatePacker;
import com.gtnewhorizon.structurelib.StructureLibAPI;
import com.gtnewhorizon.structurelib.alignment.constructable.ChannelDataAccessor;
import com.gtnewhorizon.structurelib.alignment.constructable.IConstructable;
import com.gtnewhorizon.structurelib.structure.IChannelDefinition;

import blockrenderer6343.client.utils.BRUtil;
import blockrenderer6343.client.utils.ConstructableData;
import blockrenderer6343.client.world.TrackedDummyWorld;
import blockrenderer6343.integration.gregtech.GTConstructableScan;
import cpw.mods.fml.common.registry.GameRegistry;
import it.unimi.dsi.fastutil.objects.Object2IntMap;

/**
 * Builds each GregTech multiblock in BlockRenderer6343's dummy world, the way its NEI preview does
 * (GTGuiMultiblockHandler.placeMultiblock), and counts the blocks. See Structures for the output.
 * GregTech is reached by reflection; StructureLib and BlockRenderer6343 directly.
 */
final class StructureBuilder {

    /** Where the controller goes, as in BlockRenderer6343. */
    private static final int X = 0, Y = 64, Z = 0;
    /** Channels that go higher (a structure's length) are cut to this many tiers. */
    private static final int MAX_TIERS = 64;
    /** The most combinations of channels built for a multiblock whose channels interact. */
    private static final int MAX_COMBOS = 4096;

    private final TrackedDummyWorld world = new TrackedDummyWorld();
    private final Map<String, String> names = new TreeMap<>();
    /** layouts.json.gz's blocks: block key -> {item, faces, tint, translucent}; see Layouts. */
    private final Map<String, Map<String, Object>> blocks = new TreeMap<>();
    /** The face textures the blocks use, by name, for Layouts to draw. */
    final Map<String, IIcon> icons = new LinkedHashMap<>();
    /** layouts.json.gz's multiblocks, filled beside collect()'s. */
    final Map<String, Object> layouts = new TreeMap<>();
    /** Captures what the game draws for each kind of block; null if it can't. */
    private BlockMeshes meshes;
    private int builds;
    private final Class<?> hatchClass;
    private final Class<?> previewModifier;
    private final Class<?> gtTile;
    private final Method getMetaTileEntity;
    private final Method setFrontFacing;
    private final Method isUpdateThreadEnabled;
    private final Method setUpdateThreadEnabled;

    StructureBuilder() throws ReflectiveOperationException {
        hatchClass = Class.forName("gregtech.api.metatileentity.implementations.MTEHatch");
        previewModifier = Class.forName("gregtech.api.interfaces.INEIPreviewModifier");
        gtTile = Class.forName("gregtech.api.interfaces.tileentity.IGregTechTileEntity");
        getMetaTileEntity = gtTile.getMethod("getMetaTileEntity");
        setFrontFacing = Class.forName("gregtech.api.interfaces.tileentity.ITurnable")
                .getMethod("setFrontFacing", ForgeDirection.class);
        Class<?> updates = Class.forName("gregtech.api.threads.RunnableMachineUpdate");
        isUpdateThreadEnabled = updates.getMethod("isCurrentThreadEnabled");
        setUpdateThreadEnabled = updates.getMethod("setCurrentThreadEnabled", boolean.class);
    }

    Map<String, Object> collect(Map<String, Object> report) throws ReflectiveOperationException {
        long start = System.currentTimeMillis();
        Object[] all = (Object[]) Class.forName("gregtech.api.GregTechAPI").getField("METATILEENTITIES")
                .get(null);
        List<IConstructable> multis = new ArrayList<>();
        for (Object mte : all) {
            if (mte instanceof IConstructable) {
                multis.add((IConstructable) mte);
            }
        }
        // Finds each multiblock's channels and their highest tiers (ConstructableData).
        new GTConstructableScan(result -> {}, multis).run();
        IconExportMod.LOG.info("Scanned {} multiblocks' channels in {} ms.", multis.size(),
                System.currentTimeMillis() - start);

        BRUtil.FAKE_PLAYER.setWorld(world);
        try {
            meshes = new BlockMeshes(world, icons);
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Can't capture block geometry ({}); the 3D view gets cubes.", t.toString());
        }
        Map<String, Object> out = new TreeMap<>();
        List<String> failed = new ArrayList<>();
        List<String> combined = new ArrayList<>();
        List<String> approximate = new ArrayList<>();
        boolean wasEnabled = (Boolean) isUpdateThreadEnabled.invoke(null);
        setUpdateThreadEnabled.invoke(null, false);
        try {
            for (IConstructable multi : multis) {
                ItemStack form;
                String key;
                try {
                    form = (ItemStack) multi.getClass().getMethod("getStackForm", long.class).invoke(multi, 1L);
                    key = key(form);
                } catch (Throwable t) {
                    continue;
                }
                if (key == null) {
                    continue;
                }
                try {
                    Map<String, Object> layout = new LinkedHashMap<>();
                    Map<String, Object> entry = multiblock(multi, form, layout);
                    if (entry == null) {
                        failed.add(key + " (nothing built)");
                        continue;
                    }
                    layouts.put(key, layout);
                    if (entry.containsKey("combos")) {
                        combined.add(key);
                    } else if (Boolean.TRUE.equals(entry.get("approximate"))) {
                        approximate.add(key);
                    }
                    out.put(key, entry);
                } catch (Throwable t) {
                    failed.add(key + " (" + t + ")");
                    IconExportMod.LOG.warn("Couldn't build multiblock {}: {}", key, t.toString());
                }
            }
        } finally {
            world.clear();
            setUpdateThreadEnabled.invoke(null, wasEnabled);
        }
        long millis = System.currentTimeMillis() - start;
        IconExportMod.LOG.info("Built {} of {} multiblocks ({} builds) in {} ms; {} failed.", out.size(),
                multis.size(), builds, millis, failed.size());
        report.put("multiblocks", out.size());
        report.put("multiblockBuilds", builds);
        report.put("multiblockMillis", millis);
        report.put("multiblocksFailed", failed);
        report.put("multiblocksCombined", combined);
        report.put("multiblocksApproximate", approximate);
        report.put("blockMeshes", meshes == null ? 0 : meshes.captured);
        report.put("blockMeshesFailed", meshes == null ? 0 : meshes.failed);

        Map<String, Object> file = new LinkedHashMap<>();
        file.put("format", 1);
        file.put("names", names);
        file.put("multiblocks", out);
        return file;
    }

    /** layouts.json.gz's blocks (the palette), once collect() has run. */
    Map<String, Map<String, Object>> blocks() {
        return blocks;
    }

    /**
     * One multiblock's entry (see Structures), or null if building it placed nothing; its layout
     * (see Layouts) goes in {@code layout}.
     */
    private Map<String, Object> multiblock(IConstructable multi, ItemStack form, Map<String, Object> layout)
            throws ReflectiveOperationException {
        Build baseBuild = build(form, Collections.emptyMap());
        Map<String, Integer> base = baseBuild.counts;
        List<Channel> channels = channels(multi);
        List<Object> channelList = new ArrayList<>();
        Map<String, Object> tiers = new TreeMap<>();
        Map<String, Object> layoutTiers = new TreeMap<>();
        for (Channel channel : channels) {
            Map<String, Object> byTier = new LinkedHashMap<>();
            Map<String, Object> layoutByTier = new LinkedHashMap<>();
            for (int tier = channel.min; tier <= channel.max; tier++) {
                Build built = build(form, Collections.singletonMap(channel.name, tier));
                byTier.put(String.valueOf(tier), difference(base, built.counts));
                layoutByTier.put(String.valueOf(tier), Layouts.change(baseBuild.layout, built.layout));
            }
            Map<String, Object> c = new LinkedHashMap<>();
            c.put("name", channel.name);
            c.put("min", channel.min);
            c.put("max", channel.max);
            channelList.add(c);
            tiers.put(channel.name, byTier);
            layoutTiers.put(channel.name, layoutByTier);
        }
        if (base.isEmpty() && tiers.isEmpty()) {
            return null;
        }
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("channels", channelList);
        entry.put("base", base);
        layout.put("base", Layouts.encode(baseBuild.layout));
        if (!tiers.isEmpty()) {
            entry.put("tiers", tiers);
            layout.put("tiers", layoutTiers);
        }
        if (channels.size() > 1 && !independent(form, channels, base, tiers)) {
            // A channel changes what another one adds (the assembly line's glass comes with its
            // length): every combination, built, when there aren't too many.
            long count = 1;
            for (Channel channel : channels) {
                count *= channel.max - channel.min + 1;
            }
            if (count <= MAX_COMBOS) {
                Map<String, Object> combos = new LinkedHashMap<>();
                Map<String, Object> layoutCombos = new LinkedHashMap<>();
                combine(form, channels, 0, new LinkedHashMap<>(), baseBuild.layout, combos, layoutCombos);
                entry.put("combos", combos);
                layout.put("combos", layoutCombos);
            } else {
                entry.put("approximate", true);
            }
        }
        return entry;
    }

    /**
     * Whether base plus each channel's changes gives what's built with the channels set together,
     * tried with every channel at its lowest tier above base and at its highest.
     */
    private boolean independent(ItemStack form, List<Channel> channels, Map<String, Integer> base,
            Map<String, Object> tiers) throws ReflectiveOperationException {
        for (boolean top : new boolean[] { false, true }) {
            Map<String, Integer> set = new LinkedHashMap<>();
            Map<String, Integer> summed = new TreeMap<>(base);
            for (Channel channel : channels) {
                int tier = top ? channel.max : Math.min(channel.min + 1, channel.max);
                set.put(channel.name, tier);
                @SuppressWarnings("unchecked")
                Map<String, Integer> change = (Map<String, Integer>) ((Map<?, ?>) tiers.get(channel.name))
                        .get(String.valueOf(tier));
                for (Map.Entry<String, Integer> e : change.entrySet()) {
                    summed.merge(e.getKey(), e.getValue(), Integer::sum);
                }
            }
            summed.values().removeIf(n -> n == 0);
            if (!summed.equals(build(form, set).counts)) {
                return false;
            }
        }
        return true;
    }

    /** Builds every combination of the channels from {@code index} on, keyed "a=1,b=2". */
    private void combine(ItemStack form, List<Channel> channels, int index, Map<String, Integer> set,
            Map<Integer, String> baseLayout, Map<String, Object> combos, Map<String, Object> layoutCombos)
            throws ReflectiveOperationException {
        if (index == channels.size()) {
            StringBuilder name = new StringBuilder();
            for (Map.Entry<String, Integer> e : set.entrySet()) {
                name.append(name.length() == 0 ? "" : ",").append(e.getKey()).append('=').append(e.getValue());
            }
            Build built = build(form, set);
            combos.put(name.toString(), built.counts);
            layoutCombos.put(name.toString(), Layouts.change(baseLayout, built.layout));
            return;
        }
        Channel channel = channels.get(index);
        for (int tier = channel.min; tier <= channel.max; tier++) {
            set.put(channel.name, tier);
            combine(form, channels, index + 1, set, baseLayout, combos, layoutCombos);
        }
        set.remove(channel.name);
    }

    private static final class Channel {

        final String name;
        final int min, max;

        Channel(String name, int min, int max) {
            this.name = name;
            this.min = min;
            this.max = max;
        }
    }

    /** The channels BlockRenderer6343's preview shows a slider for, with their ranges. */
    private static List<Channel> channels(IConstructable multi) {
        ConstructableData data = ConstructableData.getTierData(multi);
        List<Channel> channels = new ArrayList<>();
        for (Object2IntMap.Entry<String> e : data.getChannelMaxTierMap().object2IntEntrySet()) {
            IChannelDefinition definition = data.getChannelDefinitions().get(e.getKey());
            int min = definition == null ? 1 : Math.max(1, definition.getMinimumValue());
            int max = Math.min(e.getIntValue(), min + MAX_TIERS - 1);
            if (max >= min) {
                channels.add(new Channel(e.getKey(), min, max));
            }
        }
        channels.sort((a, b) -> a.name.compareTo(b.name));
        return channels;
    }

    private static ItemStack trigger(Map<String, Integer> channels) {
        ItemStack trigger = new ItemStack(StructureLibAPI.getDefaultHologramItem());
        for (Map.Entry<String, Integer> e : channels.entrySet()) {
            ChannelDataAccessor.setChannelData(trigger, e.getKey(), e.getValue());
        }
        return trigger;
    }

    /** One build: its blocks counted by item key (the controller left out), and where each is. */
    private static final class Build {

        final Map<String, Integer> counts = new TreeMap<>();
        /** Layouts.pack()ed position, relative to the controller -> block key. */
        final Map<Integer, String> layout = new TreeMap<>();
    }

    /** Builds the multiblock, counting its blocks and noting where each one is. */
    private Build build(ItemStack form, Map<String, Integer> channels) throws ReflectiveOperationException {
        builds++;
        ItemStack trigger = trigger(channels);
        world.clear();
        ItemStack copy = form.copy();
        copy.getItem().onItemUse(copy, BRUtil.FAKE_PLAYER, world, X, Y, Z, 0, X, Y, Z);
        TileEntity tile = world.getTileEntity(X, Y, Z);
        if (!gtTile.isInstance(tile)) {
            throw new IllegalStateException("controller not placed");
        }
        setFrontFacing.invoke(tile, ForgeDirection.SOUTH);
        Object mte = getMetaTileEntity.invoke(tile);
        if (previewModifier.isInstance(mte)) {
            previewModifier.getMethod("onPreviewConstruct", ItemStack.class).invoke(mte, trigger);
        }
        // StructureLib's creative build, as the hologram projector does it in creative mode. Its
        // survival build stops at the first spot only a hatch can fill (an EBF's muffler).
        if (mte instanceof IConstructable) {
            ((IConstructable) mte).construct(trigger, false);
        }
        if (previewModifier.isInstance(mte)) {
            previewModifier.getMethod("onPreviewStructureComplete", ItemStack.class).invoke(mte, trigger);
        }

        Build result = new Build();
        for (Map.Entry<Long, Block> e : world.blockMap.entrySet()) {
            long pos = e.getKey();
            int x = CoordinatePacker.unpackX(pos), y = CoordinatePacker.unpackY(pos),
                    z = CoordinatePacker.unpackZ(pos);
            Block block = e.getValue();
            if (block == Blocks.air || block instanceof BlockLiquid || block instanceof IFluidBlock) {
                continue;
            }
            TileEntity te = world.getTileEntity(x, y, z);
            if (gtTile.isInstance(te) && hatchClass.isInstance(getMetaTileEntity.invoke(te))) {
                continue;
            }
            boolean controller = x == X && y == Y && z == Z;
            ItemStack stack = controller ? form : drop(block, x, y, z);
            String key = stack == null ? null : key(stack);
            if (key != null && !controller) {
                result.counts.merge(key, 1, Integer::sum);
            }
            String blockKey = gtTile.isInstance(te) ? machineKey(block, x, y, z, key)
                    : blockKey(block, x, y, z, key);
            if (blockKey != null) {
                result.layout.put(Layouts.pack(x - X, y - Y, z - Z), blockKey);
            }
        }
        return result;
    }

    /**
     * The layout's key for a GregTech machine ({@code mte:} and its item key): every machine is
     * one block whose meta doesn't say which machine it is, and whose faces are drawn from the
     * machine rather than block textures, so the page shows its item icon instead.
     */
    private String machineKey(Block block, int x, int y, int z, String itemKey) {
        if (itemKey == null) {
            return null;
        }
        String key = "mte:" + itemKey;
        if (!blocks.containsKey(key)) {
            Map<String, Object> info = new LinkedHashMap<>();
            info.put("item", itemKey);
            putMesh(info, block, x, y, z);
            blocks.put(key, info);
        }
        return key;
    }

    /** The block's captured geometry, where it could be captured (BlockMeshes). */
    private void putMesh(Map<String, Object> info, Block block, int x, int y, int z) {
        if (meshes == null) {
            return;
        }
        Map<String, Object> mesh = meshes.capture(block, x, y, z);
        if (mesh != null) {
            info.put("mesh", mesh);
        }
    }

    /**
     * The layout's key for the block at x, y, z ({@code mod:name:meta}), noting its faces in
     * {@link #blocks} the first time it's seen; null if the registry can't name it.
     */
    private String blockKey(Block block, int x, int y, int z, String itemKey) {
        Object name = Block.blockRegistry.getNameForObject(block);
        if (name == null) {
            return null;
        }
        int meta = world.getBlockMetadata(x, y, z);
        String key = name + ":" + meta;
        if (blocks.containsKey(key)) {
            return key;
        }
        Map<String, Object> info = new LinkedHashMap<>();
        info.put("item", itemKey);
        List<String> faces = new ArrayList<>();
        boolean any = false;
        for (int side = 0; side < 6; side++) {
            IIcon icon = null;
            try {
                // As the world draws it, which can depend on the block's surroundings.
                icon = block.getIcon(world, x, y, z, side);
            } catch (Throwable t) {
                try {
                    icon = block.getIcon(side, meta);
                } catch (Throwable ignored) {
                    // No face texture: the page shows its item icon instead.
                }
            }
            String iconName = icon == null ? null : icon.getIconName();
            if (iconName != null) {
                icons.putIfAbsent(iconName, icon);
                any = true;
            }
            faces.add(iconName);
        }
        if (any) {
            info.put("faces", faces);
        }
        try {
            int tint = block.colorMultiplier(world, x, y, z) & 0xFFFFFF;
            if (tint != 0xFFFFFF) {
                info.put("tint", tint);
            }
        } catch (Throwable ignored) {
            // Untinted.
        }
        if (block.getRenderBlockPass() == 1) {
            info.put("translucent", true);
        }
        putMesh(info, block, x, y, z);
        blocks.put(key, info);
        return key;
    }

    /** What the block drops when broken, the way BlockRenderer6343's ingredient list reads it. */
    private ItemStack drop(Block block, int x, int y, int z) {
        int meta = world.getBlockMetadata(x, y, z);
        if (block.quantityDropped(world.rand) != 1) {
            Item item = Item.getItemFromBlock(block);
            return item == null ? null : new ItemStack(item, 1, block.damageDropped(meta));
        }
        List<ItemStack> drops = block.getDrops(world, x, y, z, meta, 0);
        return drops == null || drops.isEmpty() ? null : drops.get(0);
    }

    /** ExportJob's key for the stack, noting its name; null if it isn't in the registry. */
    private String key(ItemStack stack) {
        if (stack.getItem() == null || Item.itemRegistry.getNameForObject(stack.getItem()) == null) {
            return null;
        }
        GameRegistry.UniqueIdentifier id = GameRegistry.findUniqueIdentifierFor(stack.getItem());
        if (id == null) {
            return null;
        }
        String key = id.modId + ":" + id.name + ":" + Items.feather.getDamage(stack);
        if (!names.containsKey(key)) {
            try {
                names.put(key, stack.getDisplayName());
            } catch (Throwable t) {
                names.put(key, null);
            }
        }
        return key;
    }

    /** What changes from {@code base} to {@code built}: counts added, or taken away (negative). */
    private static Map<String, Integer> difference(Map<String, Integer> base, Map<String, Integer> built) {
        Map<String, Integer> change = new TreeMap<>();
        for (Map.Entry<String, Integer> e : built.entrySet()) {
            int n = e.getValue() - base.getOrDefault(e.getKey(), 0);
            if (n != 0) {
                change.put(e.getKey(), n);
            }
        }
        for (Map.Entry<String, Integer> e : base.entrySet()) {
            if (!built.containsKey(e.getKey())) {
                change.put(e.getKey(), -e.getValue());
            }
        }
        return change;
    }
}

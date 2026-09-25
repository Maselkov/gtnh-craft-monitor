package gcm.iconexport;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import cpw.mods.fml.common.FMLCommonHandler;
import cpw.mods.fml.common.Mod;
import cpw.mods.fml.common.event.FMLInitializationEvent;

/**
 * Headless icon/catalog export for gtnh-craft-monitor.
 *
 * <p>Does nothing unless the JVM is started with {@code -Dgcm.iconexport=true}, so leaving the jar
 * in a normal instance is harmless. When enabled, {@link ExportDriver} waits for the main menu,
 * builds NEI's item list, renders every listed item and every registered fluid, writes the output
 * files and exits the game.
 */
@Mod(
        modid = IconExportMod.MODID,
        name = "GCM Icon Export",
        version = Tags.VERSION,
        acceptableRemoteVersions = "*",
        dependencies = "required-after:NotEnoughItems")
public final class IconExportMod {

    public static final String MODID = "gcmiconexport";
    public static final Logger LOG = LogManager.getLogger("GCMIconExport");

    @Mod.EventHandler
    public void init(FMLInitializationEvent event) {
        if (!Boolean.getBoolean("gcm.iconexport")) {
            return;
        }
        if (!event.getSide().isClient()) {
            LOG.warn("gcm.iconexport is set, but this is a dedicated server - icons need a client. Ignoring.");
            return;
        }
        LOG.info("Icon export enabled.");
        FMLCommonHandler.instance().bus().register(new ExportDriver(ExportConfig.fromSystemProperties()));
    }
}

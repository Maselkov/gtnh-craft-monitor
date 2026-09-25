package gcm.iconexport.core;

import java.util.Map;

import cpw.mods.fml.relauncher.IFMLLoadingPlugin;

/**
 * Registers {@link ClockTransformer}, only when the game is started for an export
 * ({@code -Dgcm.iconexport=true}), so the jar changes nothing in a normal instance. Sorted after
 * FML's deobfuscation, so the transformer sees MCP class names and SRG member names.
 */
@IFMLLoadingPlugin.MCVersion("1.7.10")
@IFMLLoadingPlugin.SortingIndex(1001)
@IFMLLoadingPlugin.TransformerExclusions("gcm.iconexport.core")
public final class ExportCorePlugin implements IFMLLoadingPlugin {

    @Override
    public String[] getASMTransformerClass() {
        return Boolean.getBoolean("gcm.iconexport") ? new String[] { ClockTransformer.class.getName() }
                : new String[0];
    }

    @Override
    public String getModContainerClass() {
        return null;
    }

    @Override
    public String getSetupClass() {
        return null;
    }

    @Override
    public void injectData(Map<String, Object> data) {}

    @Override
    public String getAccessTransformerClass() {
        return null;
    }
}

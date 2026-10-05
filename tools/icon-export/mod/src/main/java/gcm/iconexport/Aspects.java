package gcm.iconexport;

import java.awt.image.BufferedImage;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.util.LinkedHashMap;
import java.util.Map;

import javax.imageio.ImageIO;

import net.minecraft.client.Minecraft;
import net.minecraft.util.ResourceLocation;

/**
 * Thaumcraft's aspects, for essentia icons: Thaumic Energistics puts essentia in the ME network,
 * and OpenComputers names it by the aspect's tag ("ordo").
 *
 * <p>Read by reflection, so the mod still compiles against NEI alone and runs without Thaumcraft.
 *
 * <p>An aspect's texture is a grey glyph that Thaumcraft tints with the aspect's colour when it
 * draws it (UtilsFX.drawTag). That's done here on the image itself rather than through GL: most
 * aspects draw additively, which IconRenderer's black-and-white capture can't take apart (over
 * white, an additive glyph leaves the whole square white). The texture comes through the resource
 * manager, so a resource pack's aspects are what's exported.
 */
final class Aspects {

    private Aspects() {}

    /** Every aspect by tag, in registration order; empty without Thaumcraft. */
    static Map<String, Object> all() {
        try {
            Field field = Class.forName("thaumcraft.api.aspects.Aspect").getField("aspects");
            Map<?, ?> aspects = (Map<?, ?>) field.get(null);
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : aspects.entrySet()) {
                out.put(String.valueOf(e.getKey()), e.getValue());
            }
            return out;
        } catch (ClassNotFoundException e) {
            IconExportMod.LOG.info("No Thaumcraft; no essentia icons.");
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Couldn't read Thaumcraft's aspects; no essentia icons.", t);
        }
        return new LinkedHashMap<>();
    }

    /** The aspect's display name ("Ordo"), or null. */
    static String label(Object aspect) {
        try {
            Object name = aspect.getClass().getMethod("getName").invoke(aspect);
            return name == null ? null : name.toString();
        } catch (Throwable t) {
            return null;
        }
    }

    /** The tinted glyph, scaled to size x size; null if its texture is missing or empty. */
    static BufferedImage render(Object aspect, int size) throws Exception {
        ResourceLocation image = (ResourceLocation) aspect.getClass().getMethod("getImage").invoke(aspect);
        int colour = (Integer) aspect.getClass().getMethod("getColor").invoke(aspect);
        BufferedImage texture;
        try (InputStream in = Minecraft.getMinecraft().getResourceManager().getResource(image).getInputStream()) {
            texture = ImageIO.read(in);
        }
        if (texture == null) {
            return null;
        }
        return tint(texture, colour, size);
    }

    /**
     * Each pixel's colour multiplied by the aspect's, alpha kept, scaled to size by nearest
     * neighbour like every other icon; null if no pixel is visible.
     */
    static BufferedImage tint(BufferedImage texture, int colour, int size) {
        int r = (colour >> 16) & 0xFF, g = (colour >> 8) & 0xFF, b = colour & 0xFF;
        int w = texture.getWidth(), h = texture.getHeight();
        BufferedImage out = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
        boolean visible = false;
        for (int y = 0; y < size; y++) {
            for (int x = 0; x < size; x++) {
                int p = texture.getRGB(x * w / size, y * h / size);
                int alpha = p >>> 24;
                if (alpha == 0) {
                    continue;
                }
                visible = true;
                out.setRGB(x, y, alpha << 24 | ((p >> 16) & 0xFF) * r / 255 << 16
                        | ((p >> 8) & 0xFF) * g / 255 << 8 | (p & 0xFF) * b / 255);
            }
        }
        return visible ? out : null;
    }
}

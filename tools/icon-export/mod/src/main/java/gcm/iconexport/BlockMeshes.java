package gcm.iconexport;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import net.minecraft.block.Block;
import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.RenderBlocks;
import net.minecraft.client.renderer.texture.ITextureObject;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import net.minecraft.client.renderer.texture.TextureMap;
import net.minecraft.util.IIcon;
import net.minecraft.world.World;

import com.gtnewhorizon.gtnhlib.client.renderer.CapturingTessellator;
import com.gtnewhorizon.gtnhlib.client.renderer.TessellatorManager;
import com.gtnewhorizon.gtnhlib.client.renderer.cel.model.quad.ModelQuadViewMutable;

import cpw.mods.fml.relauncher.ReflectionHelper;

/**
 * The geometry the game draws for a block, for Layouts' meshes: the block is drawn with the world's
 * own block renderer while GTNHLib's TessellatorManager captures what it draws instead of drawing
 * it, so it's exactly the game's shapes (frames, pipes, a GregTech machine's overlays), not a cube.
 *
 * <p>Every face is drawn whatever is next to it, and without ambient occlusion: a kind of block is
 * captured once, where it's first placed, and drawn the same everywhere it goes. Each quad's texture
 * coordinates are in the game's block atlas; they're turned into the sprite they fall in and where
 * in it, and the sprite goes in Layouts' own atlas.
 *
 * <p>A mesh is {@code {"s": [sprite name per quad], "p": [x, y, z per vertex], "uv": [u, v per
 * vertex], "c": [rgb per vertex]}}, four vertices a quad, positions in 1/256 of a block from the
 * block's corner, texture coordinates in 1/256 of the sprite. Layouts turns the sprite names into
 * atlas tiles.
 */
final class BlockMeshes {

    /** Positions and texture coordinates are rounded to 1/SCALE. */
    static final int SCALE = 256;

    private final World world;
    private final Map<String, IIcon> icons;
    /** The block atlas's sprites by 16-pixel cell of the atlas they cover. */
    private final Map<Long, List<TextureAtlasSprite>> cells = new HashMap<>();
    private int atlasWidth;
    private int atlasHeight;
    int captured;
    int failed;

    /** icons: where the sprites the meshes use are added, by name, for Layouts' atlas. */
    BlockMeshes(World world, Map<String, IIcon> icons) {
        this.world = world;
        this.icons = icons;
        indexAtlas();
    }

    @SuppressWarnings("unchecked")
    private void indexAtlas() {
        ITextureObject texture = Minecraft.getMinecraft().getTextureManager()
                .getTexture(TextureMap.locationBlocksTexture);
        if (!(texture instanceof TextureMap)) {
            return;
        }
        Map<String, TextureAtlasSprite> sprites = ReflectionHelper.getPrivateValue(TextureMap.class,
                (TextureMap) texture, "mapUploadedSprites", "field_94252_e");
        for (TextureAtlasSprite sprite : sprites.values()) {
            float du = sprite.getMaxU() - sprite.getMinU();
            if (atlasWidth == 0 && du > 0 && sprite.getIconWidth() > 0) {
                atlasWidth = Math.round(sprite.getIconWidth() / du);
                atlasHeight = Math.round(sprite.getIconHeight() / (sprite.getMaxV() - sprite.getMinV()));
            }
        }
        if (atlasWidth == 0) {
            return;
        }
        for (TextureAtlasSprite sprite : sprites.values()) {
            int x0 = sprite.getOriginX() >> 4, y0 = sprite.getOriginY() >> 4;
            int x1 = (sprite.getOriginX() + sprite.getIconWidth() - 1) >> 4;
            int y1 = (sprite.getOriginY() + sprite.getIconHeight() - 1) >> 4;
            for (int cx = x0; cx <= x1; cx++) {
                for (int cy = y0; cy <= y1; cy++) {
                    cells.computeIfAbsent(cell(cx, cy), k -> new ArrayList<>(1)).add(sprite);
                }
            }
        }
    }

    private static long cell(int cx, int cy) {
        return (long) cx << 32 | (cy & 0xFFFFFFFFL);
    }

    /** The sprite the atlas coordinates fall in, or null. */
    private TextureAtlasSprite spriteAt(float u, float v) {
        int px = (int) Math.floor(u * atlasWidth), py = (int) Math.floor(v * atlasHeight);
        List<TextureAtlasSprite> candidates = cells.get(cell(px >> 4, py >> 4));
        if (candidates == null) {
            return null;
        }
        for (TextureAtlasSprite s : candidates) {
            if (px >= s.getOriginX() && px < s.getOriginX() + s.getIconWidth() && py >= s.getOriginY()
                    && py < s.getOriginY() + s.getIconHeight()) {
                return s;
            }
        }
        return null;
    }

    /**
     * The block at x, y, z as the game draws it (see the class comment), or null if nothing was or
     * it couldn't be drawn. Never throws: a block that can't be captured is drawn as a cube.
     */
    Map<String, Object> capture(Block block, int x, int y, int z) {
        if (atlasWidth == 0) {
            return null;
        }
        CapturingTessellator capturing = null;
        try {
            capturing = TessellatorManager.startCapturingAndGet();
            return draw(capturing, block, x, y, z);
        } catch (Throwable e) {
            failed++;
            IconExportMod.LOG.debug("Couldn't draw {}: {}", block, e.toString());
            stopQuietly();
            return null;
        } finally {
            // The quads are pooled, and the next capture won't start while any are held.
            if (capturing != null) {
                capturing.clearQuads();
            }
        }
    }

    private Map<String, Object> draw(CapturingTessellator t, Block block, int x, int y, int z) {
        Minecraft mc = Minecraft.getMinecraft();
        int ambientOcclusion = mc.gameSettings.ambientOcclusion;
        mc.gameSettings.ambientOcclusion = 0;
        RenderBlocks renderer = new RenderBlocks(world);
        renderer.renderAllFaces = true;
        try {
            t.startDrawingQuads();
            t.setTranslation(-x, -y, -z);
            try {
                renderer.renderBlockByRenderType(block, x, y, z);
            } finally {
                t.setTranslation(0, 0, 0);
                t.draw();
            }
        } finally {
            mc.gameSettings.ambientOcclusion = ambientOcclusion;
        }
        List<ModelQuadViewMutable> quads = TessellatorManager.stopCapturingToPooledQuads();

        List<String> sprites = new ArrayList<>();
        List<Integer> positions = new ArrayList<>();
        List<Integer> uvs = new ArrayList<>();
        List<Integer> colours = new ArrayList<>();
        for (ModelQuadViewMutable quad : quads) {
            if (quad.getVertexCount() != 4) {
                continue;
            }
            float cu = 0, cv = 0;
            for (int i = 0; i < 4; i++) {
                cu += quad.getTexU(i) / 4;
                cv += quad.getTexV(i) / 4;
            }
            TextureAtlasSprite sprite = spriteAt(cu, cv);
            if (sprite == null || sprite.getIconName() == null) {
                continue;
            }
            icons.putIfAbsent(sprite.getIconName(), sprite);
            sprites.add(sprite.getIconName());
            float du = sprite.getMaxU() - sprite.getMinU(), dv = sprite.getMaxV() - sprite.getMinV();
            for (int i = 0; i < 4; i++) {
                positions.add(Math.round(quad.getX(i) * SCALE));
                positions.add(Math.round(quad.getY(i) * SCALE));
                positions.add(Math.round(quad.getZ(i) * SCALE));
                uvs.add(Math.round((quad.getTexU(i) - sprite.getMinU()) / du * SCALE));
                uvs.add(Math.round((quad.getTexV(i) - sprite.getMinV()) / dv * SCALE));
                // The tessellator's colour is ABGR in memory order: red in the low byte.
                int c = quad.getColor(i);
                colours.add((c & 0xFF) << 16 | (c >> 8 & 0xFF) << 8 | (c >> 16 & 0xFF));
            }
        }
        if (sprites.isEmpty()) {
            return null;
        }
        captured++;
        Map<String, Object> mesh = new LinkedHashMap<>();
        mesh.put("s", sprites);
        mesh.put("p", positions);
        mesh.put("uv", uvs);
        mesh.put("c", colours);
        return mesh;
    }

    private static void stopQuietly() {
        try {
            if (TessellatorManager.isCurrentlyCapturing()) {
                TessellatorManager.stopCapturingToPooledQuads();
            }
        } catch (Throwable ignored) {
            // Nothing captured to give back.
        }
    }
}

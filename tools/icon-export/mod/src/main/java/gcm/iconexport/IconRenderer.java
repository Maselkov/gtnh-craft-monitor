package gcm.iconexport;

import java.awt.image.BufferedImage;
import java.lang.reflect.Field;
import java.nio.ByteBuffer;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;

import net.minecraft.client.Minecraft;
import net.minecraft.client.entity.EntityClientPlayerMP;
import net.minecraft.client.gui.Gui;
import net.minecraft.client.renderer.EntityRenderer;
import net.minecraft.client.renderer.OpenGlHelper;
import net.minecraft.client.renderer.RenderHelper;
import net.minecraft.client.renderer.Tessellator;
import net.minecraft.client.renderer.texture.DynamicTexture;
import net.minecraft.client.renderer.texture.TextureMap;
import net.minecraft.client.renderer.tileentity.TileEntityRendererDispatcher;
import net.minecraft.client.shader.Framebuffer;
import net.minecraft.item.ItemStack;
import net.minecraft.util.IIcon;
import net.minecraftforge.client.IItemRenderer;
import net.minecraftforge.client.MinecraftForgeClient;
import net.minecraftforge.fluids.FluidStack;

import org.lwjgl.BufferUtils;
import org.lwjgl.opengl.GL11;
import org.lwjgl.opengl.GL12;
import org.lwjgl.opengl.GL20;
import org.lwjgl.opengl.GL30;

import codechicken.nei.ItemStackSet;
import codechicken.nei.guihook.GuiContainerManager;

/**
 * Renders item and fluid icons into an offscreen framebuffer.
 *
 * <p>The projection, lighting and the use of NEI's {@code drawItem} follow NESQL-Exporter's
 * Renderer (github.com/D-Cysteine/nesql-exporter), which produced the icons this project has
 * always shipped. Going through NEI matters: it applies the same render hooks NEI's item panel
 * does, which is what makes GregTech's runtime-composited material icons come out right.
 *
 * <p>NESQL rendered inside a world; this runs at the main menu, so a few things a world would
 * normally set up are done here instead (see {@link #whiteOutLightmap()},
 * {@link #giveTileEntityRenderersTextures()} and {@link #resetState()}).
 *
 * <p>Must only be used on the client thread, between {@link #begin()} and {@link #end()}.
 */
final class IconRenderer {

    private final int size;
    private final ByteBuffer pixelBuffer;
    private final int[] pixels;
    private final Gui gui = new Gui();
    private final ItemStackSet neiRenderErrors = neiRenderErrors();
    private final EntityClientPlayerMP standInPlayer = standInPlayer();
    private Framebuffer framebuffer;
    private int attribDepth;

    IconRenderer(int size) {
        this.size = size;
        this.pixelBuffer = BufferUtils.createByteBuffer(size * size * 4);
        this.pixels = new int[size * size];
    }

    static boolean framebuffersAvailable() {
        return OpenGlHelper.isFramebufferEnabled();
    }

    void begin() {
        if (framebuffer == null) {
            framebuffer = new Framebuffer(size, size, true);
            whiteOutLightmap();
        }
        giveTileEntityRenderersTextures();
        framebuffer.bindFramebuffer(true);
        OpenGlHelper.func_153171_g(GL30.GL_READ_FRAMEBUFFER, framebuffer.framebufferObject);

        GL11.glMatrixMode(GL11.GL_PROJECTION);
        GL11.glPushMatrix();
        GL11.glLoadIdentity();
        GL11.glOrtho(0.0, 1.0, 1.0, 0.0, -100.0, 100.0);
        double scale = 1 / 16.0;
        GL11.glScaled(scale, scale, scale);
        GL11.glMatrixMode(GL11.GL_MODELVIEW);
        GL11.glPushMatrix();
        GL11.glLoadIdentity();

        RenderHelper.enableGUIStandardItemLighting();
        GL11.glEnable(GL12.GL_RESCALE_NORMAL);
        attribDepth = GL11.glGetInteger(GL11.GL_ATTRIB_STACK_DEPTH);
    }

    void end() {
        resetTessellator();
        RenderHelper.disableStandardItemLighting();
        GL11.glDisable(GL12.GL_RESCALE_NORMAL);
        GL11.glColor4f(1f, 1f, 1f, 1f);
        GL11.glMatrixMode(GL11.GL_PROJECTION);
        GL11.glPopMatrix();
        GL11.glMatrixMode(GL11.GL_MODELVIEW);
        GL11.glPopMatrix();
        framebuffer.unbindFramebuffer();
        Minecraft.getMinecraft().getFramebuffer().bindFramebuffer(true);
    }

    void destroy() {
        if (framebuffer != null) {
            framebuffer.deleteFramebuffer();
            framebuffer = null;
        }
    }

    /**
     * Returns null if nothing visible was drawn.
     *
     * @throws IllegalStateException if the item's renderer threw. NEI catches that itself and
     *         draws a fire block in its place, which must not end up as the item's icon. Outside a
     *         world this is mostly renderers that read the player or the world (the bow, for one).
     */
    BufferedImage renderItem(ItemStack stack) {
        try {
            return renderItemOnce(stack);
        } catch (IllegalStateException failed) {
            if (standInPlayer == null) {
                throw failed;
            }
        }
        // Some renderers only fail because there's no player at the main menu. GTNHLib's cosmic
        // shader (Eternal Singularity, Avaritia's infinity gear) reads the player's tick count,
        // for one. Give those a second try with a blank player object in place. Renderers that
        // need more than that still fail; nothing else sees the stand-in.
        Minecraft mc = Minecraft.getMinecraft();
        neiRenderErrors.removeAll(Collections.singletonList(stack));
        // The cosmic shader steps its texture animation by this; AnimationClock sets the rest.
        standInPlayer.ticksExisted = ExportClock.tick();
        mc.thePlayer = standInPlayer;
        try {
            return renderItemOnce(stack);
        } finally {
            mc.thePlayer = null;
        }
    }

    private BufferedImage renderItemOnce(ItemStack stack) {
        resetState();
        clear();
        reseedRenderer(stack);
        try {
            GuiContainerManager.drawItem(0, 0, stack);
        } finally {
            resetTessellator();
            unwindAttribStack();
        }
        if (neiRenderErrors != null && neiRenderErrors.contains(stack)) {
            throw new IllegalStateException("its renderer threw (NEI drew its error placeholder)");
        }
        return readImage();
    }

    /** Returns null if the fluid has no icon or nothing visible was drawn. */
    BufferedImage renderFluid(FluidStack stack) {
        IIcon icon = stack.getFluid().getIcon(stack);
        if (icon == null) {
            return null;
        }
        resetState();
        clear();
        // A flat, unlit quad, whatever the previous item left enabled: with the item lighting
        // still on, a fluid came out darker or not depending on what was drawn before it.
        GL11.glDisable(GL11.GL_LIGHTING);
        GL11.glEnable(GL11.GL_BLEND);
        OpenGlHelper.glBlendFunc(GL11.GL_SRC_ALPHA, GL11.GL_ONE_MINUS_SRC_ALPHA, GL11.GL_ONE, GL11.GL_ZERO);
        try {
            // Some fluids don't bake their colour into the icon, so blend it in.
            int colour = stack.getFluid().getColor(stack);
            GL11.glColor3ub((byte) ((colour >> 16) & 0xFF), (byte) ((colour >> 8) & 0xFF), (byte) (colour & 0xFF));
            Minecraft.getMinecraft().getTextureManager().bindTexture(TextureMap.locationBlocksTexture);
            gui.drawTexturedModelRectFromIcon(0, 0, icon, 16, 16);
        } finally {
            GL11.glColor4f(1f, 1f, 1f, 1f);
            resetTessellator();
            RenderHelper.enableGUIStandardItemLighting();
        }
        return readImage();
    }

    /**
     * The state a GUI normally has when it draws items, re-established per icon: outside a world
     * nothing else resets it (the lightmap unit and fog in particular), and item renderers can
     * leave state behind that ruins every later icon. Avaritia's infinity items, for one, leave
     * their shader bound, after which everything draws as a flat silhouette.
     */
    private static void resetState() {
        GL20.glUseProgram(0);
        Minecraft.getMinecraft().entityRenderer.disableLightmap(0);
        OpenGlHelper.setActiveTexture(OpenGlHelper.defaultTexUnit);
        GL11.glMatrixMode(GL11.GL_TEXTURE);
        GL11.glLoadIdentity();
        GL11.glMatrixMode(GL11.GL_MODELVIEW);
        GL11.glDisable(GL11.GL_TEXTURE_GEN_S);
        GL11.glDisable(GL11.GL_TEXTURE_GEN_T);
        GL11.glDisable(GL11.GL_TEXTURE_GEN_R);
        GL11.glDisable(GL11.GL_TEXTURE_GEN_Q);
        GL11.glTexEnvi(GL11.GL_TEXTURE_ENV, GL11.GL_TEXTURE_ENV_MODE, GL11.GL_MODULATE);
        GL11.glDisable(GL11.GL_FOG);
        GL11.glEnable(GL11.GL_TEXTURE_2D);
        GL11.glColor4f(1f, 1f, 1f, 1f);
    }

    /**
     * Item and block renderers that draw with lighting sample the lightmap texture, which is only
     * computed each frame in a world. At the main menu it's never been filled in, and everything
     * drawn through it comes out as a dark silhouette. Full brightness is what a GUI in a lit world
     * gets anyway. The lightmap is EntityRenderer's only DynamicTexture, so find it by type.
     */
    private static void whiteOutLightmap() {
        try {
            for (Field f : EntityRenderer.class.getDeclaredFields()) {
                if (f.getType() == DynamicTexture.class) {
                    f.setAccessible(true);
                    DynamicTexture lightmap = (DynamicTexture) f.get(Minecraft.getMinecraft().entityRenderer);
                    Arrays.fill(lightmap.getTextureData(), 0xFFFFFFFF);
                    lightmap.updateDynamicTexture();
                    return;
                }
            }
            IconExportMod.LOG.warn("No lightmap texture found; lit items may render dark.");
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Couldn't brighten the lightmap; lit items may render dark.", t);
        }
    }

    /**
     * Chests, ender chests, skulls and other items drawn by a TileEntitySpecialRenderer bind their
     * textures through TileEntityRendererDispatcher's texture manager, and skip the bind when it's
     * null. The dispatcher only gets it in cacheActiveRenderInfo, when a world renders, so at the
     * main menu those models came out wearing whatever was bound last (the block atlas: noise).
     */
    private static void giveTileEntityRenderersTextures() {
        TileEntityRendererDispatcher dispatcher = TileEntityRendererDispatcher.instance;
        if (dispatcher.field_147553_e == null) {
            dispatcher.field_147553_e = Minecraft.getMinecraft().getTextureManager();
        }
    }

    /**
     * A renderer that throws between glPushAttrib and glPopAttrib leaves the attribute stack one
     * deeper. NEI catches the exception but can't unwind GL state, so without this the stack
     * would eventually overflow and every later item that pushes attributes would fail too.
     */
    private void unwindAttribStack() {
        for (int i = 0; i < 32 && GL11.glGetInteger(GL11.GL_ATTRIB_STACK_DEPTH) > attribDepth; i++) {
            GL11.glPopAttrib();
        }
    }

    private void clear() {
        GL11.glClearColor(0f, 0f, 0f, 0f);
        GL11.glClearDepth(1D);
        GL11.glClear(GL11.GL_COLOR_BUFFER_BIT | GL11.GL_DEPTH_BUFFER_BIT);
    }

    private BufferedImage readImage() {
        pixelBuffer.clear();
        GL11.glReadPixels(0, 0, size, size, GL12.GL_BGRA, GL11.GL_UNSIGNED_BYTE, pixelBuffer);
        // BGRA bytes in a native (little-endian) buffer read back as ARGB ints.
        pixelBuffer.asIntBuffer().get(pixels);

        boolean visible = false;
        for (int p : pixels) {
            if ((p >>> 24) != 0) {
                visible = true;
                break;
            }
        }
        if (!visible) {
            return null;
        }

        // GL's origin is bottom-left; flip rows while copying out.
        BufferedImage image = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
        for (int y = 0; y < size; y++) {
            image.setRGB(0, y, size, 1, pixels, (size - 1 - y) * size, size);
        }
        return image;
    }

    /**
     * A player object with every field at its default, made without running a constructor (a
     * real one needs a world and a network connection). Null if the JVM won't allow that.
     */
    private static EntityClientPlayerMP standInPlayer() {
        try {
            Field unsafeField = Class.forName("sun.misc.Unsafe").getDeclaredField("theUnsafe");
            unsafeField.setAccessible(true);
            Object unsafe = unsafeField.get(null);
            return (EntityClientPlayerMP) unsafe.getClass()
                    .getMethod("allocateInstance", Class.class)
                    .invoke(unsafe, EntityClientPlayerMP.class);
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Can't make a stand-in player; items that need one won't render.", t);
            return null;
        }
    }

    /** Random fields of each item renderer class (and its superclasses), found once. */
    private static final Map<Class<?>, List<Field>> RANDOM_FIELDS = new HashMap<>();

    /**
     * Item renderers that jitter with {@code java.util.Random} on every draw (the halos of Avaritia
     * and Universal Singularities, GT's Infinity and glitch effects) come out different every time,
     * so the export couldn't tell them from each other or capture their animated textures. Their
     * Random fields get the same seed before every render, which holds the jitter still.
     */
    private static void reseedRenderer(ItemStack stack) {
        IItemRenderer renderer = MinecraftForgeClient.getItemRenderer(stack, IItemRenderer.ItemRenderType.INVENTORY);
        if (renderer == null) {
            return;
        }
        List<Field> fields = RANDOM_FIELDS.computeIfAbsent(renderer.getClass(), IconRenderer::randomFields);
        for (Field field : fields) {
            try {
                Object random = field.get(Modifier.isStatic(field.getModifiers()) ? null : renderer);
                if (random != null) {
                    ((Random) random).setSeed(0x6763_6D00L);
                }
            } catch (Throwable ignored) {}
        }
    }

    private static List<Field> randomFields(Class<?> type) {
        List<Field> fields = new ArrayList<>();
        for (Class<?> c = type; c != null && c != Object.class; c = c.getSuperclass()) {
            for (Field field : c.getDeclaredFields()) {
                if (Random.class.isAssignableFrom(field.getType())) {
                    try {
                        field.setAccessible(true);
                        fields.add(field);
                    } catch (Throwable ignored) {}
                }
            }
        }
        return fields;
    }

    /** NEI's private record of stacks whose rendering threw; null if this NEI version lacks it. */
    private static ItemStackSet neiRenderErrors() {
        try {
            Field f = GuiContainerManager.class.getDeclaredField("renderingErrorItems");
            f.setAccessible(true);
            return (ItemStackSet) f.get(null);
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Can't see NEI's render errors; broken items will get NEI's fire icon.", t);
            return null;
        }
    }

    private static Field tessellatorDrawing;

    /** A renderer that throws mid-draw leaves the tessellator "drawing", breaking every later draw. */
    private static void resetTessellator() {
        try {
            if (tessellatorDrawing == null) {
                for (String name : new String[] { "isDrawing", "field_78415_z" }) {
                    try {
                        Field f = Tessellator.class.getDeclaredField(name);
                        f.setAccessible(true);
                        tessellatorDrawing = f;
                        break;
                    } catch (NoSuchFieldException ignored) {}
                }
            }
            if (tessellatorDrawing != null && tessellatorDrawing.getBoolean(Tessellator.instance)) {
                tessellatorDrawing.setBoolean(Tessellator.instance, false);
            }
        } catch (Throwable ignored) {}
    }
}

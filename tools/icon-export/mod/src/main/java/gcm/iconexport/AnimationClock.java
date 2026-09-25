package gcm.iconexport;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.List;

import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.OpenGlHelper;
import net.minecraft.client.renderer.texture.ITextureObject;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import net.minecraft.client.renderer.texture.TextureMap;
import net.minecraft.client.renderer.texture.TextureUtil;
import net.minecraft.client.resources.data.AnimationMetadataSection;
import net.minecraft.util.ResourceLocation;

import org.lwjgl.opengl.GL11;

import cpw.mods.fml.relauncher.ReflectionHelper;

/**
 * Puts every animated texture at a chosen tick of its animation, so icons can be rendered frame by
 * frame and the still icons always show the same frame.
 *
 * <p>Animated textures are atlas sprites with {@code .mcmeta} animation data (GT materials, lava,
 * Thaumcraft items, ...). The game advances them in real time, even at the main menu, which is
 * why still exports used to catch each one at an arbitrary frame. This sets each sprite's frame
 * directly, as {@link TextureAtlasSprite#updateAnimation()} would after that many ticks, and
 * keeps the sprite's own counters in step so the game's next tick carries on from there. Callers
 * seek before every render batch, since the game ticks in between.
 *
 * <p>Only sprites of the vanilla class are handled. Subclasses (the compass and clock, which spin
 * at random without a world, and a few mods' own) animate however they like and are left alone;
 * {@link ExportDriver} notices when those make an icon change and keeps it still.
 */
final class AnimationClock {

    private static final ResourceLocation[] ATLASES = { TextureMap.locationBlocksTexture,
            TextureMap.locationItemsTexture };

    private static final class Anim {

        final TextureAtlasSprite sprite;
        final List<int[][]> frames;
        final AnimationMetadataSection meta;
        final int[] frameTimes;
        final int cycle;

        Anim(TextureAtlasSprite sprite, List<int[][]> frames, AnimationMetadataSection meta) {
            this.sprite = sprite;
            this.frames = frames;
            this.meta = meta;
            int count = meta.getFrameCount() == 0 ? frames.size() : meta.getFrameCount();
            frameTimes = new int[count];
            int total = 0;
            for (int i = 0; i < count; i++) {
                frameTimes[i] = Math.max(1, meta.getFrameTimeSingle(i));
                total += frameTimes[i];
            }
            cycle = total;
        }
    }

    private final List<List<Anim>> atlases = new ArrayList<>();
    private final List<TextureMap> maps = new ArrayList<>();
    private final Field frameCounter;
    private final Field tickCounter;
    private int spriteCount;

    private AnimationClock(Field frameCounter, Field tickCounter) {
        this.frameCounter = frameCounter;
        this.tickCounter = tickCounter;
    }

    /** Null if this game's textures can't be reached; the export then stays still-only. */
    @SuppressWarnings("unchecked")
    static AnimationClock create() {
        try {
            Field framesField = ReflectionHelper
                    .findField(TextureAtlasSprite.class, "framesTextureData", "field_110976_a");
            Field metaField = ReflectionHelper
                    .findField(TextureAtlasSprite.class, "animationMetadata", "field_110982_k");
            AnimationClock clock = new AnimationClock(
                    ReflectionHelper.findField(TextureAtlasSprite.class, "frameCounter", "field_110973_g"),
                    ReflectionHelper.findField(TextureAtlasSprite.class, "tickCounter", "field_110983_h"));
            for (ResourceLocation location : ATLASES) {
                ITextureObject texture = Minecraft.getMinecraft().getTextureManager().getTexture(location);
                List<Anim> anims = new ArrayList<>();
                clock.maps.add(texture instanceof TextureMap ? (TextureMap) texture : null);
                if (texture instanceof TextureMap) {
                    List<TextureAtlasSprite> sprites = ReflectionHelper
                            .getPrivateValue(TextureMap.class, (TextureMap) texture, "listAnimatedSprites", "field_94258_i");
                    for (TextureAtlasSprite sprite : sprites) {
                        if (sprite.getClass() != TextureAtlasSprite.class) {
                            continue;
                        }
                        List<int[][]> frames = (List<int[][]>) framesField.get(sprite);
                        AnimationMetadataSection meta = (AnimationMetadataSection) metaField.get(sprite);
                        if (meta != null && frames != null && frames.size() > 1) {
                            anims.add(new Anim(sprite, frames, meta));
                        }
                    }
                }
                clock.atlases.add(anims);
                clock.spriteCount += anims.size();
            }
            IconExportMod.LOG.info(
                    "{} animated textures ({} blocks, {} items).",
                    clock.spriteCount,
                    clock.atlases.get(0).size(),
                    clock.atlases.get(1).size());
            return clock;
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Can't control texture animations; animated icons will be still frames.", t);
            return null;
        }
    }

    int spriteCount() {
        return spriteCount;
    }

    /** Every animated texture as it is {@code tick} ticks into its animation. */
    void seek(int tick) {
        apply(anim -> {
            int remaining = tick % anim.cycle;
            int frame = 0;
            while (remaining >= anim.frameTimes[frame]) {
                remaining -= anim.frameTimes[frame];
                frame++;
            }
            return new int[] { frame, remaining };
        });
    }

    /**
     * Every animated texture on the first of its frames that looks different from its first
     * frame's image, where it has one. Any icon drawn with an animated texture then differs from
     * its {@code seek(0)} render, which is how the driver finds animated icons.
     */
    void seekChanged() {
        apply(anim -> {
            int first = anim.meta.getFrameIndex(0);
            for (int frame = 1; frame < anim.frameTimes.length; frame++) {
                if (anim.meta.getFrameIndex(frame) != first) {
                    return new int[] { frame, 0 };
                }
            }
            return new int[] { 0, 0 };
        });
    }

    private interface Target {

        /** {frame counter, tick counter} for the sprite. */
        int[] of(Anim anim);
    }

    /**
     * Binds each atlas with GL directly and puts the previous binding back afterwards. Going
     * through TextureManager.bindTexture isn't safe here: something in the pack caches the bound
     * texture, and a bind it thinks is redundant gets skipped, which sent the block atlas's
     * frames (lava, every fluid, animated blocks) to whatever texture happened to be bound.
     */
    private void apply(Target target) {
        OpenGlHelper.setActiveTexture(OpenGlHelper.defaultTexUnit);
        int previous = GL11.glGetInteger(GL11.GL_TEXTURE_BINDING_2D);
        try {
            for (int i = 0; i < ATLASES.length; i++) {
                List<Anim> anims = atlases.get(i);
                if (anims.isEmpty()) {
                    continue;
                }
                GL11.glBindTexture(GL11.GL_TEXTURE_2D, maps.get(i).getGlTextureId());
                for (Anim anim : anims) {
                    int[] want = target.of(anim);
                    int current = frameCounter.getInt(anim.sprite);
                    // updateAnimation() keeps the uploaded image in step with the frame counter,
                    // so only upload when the image has to change.
                    int shown = anim.meta.getFrameIndex(current % anim.frameTimes.length);
                    int wanted = anim.meta.getFrameIndex(want[0]);
                    if (shown != wanted && wanted >= 0 && wanted < anim.frames.size()) {
                        TextureAtlasSprite s = anim.sprite;
                        TextureUtil.uploadTextureMipmap(
                                anim.frames.get(wanted),
                                s.getIconWidth(),
                                s.getIconHeight(),
                                s.getOriginX(),
                                s.getOriginY(),
                                false,
                                false);
                    }
                    frameCounter.setInt(anim.sprite, want[0]);
                    tickCounter.setInt(anim.sprite, want[1]);
                }
            }
        } catch (IllegalAccessException e) {
            throw new IllegalStateException(e);
        } finally {
            GL11.glBindTexture(GL11.GL_TEXTURE_2D, previous);
        }
    }
}

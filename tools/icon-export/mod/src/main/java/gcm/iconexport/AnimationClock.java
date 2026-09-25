package gcm.iconexport;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import net.minecraft.client.Minecraft;
import net.minecraft.client.renderer.OpenGlHelper;
import net.minecraft.client.renderer.texture.ITextureObject;
import net.minecraft.client.renderer.texture.TextureAtlasSprite;
import net.minecraft.client.renderer.texture.TextureClock;
import net.minecraft.client.renderer.texture.TextureCompass;
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
 * <p>GregTech keeps a second clock of its own, a client tick counter that drives its spinning
 * transcendent metal and colour-cycling materials. It's set to the same tick, so those are captured
 * as animations too instead of coming out at whatever angle or colour the menu had reached.
 *
 * <p>Sprite subclasses animate their own way (Botania's InterpolatedIcon blends between frames),
 * so for those the sprite is set one tick short and its own {@code updateAnimation()} takes the
 * last step. The compass and clock are left alone: without a world they spin at random, and
 * {@link ExportDriver} notices that and keeps their icons still.
 */
final class AnimationClock {

    private static final ResourceLocation[] ATLASES = { TextureMap.locationBlocksTexture,
            TextureMap.locationItemsTexture };

    private static final class Anim {

        final TextureAtlasSprite sprite;
        final List<int[][]> frames;
        final AnimationMetadataSection meta;
        final int[] frameTimes;
        /** A subclass: stepped onto each tick by its own updateAnimation(). */
        final boolean ownStep;

        Anim(TextureAtlasSprite sprite, List<int[][]> frames, AnimationMetadataSection meta) {
            this.ownStep = sprite.getClass() != TextureAtlasSprite.class;
            this.sprite = sprite;
            this.frames = frames;
            this.meta = meta;
            int count = meta.getFrameCount() == 0 ? frames.size() : meta.getFrameCount();
            frameTimes = new int[count];
            for (int i = 0; i < count; i++) {
                frameTimes[i] = Math.max(1, meta.getFrameTimeSingle(i));
            }
        }
    }

    private final List<List<Anim>> atlases = new ArrayList<>();
    private final List<TextureMap> maps = new ArrayList<>();
    /** GT's client proxy and its counter fields; null without GT. */
    private Object gtClient;
    private Field gtAnimationTick;
    private Field gtRenderTickTime;
    /** GTNHLib's cosmic shader texture atlas and its per-sprite animation state; null without it. */
    private Object cosmicAtlas;
    private Field cosmicLastUpdate;
    private final List<Object> cosmicSprites = new ArrayList<>();
    private final List<int[]> cosmicFrameTimes = new ArrayList<>();
    private Field cosmicTickCounter, cosmicFrameCounter, cosmicIndex;
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
                        if (sprite instanceof TextureCompass || sprite instanceof TextureClock) {
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
            clock.findGregTechClock();
            clock.findCosmicClock();
            Map<String, Integer> subclasses = new TreeMap<>();
            for (List<Anim> anims : clock.atlases) {
                for (Anim anim : anims) {
                    if (anim.ownStep) {
                        subclasses.merge(anim.sprite.getClass().getName(), 1, Integer::sum);
                    }
                }
            }
            IconExportMod.LOG.info(
                    "{} animated textures ({} blocks, {} items; stepped by their own class: {}).",
                    clock.spriteCount,
                    clock.atlases.get(0).size(),
                    clock.atlases.get(1).size(),
                    subclasses);
            return clock;
        } catch (Throwable t) {
            IconExportMod.LOG.warn("Can't control texture animations; animated icons will be still frames.", t);
            return null;
        }
    }

    /**
     * {@code GTMod.clientProxy()}'s {@code mAnimationTick}, which GT adds its partial tick
     * ({@code renderTickTime}) to in {@code getAnimationRenderTicks()}.
     */
    private void findGregTechClock() {
        try {
            Class<?> mod = Class.forName("gregtech.GTMod", false, AnimationClock.class.getClassLoader());
            Object client = mod.getMethod("clientProxy").invoke(null);
            Field tick = client.getClass().getDeclaredField("mAnimationTick");
            Field partial = client.getClass().getDeclaredField("renderTickTime");
            tick.setAccessible(true);
            partial.setAccessible(true);
            gtClient = client;
            gtAnimationTick = tick;
            gtRenderTickTime = partial;
            IconExportMod.LOG.info("Controlling GregTech's animation ticks too.");
        } catch (Throwable t) {
            IconExportMod.LOG.info("No GregTech animation clock ({}); its animated items stay still.", t.toString());
        }
    }

    /**
     * GTNHLib's cosmic shader animates its own texture atlas, one step whenever the player's
     * {@code ticksExisted} changes (the stand-in player's follows ExportClock). Its per-sprite
     * state ({@code SpriteAnimationMetadata}) is set to the tick before the one wanted, and the
     * atlas made to update once more, which steps it onto that tick.
     */
    private void findCosmicClock() {
        try {
            ClassLoader loader = AnimationClock.class.getClassLoader();
            Class<?> shader = Class.forName(
                    "com.gtnewhorizon.gtnhlib.client.renderer.postprocessing.shaders.UniversiumShader",
                    false,
                    loader);
            Object instance = shader.getMethod("getInstance").invoke(null);
            Field atlasField = shader.getDeclaredField("textureAtlas");
            atlasField.setAccessible(true);
            Object atlas = atlasField.get(instance);
            Field metadataField = atlas.getClass().getDeclaredField("animationMetadata");
            Field lastUpdate = atlas.getClass().getDeclaredField("lastTextureUpdate");
            metadataField.setAccessible(true);
            lastUpdate.setAccessible(true);
            Class<?> sprite = Class.forName(
                    "com.gtnewhorizon.gtnhlib.client.renderer.textures.SpriteAnimationMetadata",
                    false,
                    loader);
            cosmicTickCounter = sprite.getField("tickCounter");
            cosmicFrameCounter = sprite.getField("frameCounter");
            cosmicIndex = sprite.getField("index");
            Field meta = sprite.getField("metadata");
            for (Object animation : (Object[]) metadataField.get(atlas)) {
                AnimationMetadataSection section = (AnimationMetadataSection) meta.get(animation);
                int[] times = new int[Math.max(1, section.getFrameCount())];
                for (int i = 0; i < times.length; i++) {
                    times[i] = Math.max(1, section.getFrameTimeSingle(i));
                }
                cosmicSprites.add(animation);
                cosmicFrameTimes.add(times);
            }
            cosmicAtlas = atlas;
            cosmicLastUpdate = lastUpdate;
            IconExportMod.LOG.info("Controlling the cosmic shader's {} animated textures too.", cosmicSprites.size());
        } catch (Throwable t) {
            IconExportMod.LOG.info("No cosmic shader animation to control ({}).", t.toString());
        }
    }

    private void setCosmicTick(int tick) {
        if (cosmicAtlas == null) {
            return;
        }
        try {
            for (int i = 0; i < cosmicSprites.size(); i++) {
                int[] times = cosmicFrameTimes.get(i);
                Object animation = cosmicSprites.get(i);
                int[] at = frameAt(times, tick - 1);
                AnimationMetadataSection section = (AnimationMetadataSection) animation.getClass()
                        .getField("metadata")
                        .get(animation);
                cosmicFrameCounter.setInt(animation, at[0]);
                cosmicTickCounter.setInt(animation, at[1]);
                cosmicIndex.setInt(animation, section.getFrameIndex(at[0]));
            }
            cosmicLastUpdate.setInt(cosmicAtlas, Integer.MIN_VALUE);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }

    /** {frame, ticks into it} for an animation with these frame times, tick ticks in (any sign). */
    private static int[] frameAt(int[] frameTimes, int tick) {
        int cycle = 0;
        for (int time : frameTimes) {
            cycle += time;
        }
        int remaining = ((tick % cycle) + cycle) % cycle;
        int frame = 0;
        while (remaining >= frameTimes[frame]) {
            remaining -= frameTimes[frame];
            frame++;
        }
        return new int[] { frame, remaining };
    }

    /**
     * The clocks that aren't texture animations: GT's, Botania's, the cosmic shader's and the
     * system clock's.
     */
    private void setOtherClocks(int tick) {
        ExportClock.set(tick);
        setGregTechTick(tick);
        setBotaniaTick(tick);
        setCosmicTick(tick);
    }

    private Field botaniaTicks, botaniaPartial, botaniaTotal;
    private boolean botaniaLooked;

    /**
     * Botania keeps a client tick counter of its own ({@code ClientTickHandler.ticksInGame}, plus
     * the partial tick, summed in {@code total}), which its rainbow blocks (bifrost, prismarine,
     * shimmerrock) colour themselves by.
     */
    private void setBotaniaTick(int tick) {
        if (!botaniaLooked) {
            botaniaLooked = true;
            try {
                Class<?> handler = Class.forName(
                        "vazkii.botania.client.core.handler.ClientTickHandler",
                        false,
                        AnimationClock.class.getClassLoader());
                botaniaTicks = handler.getField("ticksInGame");
                botaniaPartial = handler.getField("partialTicks");
                botaniaTotal = handler.getField("total");
                IconExportMod.LOG.info("Controlling Botania's animation ticks too.");
            } catch (Throwable t) {
                IconExportMod.LOG.info("No Botania animation clock ({}).", t.toString());
            }
        }
        if (botaniaTicks == null) {
            return;
        }
        try {
            botaniaTicks.setInt(null, tick);
            botaniaPartial.setFloat(null, 0f);
            botaniaTotal.setFloat(null, tick);
        } catch (IllegalAccessException e) {
            throw new IllegalStateException(e);
        }
    }

    private void setGregTechTick(long tick) {
        if (gtClient == null) {
            return;
        }
        try {
            gtAnimationTick.setLong(gtClient, tick);
            gtRenderTickTime.setFloat(gtClient, 0f);
        } catch (IllegalAccessException e) {
            throw new IllegalStateException(e);
        }
    }

    int spriteCount() {
        return spriteCount;
    }

    /** Every animated texture as it is {@code tick} ticks into its animation. */
    void seek(int tick) {
        setOtherClocks(tick);
        apply(anim -> frameAt(anim.frameTimes, tick));
    }

    /**
     * Every animated texture on the first of its frames that looks different from its first
     * frame's image, where it has one. Any icon drawn with an animated texture then differs from
     * its {@code seek(0)} render, which is how the driver finds animated icons.
     */
    void seekChanged() {
        // 10 ticks turns a spinning ingot 35 degrees; GT's colour cycles and the cosmic shader
        // move on visibly too.
        setOtherClocks(10);
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

    /**
     * A subclass sprite onto {frame, ticks}: its counters one tick short of that (the frame's
     * image uploaded, as vanilla keeps it), then its own updateAnimation() for the last tick.
     * False if its updateAnimation() threw; it's then left to itself.
     */
    private boolean stepOnto(Anim anim, int[] want) throws IllegalAccessException {
        int frame = want[0];
        int ticks = want[1] - 1;
        if (ticks < 0) {
            frame = (frame + anim.frameTimes.length - 1) % anim.frameTimes.length;
            ticks = anim.frameTimes[frame] - 1;
        }
        int index = anim.meta.getFrameIndex(frame);
        if (index >= 0 && index < anim.frames.size()) {
            TextureAtlasSprite s = anim.sprite;
            TextureUtil.uploadTextureMipmap(
                    anim.frames.get(index),
                    s.getIconWidth(),
                    s.getIconHeight(),
                    s.getOriginX(),
                    s.getOriginY(),
                    false,
                    false);
        }
        frameCounter.setInt(anim.sprite, frame);
        tickCounter.setInt(anim.sprite, ticks);
        try {
            anim.sprite.updateAnimation();
            return true;
        } catch (Throwable t) {
            IconExportMod.LOG.warn("{} can't be stepped; its animation stays uncontrolled.", anim.sprite.getIconName(), t);
            return false;
        }
    }

    private interface Target {

        /** {frame counter, tick counter} for the sprite. */
        int[] of(Anim anim);
    }

    /**
     * Binds each atlas with GL directly and puts the previous binding back afterwards, so the
     * renderers that run next find the texture state as they left it.
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
                for (Iterator<Anim> it = anims.iterator(); it.hasNext();) {
                    Anim anim = it.next();
                    int[] want = target.of(anim);
                    if (anim.ownStep) {
                        if (!stepOnto(anim, want)) {
                            it.remove();
                        }
                        continue;
                    }
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

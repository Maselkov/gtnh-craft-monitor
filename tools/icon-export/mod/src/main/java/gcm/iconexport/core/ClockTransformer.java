package gcm.iconexport.core;


import net.minecraft.launchwrapper.IClassTransformer;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.AbstractInsnNode;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.MethodInsnNode;
import org.objectweb.asm.tree.MethodNode;

/**
 * Points renderers' clock calls at {@code gcm.iconexport.ExportClock}; see ExportClock for why.
 *
 * <p>Touched: GTNHLib's cosmic shader, vanilla's RenderItem (the glint, frozen), and item renderers
 * from mods: classes that implement {@code IItemRenderer} themselves, and GT's item renderer
 * package (its renderers mostly inherit the interface). Nothing else in Minecraft, so the game
 * loop's own timing is untouched. Only calls to the clock are changed.
 */
public final class ClockTransformer implements IClassTransformer {

    private static final Logger LOG = LogManager.getLogger("GCMIconExport");
    private static final String CLOCK = "gcm/iconexport/ExportClock";

    /** A static clock call and what replaces it. */
    private static final class Redirect {

        final String owner;
        final String[] names;
        final String replacement;

        Redirect(String owner, String replacement, String... names) {
            this.owner = owner;
            this.names = names;
            this.replacement = replacement;
        }

        boolean matches(MethodInsnNode call) {
            if (call.getOpcode() != Opcodes.INVOKESTATIC || !call.owner.equals(owner) || !call.desc.equals("()J")) {
                return false;
            }
            for (String name : names) {
                if (call.name.equals(name)) {
                    return true;
                }
            }
            return false;
        }
    }

    private static final Redirect SYSTEM_MILLIS = new Redirect(
            "java/lang/System",
            "animationMillis",
            "currentTimeMillis");
    private static final Redirect MC_TIME_ANIMATION = new Redirect(
            "net/minecraft/client/Minecraft",
            "animationSystemTime",
            "func_71386_F",
            "getSystemTime");
    private static final Redirect MC_TIME_GLINT = new Redirect(
            "net/minecraft/client/Minecraft",
            "glintSystemTime",
            "func_71386_F",
            "getSystemTime");

    private static final Redirect SYSTEM_NANOS = new Redirect("java/lang/System", "animationNanos", "nanoTime");
    private static final Redirect LWJGL_TIME = new Redirect("org/lwjgl/Sys", "animationSysTime", "getTime");

    private static final Redirect[] ANIMATION = { SYSTEM_MILLIS, SYSTEM_NANOS, MC_TIME_ANIMATION, LWJGL_TIME };
    private static final Redirect[] GLINT = { MC_TIME_GLINT };

    private static final String ITEM_RENDERER = "net/minecraftforge/client/IItemRenderer";
    private static final String GT_ITEM_RENDERERS = "gregtech.common.render.items.";
    private static final String UNIVERSIUM_SHADER = "com.gtnewhorizon.gtnhlib.client.renderer.postprocessing.shaders.UniversiumShader";

    /** The clock calls to redirect in a class, or null to leave it alone. */
    private static Redirect[] redirectsFor(String transformedName, byte[] bytes) {
        if (transformedName.equals("net.minecraft.client.renderer.entity.RenderItem")) {
            return GLINT;
        }
        if (transformedName.startsWith("net.minecraft.") || transformedName.startsWith("gcm.iconexport.")) {
            return null;
        }
        if (transformedName.equals(UNIVERSIUM_SHADER) || transformedName.startsWith(GT_ITEM_RENDERERS)) {
            return ANIMATION;
        }
        for (String iface : new ClassReader(bytes).getInterfaces()) {
            if (iface.equals(ITEM_RENDERER)) {
                return ANIMATION;
            }
        }
        return null;
    }

    @Override
    public byte[] transform(String name, String transformedName, byte[] bytes) {
        if (bytes == null) {
            return null;
        }
        try {
            Redirect[] redirects = redirectsFor(transformedName, bytes);
            if (redirects == null) {
                return bytes;
            }
            ClassNode node = new ClassNode();
            new ClassReader(bytes).accept(node, 0);
            int count = 0;
            for (MethodNode method : node.methods) {
                for (AbstractInsnNode insn = method.instructions.getFirst(); insn != null; insn = insn.getNext()) {
                    if (!(insn instanceof MethodInsnNode)) {
                        continue;
                    }
                    MethodInsnNode call = (MethodInsnNode) insn;
                    for (Redirect redirect : redirects) {
                        if (redirect.matches(call)) {
                            call.owner = CLOCK;
                            call.name = redirect.replacement;
                            call.itf = false;
                            count++;
                            break;
                        }
                    }
                }
            }
            if (count == 0) {
                return bytes;
            }
            LOG.info("Export clock: redirected {} clock call(s) in {}.", count, transformedName);
            ClassWriter writer = new ClassWriter(0);
            node.accept(writer);
            return writer.toByteArray();
        } catch (Throwable t) {
            LOG.warn("Export clock: couldn't transform {}; it keeps the real clock.", transformedName, t);
            return bytes;
        }
    }
}

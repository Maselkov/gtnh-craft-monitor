package gcm.iconexport.core;

import java.nio.charset.StandardCharsets;

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
 * Points every {@code Keyboard.isKeyDown(int)} call at {@code gcm.iconexport.ExportKeys}, so the
 * export can read tooltips as if Shift were held; see ExportKeys. Vanilla's
 * {@code GuiScreen.isShiftKeyDown()}, which most mods use, is one of those calls. LWJGL itself
 * isn't transformed (the launcher excludes it), so the call sites are what changes. Any
 * {@code .../input/Keyboard} owner: lwjgl3ify may have moved mods' calls to its own class first.
 */
public final class KeyTransformer implements IClassTransformer {

    private static final Logger LOG = LogManager.getLogger("GCMIconExport");
    private static final String KEYS = "gcm/iconexport/ExportKeys";
    private static final byte[] METHOD_NAME = "isKeyDown".getBytes(StandardCharsets.UTF_8);

    @Override
    public byte[] transform(String name, String transformedName, byte[] bytes) {
        if (bytes == null || transformedName.startsWith("gcm.iconexport.") || !contains(bytes, METHOD_NAME)) {
            return bytes;
        }
        try {
            ClassNode node = new ClassNode();
            new ClassReader(bytes).accept(node, 0);
            int count = 0;
            for (MethodNode method : node.methods) {
                for (AbstractInsnNode insn = method.instructions.getFirst(); insn != null; insn = insn.getNext()) {
                    if (!(insn instanceof MethodInsnNode)) {
                        continue;
                    }
                    MethodInsnNode call = (MethodInsnNode) insn;
                    if (call.getOpcode() == Opcodes.INVOKESTATIC && call.owner.endsWith("/input/Keyboard")
                            && call.name.equals("isKeyDown") && call.desc.equals("(I)Z")) {
                        call.owner = KEYS;
                        call.itf = false;
                        count++;
                    }
                }
            }
            if (count == 0) {
                return bytes;
            }
            ClassWriter writer = new ClassWriter(0);
            node.accept(writer);
            return writer.toByteArray();
        } catch (Throwable t) {
            LOG.warn("Export keys: couldn't transform {}; it reads the real keyboard.", transformedName, t);
            return bytes;
        }
    }

    /** Whether the class file mentions the name at all: most don't, and aren't parsed. */
    private static boolean contains(byte[] bytes, byte[] needle) {
        outer:
        for (int i = 0; i <= bytes.length - needle.length; i++) {
            for (int j = 0; j < needle.length; j++) {
                if (bytes[i + j] != needle[j]) {
                    continue outer;
                }
            }
            return true;
        }
        return false;
    }
}

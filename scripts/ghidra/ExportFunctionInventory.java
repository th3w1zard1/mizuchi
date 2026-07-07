// Export per-function inventory rows as JSONL for Mizuchi source-parity pipelines.
// @category Export
// @keybinding
// @menupath
// @toolbar

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.mem.MemoryBlock;

import com.google.gson.Gson;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.util.LinkedHashMap;
import java.util.Map;

public class ExportFunctionInventory extends GhidraScript {

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            printerr("usage: ExportFunctionInventory.java <output.jsonl>");
            return;
        }

        File out = new File(args[0]);
        File parent = out.getParentFile();
        if (parent != null) {
            parent.mkdirs();
        }

        Gson gson = new Gson();
        Listing listing = currentProgram.getListing();
        Memory memory = currentProgram.getMemory();
        FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
        int count = 0;

        try (BufferedWriter writer = new BufferedWriter(new FileWriter(out))) {
            while (functions.hasNext()) {
                if (monitor.isCancelled()) {
                    break;
                }
                Function function = functions.next();
                Address entry = function.getEntryPoint();
                long entryOffset = entry.getOffset();
                String entryHex = String.format("%08x", entryOffset);
                AddressSetView body = function.getBody();
                int bodyBytes = (int) body.getNumAddresses();

                MemoryBlock block = memory.getBlock(entry);
                String section = block != null ? block.getName() : "";

                int instructionCount = 0;
                InstructionIterator instructions = listing.getInstructions(body, true);
                while (instructions.hasNext()) {
                    instructions.next();
                    instructionCount++;
                }

                StringBuilder hex = new StringBuilder(bodyBytes * 2);
                for (Address addr : body.getAddresses(true)) {
                    try {
                        byte value = memory.getByte(addr);
                        hex.append(String.format("%02x", value & 0xff));
                    } catch (MemoryAccessException e) {
                        // Skip unreadable bytes inside the function body.
                    }
                }

                Map<String, Object> row = new LinkedHashMap<>();
                row.put("name", function.getName());
                row.put("entry", entryHex);
                row.put("entryOffset", entryOffset);
                row.put("section", section);
                row.put("bodyBytes", bodyBytes);
                row.put("instructionCount", instructionCount);
                row.put("bytes", hex.toString());

                writer.write(gson.toJson(row));
                writer.newLine();
                count++;
            }
        }

        println("ExportFunctionInventory wrote " + count + " functions to " + out.getAbsolutePath());
    }
}

# GBA Web Emulator with Debugger — Implementation Plan

## 1. Architecture

### 1.1 Extending `shared/arm-emulator` via Dependency Injection

The existing `shared/arm-emulator` is a Thumb-only CPU execution engine tightly coupled to `GbaMemory`. To support both the behavioral test runner (headless, Thumb-only, stub-based) and a full GBA emulator (ARM+Thumb, PPU, APU, DMA, timers, interrupts), we introduce interfaces at the boundary between CPU and everything else.

**Current coupling problem:**

```
ThumbCpu → GbaMemory (concrete class, direct field access)
```

**Target architecture:**

```
ArmCpu → MemoryBus (interface)
           ├── TestMemoryBus    (behavioral tests: write logging, stubs)
           └── GbaSystemBus     (full emulator: dispatches to PPU, APU, DMA, timers, MMIO)
```

#### 1.1.1 `MemoryBus` interface

```typescript
interface MemoryBus {
  read8(address: number): number;
  read16(address: number): number;
  read32(address: number): number;
  write8(address: number, value: number): void;
  write16(address: number, value: number): void;
  write32(address: number, value: number): void;
}
```

`GbaMemory` already implements these methods. The refactor is:

1. Extract the `MemoryBus` interface
2. Make `GbaMemory` implement it explicitly
3. Change `ThumbCpu` constructor from `GbaMemory` to `MemoryBus`
4. The existing behavioral test code continues to pass `GbaMemory` (which satisfies `MemoryBus`)

This is the **only breaking change** to `shared/arm-emulator`. All existing tests continue to work.

#### 1.1.2 CPU extension: ARM mode

The existing `ThumbCpu` only decodes 16-bit Thumb instructions. A real GBA emulator needs 32-bit ARM mode for:

- BIOS execution
- Interrupt handlers (CPU switches to ARM mode on IRQ)
- Performance-critical routines in IWRAM

**Approach:** Rename `ThumbCpu` to `ArmCpu` and add ARM instruction decoding alongside existing Thumb decoding. The `step()` method checks the T bit in CPSR to determine which decoder to use.

```typescript
class ArmCpu {
  // ... existing Thumb fields ...
  mode: CpuMode; // usr, fiq, irq, svc, abt, und, sys

  step(): boolean {
    if (this.cpsr.t) {
      return this.#executeThumb(); // existing code, moved to private method
    } else {
      return this.#executeArm(); // new ARM decoder
    }
  }
}
```

The existing Thumb decoder is preserved exactly. ARM mode adds ~40 instruction patterns (data processing, multiply, load/store, branch, block transfer, SWI, coprocessor stubs).

**Backward compatibility:** The behavioral test runner continues to use the same class. It calls `run()` which calls `step()` which calls the Thumb decoder (since test functions are Thumb code with T=1). The ARM decoder is simply unused in test mode.

#### 1.1.3 Debug hooks

```typescript
interface DebugHooks {
  onInstructionPre?(address: number, instruction: number): DebugAction;
  onInstructionPost?(address: number, instruction: number): void;
  onMemoryRead?(address: number, size: 1 | 2 | 4, value: number): void;
  onMemoryWrite?(address: number, size: 1 | 2 | 4, value: number): void;
  onBreakpoint?(address: number): DebugAction;
}

type DebugAction = 'continue' | 'break';
```

The CPU accepts an optional `DebugHooks` object. When absent (behavioral tests, full-speed play), zero overhead — no hook check code runs. When present (debugger active), each instruction/memory access calls through the hooks.

Implementation: the `step()` method gains a fast path:

```typescript
step(): boolean {
  if (this.#hooks) {
    return this.#stepWithHooks();
  }
  return this.#stepFast();
}
```

#### 1.1.4 Interrupt and exception support

ARM7TDMI has 7 exception types. For GBA, the relevant ones are:

- **IRQ**: Hardware interrupt (VBlank, HBlank, timer, DMA, keypad, etc.)
- **SWI**: Software interrupt (BIOS calls)
- **Undefined instruction**: For coprocessor emulation stubs

Exception handling:

1. Save CPSR to SPSR of target mode
2. Switch to target mode (ARM, specific register bank)
3. Set PC to exception vector
4. Disable IRQs (set I bit)

The behavioral test runner never triggers exceptions (functions return via `bx lr` to sentinel). The full emulator needs them for BIOS calls and hardware interrupts.

### 1.2 JSON contract for behavioral test results

The behavioral test results are the bridge between `shared/arm-emulator` (producer) and the webapp (consumer). The webapp never reaches into emulator internals — it receives JSON and renders it.

#### 1.2.1 Existing schema (unchanged)

```typescript
// Already exists in types.ts — these remain the same
interface ExecutionResult {
  registers: number[]; // Serialized from Uint32Array
  cpsr: CpsrFlags;
  memoryWrites: MemoryWrite[];
  externalCalls: ExternalCall[];
  instructionsExecuted: number;
  completed: boolean;
}

interface ComparisonResult {
  allMatch: boolean;
  totalScenarios: number;
  matchCount: number;
  mismatchCount: number;
  scenarios: ScenarioResult[];
  summary: string;
}
```

#### 1.2.2 New: Execution trace for debugger visualization

For the behavioral test diff view, we need instruction-level traces showing where divergence occurs:

```typescript
/** A snapshot of CPU state at one instruction */
interface InstructionTrace {
  /** Program counter (address of this instruction) */
  pc: number;
  /** Raw instruction word (16-bit Thumb or 32-bit ARM) */
  instruction: number;
  /** Disassembled mnemonic (e.g., "ldr r0, [r1, #4]") */
  mnemonic: string;
  /** Register values AFTER execution */
  registers: number[];
  /** CPSR flags after execution */
  cpsr: CpsrFlags;
  /** Memory writes performed by this instruction (0 or 1 typically) */
  writes: MemoryWrite[];
  /** External call if this was a BL to a stub */
  externalCall?: ExternalCall;
}

/** Full execution trace for one function invocation */
interface ExecutionTrace {
  /** Ordered list of executed instructions */
  instructions: InstructionTrace[];
  /** Final result (same as ExecutionResult) */
  result: ExecutionResult;
}

/** Side-by-side comparison with divergence info */
interface DivergenceReport {
  /** Scenario input description */
  inputDescription: string;
  /** Index of first diverging instruction (-1 if no divergence) */
  divergenceIndex: number;
  /** What diverged at that point */
  divergenceType: 'register' | 'memory_write' | 'external_call' | 'control_flow' | 'completion';
  /** Human-readable description of the divergence */
  divergenceDescription: string;
  /** Original function trace */
  originalTrace: ExecutionTrace;
  /** Compiled function trace */
  compiledTrace: ExecutionTrace;
}
```

**Trace recording is opt-in.** The comparator gains an optional `traceEnabled: boolean` in `ComparisonConfig`. When false (default, for pipeline runs), no traces are recorded — same performance as today. When true (for debugger visualization), full traces are captured.

The `ComparisonResult` gains an optional field:

```typescript
interface ComparisonResult {
  // ... existing fields ...
  /** Detailed divergence reports (only when trace enabled) */
  divergenceReports?: DivergenceReport[];
}
```

#### 1.2.3 Contract enforcement

The JSON schema is defined in `shared/arm-emulator/types.ts`. The webapp imports only types (type-only imports enforced by dependency-cruiser). Data flows as:

```
comparator.ts → ComparisonResult (JSON-serializable)
    ↓ (serialized to file or passed via API)
webapp → renders divergence view from ComparisonResult
```

The webapp never imports `ThumbCpu`, `GbaMemory`, or any class — only type definitions.

### 1.3 Module boundaries

```
src/shared/arm-emulator/     ← CPU core + MemoryBus interface + types
src/shared/gba-emulator/     ← Full GBA: PPU, APU, DMA, timers, GbaSystemBus, scheduler
src/ui/gba-debugger/         ← Web application
```

**Dependency-cruiser rules to add:**

```javascript
// shared/gba-emulator can only import from shared/arm-emulator and itself
{
  name: 'gba-emulator-import-restrictions',
  severity: 'error',
  from: { path: '^src/shared/gba-emulator/' },
  to: {
    path: '^src/',
    pathNot: [
      '^src/shared/gba-emulator/',
      '^src/shared/arm-emulator/',
    ],
  },
}

// ui/gba-debugger can import from shared layers and itself
{
  name: 'ui-gba-debugger-import-restrictions',
  severity: 'error',
  from: { path: '^src/ui/gba-debugger/' },
  to: {
    path: '^src/',
    pathNot: [
      '^src/ui/gba-debugger/',
      '^src/ui/shared/',
      '^src/shared/arm-emulator/',
      '^src/shared/gba-emulator/',
      '^src/shared/mizuchi-db/',
      '^src/shared/config\\.ts$',
    ],
  },
}

// No other module may import from gba-emulator or gba-debugger
// (arm-emulator must NOT import gba-emulator)
{
  name: 'no-gba-emulator-reverse-deps',
  severity: 'error',
  from: {
    path: '^src/',
    pathNot: [
      '^src/shared/gba-emulator/',
      '^src/ui/gba-debugger/',
    ],
  },
  to: {
    path: '^src/shared/gba-emulator/',
  },
}
```

---

## 2. GBA Subsystem Implementation Plan

### 2.1 Overview

The full GBA hardware lives in `src/shared/gba-emulator/`. It implements the subsystems that `shared/arm-emulator` defines interfaces for.

```
src/shared/gba-emulator/
├── index.ts                 # Public API
├── gba.ts                   # Main GBA system (coordinates all subsystems)
├── system-bus.ts            # GbaSystemBus implements MemoryBus
├── scheduler.ts             # Event scheduler (cycle-accurate timing)
├── ppu/
│   ├── ppu.ts              # PPU core: scanline rendering, mode dispatch
│   ├── backgrounds.ts      # Text/affine BG rendering
│   ├── sprites.ts          # OAM sprite rendering
│   └── compositor.ts       # Layer priority, windowing, blending
├── apu/
│   ├── apu.ts              # APU core: channel mixing
│   ├── psg.ts              # PSG channels 1-4
│   └── direct-sound.ts     # DirectSound A/B FIFO management
├── dma.ts                   # DMA controller (4 channels)
├── timers.ts                # Timer subsystem (4 timers)
├── input.ts                 # Keypad input register
├── interrupts.ts            # Interrupt controller (IE, IF, IME)
├── bios.ts                  # HLE BIOS implementations
└── types.ts                 # GBA-specific types
```

### 2.2 Event Scheduler

The scheduler is the heart of the emulator. GBA hardware is timing-sensitive: the CPU, PPU, timers, and DMA all share the same clock (16.78 MHz) and interact based on cycle counts.

```typescript
interface ScheduledEvent {
  cyclesUntilFire: number;
  callback: () => void;
  id: EventId;
}

class Scheduler {
  schedule(id: EventId, cycles: number, callback: () => void): void;
  cancel(id: EventId): void;
  tick(cycles: number): void; // Advance time, fire events
  nextEventCycles(): number; // Cycles until next event
}
```

The main loop:

```
while running:
  cyclesToNext = scheduler.nextEventCycles()
  cpu.run(cyclesToNext)      // Execute CPU for that many cycles
  scheduler.tick(cyclesToNext) // Fire the event
```

Key events: HBlank (every 1232 cycles), VBlank (after scanline 160), timer overflow, DMA completion.

### 2.3 PPU (Picture Processing Unit)

**Scope for target ROMs:**

Sonic Advance 3 and Klonoa: Empire of Dreams are 2D platformers. They primarily use:

- Mode 0 (4 text BG layers for parallax scrolling)
- Possibly Mode 1 (3 layers, one affine for rotation effects)
- Sprites (128 OBJ entries in OAM)
- Alpha blending for transparency effects
- Windowing for HUD overlays

**Phase 1 (minimum viable):**

- Mode 0: 4 text background layers with scrolling
- Sprite rendering: all sizes, 4bpp/8bpp, priority
- Palette handling (256 BG + 256 OBJ colors)
- Basic priority sorting (BG layers + sprites)

**Phase 2 (target ROM compatibility):**

- Mode 1: affine BG2 with rotation/scaling
- Alpha blending (EVA/EVB registers)
- Brightness increase/decrease
- Window 0/1 and OBJ window
- Mosaic effect
- Sprite affine transformations (32 matrices)

**Phase 3 (broad compatibility, stretch):**

- Mode 2 (dual affine BGs)
- Bitmap modes 3/4/5 (for games that use them)
- OBJ semi-transparency
- Sprite priority edge cases

**Rendering approach:** Scanline-based, matching real hardware. For each of the 160 visible scanlines:

1. Render each enabled BG layer for this scanline
2. Render visible sprites for this scanline
3. Composite layers by priority with blending/windowing
4. Write the 240-pixel scanline to the framebuffer

Output: a `Uint32Array(240 * 160)` RGBA framebuffer, updated scanline by scanline, completed at VBlank.

### 2.4 APU (Audio Processing Unit)

**Scope:** Most GBA games use the m4a/Sappy sound engine which does software mixing and outputs to DirectSound channels A/B. PSG channels are secondary.

**Phase 1:**

- DirectSound A/B: FIFO queues (32 bytes each), timer-triggered sample pop, DMA refill
- Sample output buffer: ring buffer of signed 8-bit samples at emulated rate
- Resampling to 48kHz for Web Audio output

**Phase 2:**

- PSG channels 1-4 (square, wave, noise)
- Channel mixing with SOUNDCNT_L/H volume control
- SOUNDBIAS handling

**Phase 3 (stretch):**

- Accurate PSG sweep/envelope timing
- PWM resolution modes

### 2.5 DMA Controller

All 4 channels needed from the start — DMA is essential for audio (FIFO refill) and video (OAM/VRAM transfers).

- Immediate, VBlank, HBlank, and Special (FIFO) start modes
- Priority handling (DMA0 > DMA1 > DMA2 > DMA3)
- CPU halt during transfer
- Repeat mode for sound FIFO
- 16-bit and 32-bit transfer modes

### 2.6 Timers

All 4 timers needed from the start — timers drive audio sample rates.

- Prescaler values (F/1, F/64, F/256, F/1024)
- Cascade mode (timer N increments when timer N-1 overflows)
- IRQ on overflow
- Reload value on overflow

### 2.7 Input

Simple register at `0x04000130` (KEYINPUT). 10 buttons: A, B, Select, Start, Right, Left, Up, Down, R, L. Active-low (0 = pressed). Straightforward to implement.

### 2.8 Interrupt Controller

- IME (0x04000208): Master interrupt enable
- IE (0x04000200): Individual interrupt enable flags
- IF (0x04000202): Interrupt request flags (write 1 to acknowledge)
- IRQ sources: VBlank, HBlank, VCount, Timer 0-3, DMA 0-3, Keypad, Game Pak

When an enabled interrupt fires: CPU switches to IRQ mode (ARM), saves state, jumps to `0x00000018` (or the game's IRQ handler via `0x03007FFC`).

### 2.9 BIOS

**HLE (High-Level Emulation)** approach: instead of running the real BIOS ROM (which would require the copyrighted BIOS dump), we intercept SWI calls and implement them in TypeScript.

Priority BIOS calls (needed for most games):

- `SWI 0x05` VBlankIntrWait — most common; wait for VBlank via halt
- `SWI 0x06` Div / `SWI 0x07` DivArm — integer division
- `SWI 0x08` Sqrt — integer square root
- `SWI 0x0B` CpuSet / `SWI 0x0C` CpuFastSet — memory copy/fill
- `SWI 0x11` LZ77UnCompWram / `SWI 0x12` LZ77UnCompVram — LZ77 decompression
- `SWI 0x0F` ObjAffineSet — compute sprite affine matrices

Lower priority (implement as needed):

- `SWI 0x00` SoftReset
- `SWI 0x01` RegisterRamReset
- `SWI 0x02` Halt / `SWI 0x04` IntrWait
- `SWI 0x09` ArcTan / `SWI 0x0A` ArcTan2
- `SWI 0x10` BitUnPack
- `SWI 0x13` HuffUnComp
- `SWI 0x14`/`0x15` RLUnComp

### 2.10 GbaSystemBus

The `GbaSystemBus` implements `MemoryBus` and dispatches reads/writes to the appropriate subsystem based on address:

```typescript
class GbaSystemBus implements MemoryBus {
  ram: { ewram: ArrayBuffer; iwram: ArrayBuffer };
  ppu: Ppu; // owns VRAM, palette, OAM
  apu: Apu; // owns sound registers
  dma: DmaController;
  timers: TimerController;
  input: InputController;
  interrupts: InterruptController;
  rom: ArrayBuffer;

  read32(address: number): number {
    switch ((address >>> 24) & 0xff) {
      case 0x00:
        return this.biosRead(address);
      case 0x02:
        return this.ewramRead32(address);
      case 0x03:
        return this.iwramRead32(address);
      case 0x04:
        return this.mmioRead32(address); // dispatches to PPU/APU/DMA/timers/etc.
      case 0x05:
        return this.ppu.paletteRead32(address);
      case 0x06:
        return this.ppu.vramRead32(address);
      case 0x07:
        return this.ppu.oamRead32(address);
      case 0x08:
      case 0x09: /* ... */
      case 0x0e:
        return this.savRead(address);
      default:
        return 0; // open bus
    }
  }
}
```

The MMIO dispatch (0x04000000 range) is a large switch on the register address, routing to the owning subsystem. This is where most of the wiring complexity lives.

---

## 3. Emulator Reference Analysis

### 3.1 gbajs (endrift)

**What to adopt:**

- **Canvas 2D for rendering**: Proven sufficient for 240x160. Use `putImageData()` for the framebuffer, CSS `image-rendering: pixelated` for upscaling.
- **`requestAnimationFrame` main loop**: Natural frame pacing tied to browser refresh rate. Execute one frame's worth of CPU cycles (~280,896) per rAF callback.
- **Web Audio API for sound**: AudioWorklet (modernized from gbajs's ScriptProcessorNode).
- **ROM loading via FileReader API**: Drag-and-drop or file input → ArrayBuffer.

**What to improve on:**

- gbajs is a monolithic JS file. We use a modular TypeScript architecture with clear subsystem boundaries.
- gbajs has no debugger. We add a full debug UI.
- gbajs used ScriptProcessorNode (deprecated). We use AudioWorklet with a SharedArrayBuffer ring buffer.

### 3.2 mGBA

**What to adopt:**

- **Event scheduler architecture**: mGBA's scheduler-driven design where the CPU runs until the next hardware event. This is the standard approach for cycle-accurate timing without checking timing conditions on every instruction.
- **Scanline-based PPU rendering**: Render one scanline at a time during HBlank, matching real hardware timing. Enables mid-frame register changes (raster effects).
- **HLE BIOS**: mGBA supports both real BIOS and HLE. We go HLE-only to avoid requiring users to supply a BIOS dump.
- **Subsystem separation**: mGBA's clean separation of `video.c`, `audio.c`, `dma.c`, `timer.c` maps well to our module structure.

**What to simplify:**

- mGBA supports multiple platforms (GBA + GB/GBC) and multiple frontends (Qt, SDL, etc.). We only target GBA in the browser.
- mGBA supports many save types and hardware variants. We start with the save types needed for our two target ROMs.

### 3.3 NO$GBA (debugger reference)

**Features to include in v1:**

- Register viewer (all ARM registers, CPSR decoded, current mode)
- Disassembly view around current PC
- Memory viewer/editor (hex dump with ASCII, jump to address)
- Breakpoints (address breakpoints on execution)
- Step / Step Over / Run to breakpoint
- I/O register viewer (DISPCNT, BGxCNT, etc. with decoded fields)

**Features for v2:**

- Memory write watchpoints
- Conditional breakpoints
- Tile/BG/sprite viewer (VRAM visualization)
- Call stack view
- Execution profiler (cycle counts per function)

**Features to skip (not needed for decompilation work):**

- Source-level debugging with DWARF (we use mizuchi-db instead)
- Real-time register editing during execution
- Emulation speed control (frame skip, fast forward)

### 3.4 NanoBoyAdvance

**Reference value:** Cycle-accurate timing documentation. If we encounter timing-sensitive behavior in the target ROMs, NanoBoyAdvance's source is the reference for exact cycle counts of bus accesses and wait states. Not needed for initial implementation — only consulted if specific timing bugs appear.

---

## 4. Web Technologies

### 4.1 Rendering

**Canvas 2D API** — sufficient and simplest for 240x160.

Setup:

```typescript
const canvas = document.createElement('canvas');
canvas.width = 240;
canvas.height = 160;
canvas.style.imageRendering = 'pixelated';
const ctx = canvas.getContext('2d')!;
const imageData = ctx.createImageData(240, 160);
```

Per frame:

```typescript
// PPU writes RGBA pixels into framebuffer Uint32Array
// Copy to ImageData and paint
new Uint8ClampedArray(imageData.data.buffer).set(new Uint8ClampedArray(framebuffer.buffer));
ctx.putImageData(imageData, 0, 0);
```

Upscaling: CSS `width/height` on the canvas element (e.g., 720x480 for 3x) with `image-rendering: pixelated`. No WebGL needed unless we add shader-based filters later.

### 4.2 Audio

**AudioWorklet** with a ring buffer:

```
Main thread (emulator):
  APU mixes samples at emulated rate (~32768 Hz)
  Writes to SharedArrayBuffer ring buffer

Audio thread (AudioWorklet):
  Reads from ring buffer
  Resamples to AudioContext.sampleRate (48000 Hz)
  Linear interpolation for resampling
  Outputs 128-sample blocks
```

Ring buffer size: 4096 samples (~125ms at 32768 Hz) — enough to absorb frame timing jitter.

Fallback: If SharedArrayBuffer is unavailable (cross-origin isolation not configured), use a regular ArrayBuffer with `postMessage` to the AudioWorklet. Higher latency but functional.

### 4.3 Performance Architecture

**Single-threaded approach (Phase 1):**

- CPU, PPU, APU, DMA, timers all run on the main thread
- `requestAnimationFrame` drives frame execution
- Target: 60fps with 280,896 CPU cycles per frame
- TypeScript/JavaScript should be fast enough — gbajs proved this years ago with a much slower JS engine

**Web Worker approach (Phase 2, if needed):**

- Move emulator core to a Web Worker
- Communicate with main thread via `SharedArrayBuffer` for framebuffer + audio buffer
- UI controls sent via `postMessage`
- Only needed if main-thread emulation can't sustain 60fps (unlikely for our target ROMs)

**No WASM for Phase 1.** TypeScript JIT-compiled by V8 is fast enough for GBA emulation (proven by gbajs and its successors). WASM would add build complexity without meaningful performance gain at this scale.

### 4.4 State Persistence

**IndexedDB** for save states:

- ROM save data (SRAM/Flash/EEPROM) persists across page reloads
- Full emulator snapshots (all registers, RAM, VRAM, PPU state, etc.) for save states
- keyed by ROM hash (CRC32 or SHA-256 of the ROM)

Library: Use the raw IndexedDB API (lightweight) or `idb` package (thin promise wrapper).

### 4.5 Build System

Follow the Decomp Atlas pattern:

- Vite + React
- `vite-plugin-singlefile` for production build
- Tailwind CSS (shared config from `src/ui/shared/`)
- Path aliases: `~`, `@shared`, `@ui-shared`
- TypeScript strict mode
- Dev server with Vite HMR

---

## 5. Debugger Feature Spectrum

### 5.1 Phase 1 — Core Debugger (MVP)

| Feature                 | Description                                                 | Priority  |
| ----------------------- | ----------------------------------------------------------- | --------- |
| **Pause / Resume**      | Stop emulation, inspect state, continue                     | Essential |
| **Step Instruction**    | Execute one instruction, update all views                   | Essential |
| **Step Over**           | Execute until PC passes current instruction (skip BL calls) | Essential |
| **Run to Address**      | Continue until a specific PC is reached                     | Essential |
| **Register Viewer**     | All 16 ARM registers, CPSR flags decoded, current mode      | Essential |
| **Disassembly View**    | 30-50 instructions around PC, labels from mizuchi-db        | Essential |
| **Memory Viewer**       | Hex dump with ASCII, navigate by address, 8/16/32-bit view  | Essential |
| **Address Breakpoints** | Set breakpoints on code addresses, persist across runs      | Essential |
| **Screen View**         | Current framebuffer visible during stepping                 | Essential |

### 5.2 Phase 2 — Enhanced Debugging

| Feature                 | Description                                              | Priority |
| ----------------------- | -------------------------------------------------------- | -------- |
| **Memory Watchpoints**  | Break on read/write to specific address ranges           | High     |
| **I/O Register Viewer** | DISPCNT, BGxCNT, timer registers with decoded bit fields | High     |
| **Call Stack**          | Track BL/BX calls, show function hierarchy               | High     |
| **Function Labels**     | Show function names from mizuchi-db in disassembly       | High     |
| **Search Memory**       | Find byte patterns in RAM                                | Medium   |
| **Tile Viewer**         | Visualize tile data in VRAM (character blocks)           | Medium   |
| **BG Map Viewer**       | Visualize background tile maps with scroll position      | Medium   |
| **Sprite Viewer**       | Show all 128 OAM entries with their tiles                | Medium   |
| **Palette Viewer**      | Show BG and OBJ palettes as color swatches               | Medium   |

### 5.3 Phase 3 — Advanced (Stretch)

| Feature                     | Description                                         | Priority |
| --------------------------- | --------------------------------------------------- | -------- |
| **Conditional Breakpoints** | Break when register/memory matches condition        | Medium   |
| **Execution Profiler**      | Cycle counts per function (like NO$GBA)             | Low      |
| **Trace Log**               | Record last N instructions for post-mortem analysis | Low      |
| **Memory Diff**             | Compare memory state between two snapshots          | Low      |

---

## 6. UI/UX Proposal

### 6.1 Aesthetic Direction

**Inspiration:** VS Code's debug view meets NO$GBA's information density. Dark theme (consistent with existing Mizuchi UIs), monospace fonts for hex/asm, syntax highlighting for disassembly. The existing Tailwind dark theme from `src/ui/shared/styles.css` serves as the base.

**Key principles:**

- Information density when debugging — maximize visible state without overwhelming
- Clean separation between "play" and "debug" modes
- All panels resizable and collapsible
- Keyboard-driven navigation (vim-style shortcuts for stepping, breakpoints)

### 6.2 Wireframe 1: Play Mode

```
┌─────────────────────────────────────────────────────────┐
│  Mizuchi GBA Emulator                    [Debug] [⚙]    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│              ┌──────────────────────┐                   │
│              │                      │                   │
│              │    GBA Screen        │                   │
│              │    (720 x 480)       │                   │
│              │    3x upscaled       │                   │
│              │                      │                   │
│              └──────────────────────┘                   │
│                                                         │
│              [Load ROM]  [Save State]  [Load State]     │
│                                                         │
│   Controls: Arrow keys = D-pad, Z = A, X = B,          │
│   Enter = Start, Backspace = Select, A/S = L/R         │
└─────────────────────────────────────────────────────────┘
```

Minimal chrome. Screen centered and prominent. Control hints at the bottom. The `[Debug]` button enters debug mode.

### 6.3 Wireframe 2: Debug Mode

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Mizuchi GBA Debugger           [▶ Run] [⏸ Pause] [→ Step] [↷ Over]   │
├───────────────┬─────────────────────────────────────┬───────────────────┤
│               │  Disassembly                        │  Registers        │
│  GBA Screen   │  ─────────────────────────          │  ──────────       │
│  (240x160     │  0x08000100  ldr r0, [r1]          │  R0  0x0000001A  │
│   1x in       │  0x08000102  cmp r0, #0    ← PC    │  R1  0x02000100  │
│   debug)      │  0x08000104  beq 0x08000110         │  R2  0x00000000  │
│               │  0x08000106  add r0, #1             │  R3  0x08001000  │
│               │  0x08000108  str r0, [r1]           │  ...             │
│               │  0x0800010A  b 0x08000100           │  SP  0x03007F00  │
│               │  ...                                │  LR  0x08000050  │
│               │                                     │  PC  0x08000102  │
│               │  ● = breakpoint   ← = PC            │                  │
│               │                                     │  CPSR: N=0 Z=0  │
│               │                                     │        C=1 V=0  │
│               │                                     │  Mode: Thumb/USR │
├───────────────┴─────────────────────────────────────┼───────────────────┤
│  Memory Viewer                                      │  I/O Registers    │
│  ─────────────                                      │  ──────────────   │
│  Addr      00 01 02 03 04 05 06 07  ASCII           │  DISPCNT: 0x1140 │
│  02000000  1A 00 00 00 FF 03 00 00  ........        │   Mode: 0        │
│  02000008  00 00 00 00 00 00 00 00  ........        │   BG0: on        │
│  02000010  48 65 6C 6C 6F 00 00 00  Hello...        │   BG1: on        │
│  ...                                                │   OBJ: on        │
│  [Go to: ________]                                  │  BG0CNT: 0x1C08  │
│                                                     │  ...              │
├─────────────────────────────────────────────────────┴───────────────────┤
│  Breakpoints: 0x08000100 ✕  0x08001000 ✕    [+ Add]                    │
└─────────────────────────────────────────────────────────────────────────┘
```

**Layout:** 4-panel grid. Screen (top-left, small), Disassembly (top-center, largest), Registers (top-right), Memory + I/O (bottom). Breakpoints bar at very bottom.

Panels are resizable via drag handles. Any panel can be collapsed to give more space to others.

### 6.4 Wireframe 3: Behavioral Test Comparison View

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Behavioral Test Comparison         [Load JSON] [Scenario: ◄ 3/100 ►]  │
├─────────────────────────────────────────────────────────────────────────┤
│  Input: r0=0x02000100 (EWRAM ptr), r1=0x00000005, r2=0x0, r3=0x0      │
│  Result: MISMATCH — Return value differs (original=0x1A, compiled=0x19) │
├──────────────────────────┬──────────────────────────────────────────────┤
│  Original (target.o)     │  Compiled (your code)                       │
│  ─────────────────       │  ──────────────────                         │
│  0: ldr r0, [r1]         │  0: ldr r0, [r1]                           │
│  1: cmp r0, #0           │  1: cmp r0, #0                             │
│  2: beq → 6              │  2: beq → 6                                │
│  3: add r0, #1      ← ● │  3: add r0, #2      ← ●  ⚠ DIVERGE        │
│  4: str r0, [r1]         │  4: str r0, [r1]                           │
│  5: b → 0                │  5: b → 0                                  │
│  6: bx lr                │  6: bx lr                                  │
│                          │                                             │
│  r0=0x1A r1=0x02000100   │  r0=0x19 r1=0x02000100   ⚠ r0 differs     │
│  Writes: [0x02000100]=26 │  Writes: [0x02000100]=25  ⚠ value differs  │
├──────────────────────────┴──────────────────────────────────────────────┤
│  Timeline:  [=====●=====] Step 3/7: add r0, #1 vs add r0, #2          │
│             ◄◄  ◄  ►  ►►   [Jump to divergence]                       │
└─────────────────────────────────────────────────────────────────────────┘
```

**Features:**

- Side-by-side trace view with synchronized scrolling
- Divergence point highlighted with a warning icon
- Timeline scrubber at bottom to navigate through execution steps
- "Jump to divergence" button snaps to the first difference
- Scenario selector (navigate between test cases)
- Register diff at the bottom of each column (differences highlighted)
- Memory write comparison (address, value pairs)

### 6.5 Wireframe 4: A/B Test Patching View

```
┌─────────────────────────────────────────────────────────────────────────┐
│  A/B Test Mode                          [Select Function ▼] [▶ Run]    │
├─────────────────────────────────┬───────────────────────────────────────┤
│  Original ROM                   │  Patched ROM                         │
│  ┌───────────────────────┐      │  ┌───────────────────────┐           │
│  │                       │      │  │                       │           │
│  │   GBA Screen          │      │  │   GBA Screen          │           │
│  │   (360 x 240)         │      │  │   (360 x 240)         │           │
│  │                       │      │  │                       │           │
│  └───────────────────────┘      │  └───────────────────────┘           │
│                                 │                                      │
│  sub_8085618: original asm      │  sub_8085618: decompiled C           │
│  Calls: 0  Frames: 1204        │  Calls: 0  Frames: 1204             │
├─────────────────────────────────┴───────────────────────────────────────┤
│  Function: sub_8085618 │ Status: Running │ Divergences: 0              │
│                                                                        │
│  [Patched Code]                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ void sub_8085618(int r0, int r1) {                              │   │
│  │     if (r0 > 0) {                                               │   │
│  │         *(int*)(r1) = r0 + 1;                                   │   │
│  │     }                                                           │   │
│  │ }                                                               │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│  [Edit Code]  [Compile & Reload]                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

**How it works:**

- Two emulator instances run in lockstep (frame-synced)
- Left: original ROM, unmodified
- Right: patched ROM — when the CPU enters the selected function's address, execution is redirected to the compiled decompiled code
- Both receive the same input (keyboard events mirrored)
- Bottom panel shows the decompiled C code, with option to edit and recompile (requires backend)
- Divergence counter tracks any differences in observable behavior

### 6.6 Mode Switching

- **Play mode** ↔ **Debug mode**: Toggle via `[Debug]` button or `F12` key. Debug mode pauses emulation and shows all panels. Returning to play mode resumes.
- **Behavioral test view**: Separate route/tab. Loads a JSON file independently of any running emulation.
- **A/B test mode**: Accessed from debug mode via a menu. Requires a running ROM and a function selection from mizuchi-db.

---

## 7. Behavioral Test Visualization Design

### 7.1 JSON Schema

The webapp loads a `ComparisonResult` JSON (produced by the behavioral plugin or exported from the pipeline report). For the trace-level visualization, the enhanced schema from Section 1.2.2 is used.

**File format:** A standalone JSON file with this top-level structure:

```typescript
interface BehavioralTestFile {
  version: 1;
  functionName: string;
  originalObjectPath: string;
  compiledObjectPath: string;
  config: ComparisonConfig;
  result: ComparisonResult;
  /** Present only when trace was enabled */
  divergenceReports?: DivergenceReport[];
}
```

**Two visualization modes:**

1. **Summary mode** (always available): Shows the `ComparisonResult` — match count, scenario table with pass/fail, diff strings for failures. This works with the existing pipeline output (no trace needed).

2. **Trace mode** (when `divergenceReports` present): Full side-by-side instruction trace with divergence highlighting. Requires running the comparison with `traceEnabled: true`.

### 7.2 Trace Generation

Add a new function to the comparator:

```typescript
export async function compareFunctionWithTrace(
  originalObjPath: string,
  compiledObjPath: string,
  functionName: string,
  config?: Partial<ComparisonConfig & { traceEnabled: true }>,
): Promise<BehavioralTestFile>;
```

This is separate from the pipeline's `compareFunction()` to keep the pipeline fast (no trace overhead). The trace function is called from:

- The webapp (via backend API) when the user wants to inspect a specific function
- A CLI command (`mizuchi behavioral-trace --function sub_8085618`)

### 7.3 Diff View Design

The trace viewer is the core of the behavioral test visualization:

- **Synchronized dual-pane scroll**: Left = original, Right = compiled. Scrolling one pane scrolls the other.
- **Instruction alignment**: Instructions are displayed by execution order (step 0, 1, 2...), not by address. If both sides execute the same number of instructions, they align naturally. If counts differ (e.g., different loop iterations), alignment breaks — show a visual indicator.
- **Divergence markers**: A colored gutter on both sides. Green = matching, red = diverging. First divergence gets a prominent marker.
- **Register sidebar**: Shows register values at the selected instruction. Differences between original and compiled are highlighted in red.
- **Memory write panel**: Below the traces, shows a table of memory writes from both sides, aligned by address. Differences highlighted.
- **Timeline**: A horizontal bar at the bottom. Width proportional to total instructions. A cursor shows current position. Red marks show divergence points. Click to jump.

### 7.4 Interaction Flow

1. User loads a behavioral test JSON file (drag-and-drop or file picker)
2. Summary view shows: X/Y scenarios passed, table of failed scenarios
3. User clicks a failed scenario → trace view opens (if traces available)
4. Trace view starts at the divergence point
5. User can step forward/backward through the trace
6. User can switch to a different scenario via the scenario selector
7. User can jump between divergence points via "Next divergence" / "Prev divergence"

---

## 8. A/B Test Patching Design

### 8.1 Function Interception Architecture

The A/B test mode does NOT byte-patch the ROM. Instead, the emulator intercepts function calls at the CPU level:

```typescript
class FunctionPatch {
  /** Address of the original function in ROM */
  originalAddress: number;
  /** Compiled code of the decompiled version (loaded from .o file or compiled on-the-fly) */
  patchedCode: Uint8Array;
  /** Base address where the patched code is loaded in emulator memory */
  patchBaseAddress: number;
  /** Relocations applied to the patched code */
  relocations: Relocation[];
}
```

**Interception mechanism:**

The GBA ROM is loaded normally. The patched function's compiled code is loaded into a reserved region of memory (e.g., unused ROM space at the end, or a dedicated "patch bank" in EWRAM).

Two approaches:

**Approach A — Address rewriting (simpler):**

- Scan the ROM for BL/B instructions targeting the original function
- Rewrite them to target the patched code's address
- Problem: BL range is limited (±4MB in Thumb), and self-modifying code won't be caught

**Approach B — CPU hook (more robust, chosen):**

- Register a debug hook: `onInstructionPre(address)` checks if `address === originalFunction.address`
- When hit, redirect PC to the patched code's address
- Patched code returns via `bx lr` as normal, returning to the caller seamlessly
- No ROM modification needed. Works even for indirect calls (function pointers).

For the side-by-side comparison:

- Two emulator instances run in parallel
- Instance A: unmodified (no hooks)
- Instance B: hook installed on the target function
- Both receive identical input events
- Frame-sync: advance both one frame at a time, display both screens

### 8.2 User Flow

1. Load ROM in normal play mode
2. Enter A/B test mode (menu or keyboard shortcut)
3. Select function to patch:
   - Load `mizuchi-db.json` (from file or backend)
   - Browse function list, filter by name
   - Select a function that has decompiled C code
4. The system compiles the C code (via backend) or loads a pre-compiled .o file
5. Two screens appear side by side
6. Press Play — both emulators run in lockstep
7. A counter shows how many times the patched function has been called and whether any behavioral differences were detected
8. User can pause, step, and inspect state in either emulator independently

### 8.3 Backend Compilation Endpoint

For live editing of decompiled code:

```
POST /api/compile
Body: { code: string, functionName: string, context?: string }
Response: { success: true, objectFile: ArrayBuffer } | { success: false, error: string }
```

The backend runs the same `CCompiler` used by the pipeline (compilerScript from mizuchi.yaml). The compiled .o file is sent back to the webapp, which extracts the function code and loads it into the patched emulator.

---

## 9. Backend API

### 9.1 Architecture

A Hono server, following the Decomp Atlas pattern. Separate from the Decomp Atlas server (different port, different concern).

```
src/gba-debugger-server/
├── server.ts          # Hono app with routes
└── index.ts           # Entry point (CLI or import)
```

### 9.2 Endpoints

**Project Data (replicates Decomp Atlas pattern):**

```
POST /api/loadProject
Body: { projectPath: string }
Response: { data: MizuchiDbDump, platform: PlatformTarget }
```

Reads `mizuchi-db.json` from the project directory. Same implementation as Decomp Atlas server — potentially share the code.

```
POST /api/loadConfig
Body: { configPath?: string }
Response: { config: PipelineConfig }
```

Reads `mizuchi.yaml` and returns the pipeline config (needed for compilerScript, promptsDir, etc.).

**Compilation:**

```
POST /api/compile
Body: { code: string, functionName: string, context?: string }
Response: { objectFile: base64string } | { error: string }
```

Compiles C code using `CCompiler` and returns the .o file as base64.

**Behavioral Trace:**

```
POST /api/behavioralTrace
Body: {
  originalObjPath: string,
  compiledObjPath: string,
  functionName: string,
  config?: Partial<ComparisonConfig>
}
Response: BehavioralTestFile
```

Runs the comparator with trace enabled and returns the full trace JSON.

### 9.3 Static File Serving

Same pattern as Decomp Atlas:

- Production: serve the built `index.html` with `window.__MIZUCHI_CONFIG__` injected
- Dev: Vite dev server proxies `/api/` to the backend

### 9.4 What's client-only

Everything except compilation and project file reading:

- ROM loading (FileReader)
- Emulation (all in-browser)
- Save states (IndexedDB)
- Behavioral test JSON loading (FileReader)
- Debugger (all client-side)

---

## 10. Phased Implementation Plan

### Phase 0 — Foundation (arm-emulator refactoring)

**Scope:** Refactor `shared/arm-emulator` to support DI without breaking existing tests.

- [ ] Extract `MemoryBus` interface from `GbaMemory`
- [ ] Change `ThumbCpu` constructor to accept `MemoryBus`
- [ ] `GbaMemory` implements `MemoryBus`
- [ ] Add optional `DebugHooks` to CPU
- [ ] All existing tests pass unchanged
- [ ] Update dependency-cruiser rules for new modules

**Dependencies:** None
**Risk:** Low — purely additive refactoring

### Phase 1 — ARM Mode + BIOS

**Scope:** Extend CPU to support ARM mode instructions and HLE BIOS calls.

- [ ] Add ARM mode instruction decoder (~40 instruction patterns)
- [ ] Add CPU mode switching (usr, irq, svc, etc.) with banked registers
- [ ] Implement SWI dispatch for HLE BIOS calls
- [ ] Implement priority BIOS calls (VBlankIntrWait, Div, CpuSet, LZ77 decompression)
- [ ] Interrupt handling (save CPSR to SPSR, mode switch, jump to vector)
- [ ] Unit tests for ARM instructions, mode switching, BIOS calls

**Dependencies:** Phase 0
**Risk:** Medium — ARM instruction decoder is well-documented but large

### Phase 2 — Core GBA Hardware

**Scope:** Implement the hardware subsystems needed to boot a ROM.

- [ ] Event scheduler
- [ ] GbaSystemBus (MMIO dispatch)
- [ ] Interrupt controller (IE, IF, IME)
- [ ] Timers (4 channels, cascade, prescaler)
- [ ] DMA controller (4 channels, all modes)
- [ ] Input controller (keypad register)
- [ ] Minimal PPU: render solid-color backgrounds (verify timing)
- [ ] Boot sequence: ROM header parsing, entry point detection

**Dependencies:** Phase 1
**Risk:** Medium — hardware timing interactions are tricky. The scheduler design is critical.

### Phase 3 — PPU (Graphics)

**Scope:** Full Mode 0 + sprite rendering — enough for the target ROMs.

- [ ] Text background rendering (4bpp/8bpp tiles, scrolling)
- [ ] Sprite rendering (all sizes, priorities, 4bpp/8bpp)
- [ ] Layer compositing (BG + sprite priority sorting)
- [ ] Mode 0 complete
- [ ] Mode 1 (affine BG2)
- [ ] Alpha blending and brightness effects
- [ ] Windowing
- [ ] Sprite affine transforms

**Dependencies:** Phase 2
**Risk:** High — PPU is the largest subsystem. Edge cases in compositing and blending are numerous.

**Milestone:** Target ROMs boot to title screen.

### Phase 4 — APU (Audio)

**Scope:** DirectSound playback — enough for game music.

- [ ] DirectSound FIFO management (A/B channels)
- [ ] Timer-triggered sample playback
- [ ] DMA FIFO refill
- [ ] AudioWorklet output with resampling
- [ ] PSG channels (basic square, wave, noise)
- [ ] Channel mixing

**Dependencies:** Phase 2 (DMA + timers)
**Risk:** Medium — audio timing is sensitive. Ring buffer underruns cause audible glitches.

**Milestone:** Target ROMs have music and sound effects.

### Phase 5 — Web Application Shell

**Scope:** Basic webapp that loads and runs ROMs.

- [ ] Vite + React project setup in `src/ui/gba-debugger/`
- [ ] ROM loader (file picker + drag-and-drop)
- [ ] Canvas rendering (240x160 → upscaled)
- [ ] Keyboard input mapping
- [ ] Main loop with `requestAnimationFrame`
- [ ] Play/pause controls
- [ ] IndexedDB save state persistence

**Dependencies:** Phases 3 + 4
**Risk:** Low — standard web app patterns

**Milestone:** Playable in the browser (Sonic Advance 3 and Klonoa).

### Phase 6 — Debugger

**Scope:** Debug UI with all Phase 1 debugger features.

- [ ] Debug mode layout (4-panel grid)
- [ ] Register viewer
- [ ] Disassembly view (Thumb + ARM disassembler)
- [ ] Memory viewer/editor
- [ ] Breakpoints (set, remove, list)
- [ ] Step / Step Over / Run to breakpoint
- [ ] I/O register viewer
- [ ] Keyboard shortcuts (F5=run, F10=step over, F11=step in, F9=toggle breakpoint)

**Dependencies:** Phase 5
**Risk:** Medium — the disassembler needs to handle both ARM and Thumb. UI complexity.

### Phase 7 — Behavioral Test Visualization

**Scope:** Load and visualize behavioral test results.

- [ ] `BehavioralTestFile` JSON schema and export
- [ ] Trace-enabled comparator mode
- [ ] Summary view (scenario table, pass/fail, diff strings)
- [ ] Trace view (side-by-side instruction traces)
- [ ] Divergence highlighting and navigation
- [ ] Timeline scrubber
- [ ] Register diff sidebar

**Dependencies:** Phase 6 (reuses debug UI components)
**Risk:** Low-Medium — the data is already produced by the pipeline. This is mostly UI work.

### Phase 8 — A/B Test Patching

**Scope:** Run original vs. patched ROM side by side.

- [ ] Function interception via CPU hooks
- [ ] Dual emulator instances with frame sync
- [ ] Side-by-side screen rendering
- [ ] Function selector (from mizuchi-db)
- [ ] Divergence counter
- [ ] Backend compilation endpoint
- [ ] Live code editing with recompile

**Dependencies:** Phase 5 + backend server
**Risk:** High — dual emulator sync is complex. Frame-level determinism is hard to guarantee.

### Phase 9 — Backend Server

**Scope:** Hono server for compilation and project data.

- [ ] `/api/loadProject` (shared with Decomp Atlas pattern)
- [ ] `/api/loadConfig`
- [ ] `/api/compile` (CCompiler integration)
- [ ] `/api/behavioralTrace`
- [ ] Dev server integration (Vite proxy)
- [ ] Production static file serving

**Dependencies:** Phase 5 (webapp exists to consume the API)
**Risk:** Low — follows established Decomp Atlas patterns

### Dependency Graph

```
Phase 0 (DI refactor)
  └→ Phase 1 (ARM mode + BIOS)
       └→ Phase 2 (Core hardware)
            ├→ Phase 3 (PPU) ─────┐
            └→ Phase 4 (APU) ─────┤
                                  ├→ Phase 5 (Web app shell) ──→ Phase 9 (Backend)
                                  │     └→ Phase 6 (Debugger)
                                  │          └→ Phase 7 (Behavioral viz)
                                  │
                                  └→ Phase 8 (A/B testing) ←── Phase 9
```

### Realistic Effort Estimates

| Phase   | Estimated Complexity                                            |
| ------- | --------------------------------------------------------------- |
| Phase 0 | Small — 1-2 sessions                                            |
| Phase 1 | Medium — ARM decoder is systematic but large (~40 instructions) |
| Phase 2 | Medium — scheduler + DMA + timers are well-specified            |
| Phase 3 | Large — PPU is the biggest subsystem, many edge cases           |
| Phase 4 | Medium — DirectSound is straightforward, PSG adds complexity    |
| Phase 5 | Small-Medium — standard webapp with existing patterns           |
| Phase 6 | Medium — debugger UI with multiple interactive panels           |
| Phase 7 | Small-Medium — mostly UI work, data already exists              |
| Phase 8 | Large — dual emulator sync, function interception               |
| Phase 9 | Small — follows Decomp Atlas pattern                            |

---

## 11. Risks

### 11.1 ARM Mode Instruction Decoder Completeness

**Risk:** Missing or incorrect ARM instruction implementations cause subtle bugs.
**Mitigation:** Use the ARM7TDMI data sheet as the authoritative reference. Write comprehensive tests for each instruction format. Test against known-good outputs from other emulators.
**Severity:** Medium — most game code is Thumb; ARM mode is used sparingly.

### 11.2 PPU Accuracy for Target ROMs

**Risk:** The target ROMs use PPU features or timing tricks that our scanline renderer doesn't handle.
**Mitigation:** Start with the most common features (Mode 0, basic sprites, blending). Test early and often with the actual ROMs. Consult mGBA source for edge cases. Sonic Advance 3 likely uses affine sprites and blending effects that require careful implementation.
**Severity:** High — PPU bugs are the #1 cause of visual corruption in emulators.

### 11.3 Audio Timing and Ring Buffer Underruns

**Risk:** Audio crackles, pops, or silence due to buffer underruns or timing mismatches.
**Mitigation:** Use a generous ring buffer (4096 samples). Implement adaptive sync — if audio gets ahead, insert silence; if behind, skip samples. The AudioWorklet approach with SharedArrayBuffer is the most robust.
**Severity:** Medium — audio glitches are noticeable but don't block functionality.

### 11.4 DMA/Timer/IRQ Interaction Bugs

**Risk:** Subtle timing bugs in hardware interactions (e.g., DMA triggered by timer overflow while handling an IRQ).
**Mitigation:** The event scheduler serializes all events. Implement a strict priority order: DMA > timers > IRQ. Test with the target ROMs' audio system (DMA FIFO refill is timing-sensitive).
**Severity:** Medium — usually manifests as audio glitches or missed frames.

### 11.5 Dual Emulator Determinism (A/B Testing)

**Risk:** The two emulator instances drift out of sync due to floating-point differences, non-deterministic scheduling, or Web API timing variations.
**Mitigation:** Both instances share the same input queue (keyboard events timestamped by frame number). Both use integer-only math. The scheduler is deterministic. If drift occurs, resync at VBlank boundaries.
**Severity:** High for A/B testing feature specifically, but this feature is Phase 8 (later phase).

### 11.6 Performance on Main Thread

**Risk:** Full GBA emulation at 60fps on the main thread causes frame drops, especially with debug hooks active.
**Mitigation:** Profile early. The GBA CPU runs at ~16.78 MHz; at 60fps that's ~280K cycles per frame. Modern JS engines handle this easily (gbajs proved it). If performance is insufficient, move to Web Worker in Phase 2. Disable debug hooks during full-speed play.
**Severity:** Low — unlikely to be a problem based on gbajs precedent.

### 11.7 Breaking Changes to `shared/arm-emulator`

**Risk:** The DI refactoring breaks the existing behavioral test pipeline.
**Mitigation:** Phase 0 is purely additive — `MemoryBus` interface extracted from existing methods, `GbaMemory` satisfies it. Run the full test suite after every change. The behavioral plugin continues to use `GbaMemory` directly.
**Severity:** Low — careful refactoring with test coverage.

### 11.8 BIOS HLE Completeness

**Risk:** Target ROMs call BIOS functions we haven't implemented.
**Mitigation:** Implement a fallback that logs unimplemented SWI calls with their number and arguments. This makes it easy to identify which calls need implementation. The most common BIOS calls (VBlankIntrWait, Div, CpuSet, LZ77) cover 90%+ of game usage.
**Severity:** Low-Medium — most games use a small subset of BIOS calls.

### 11.9 Save Type Detection

**Risk:** The target ROMs use a save type (Flash, EEPROM, SRAM) that we need to detect and emulate.
**Mitigation:** ROMs contain identifying strings ("SRAM_V", "FLASH_V", "EEPROM_V") that indicate the save type. Parse these on ROM load. Implement SRAM (simplest) first, then Flash (needed for most games).
**Severity:** Low — save type emulation is well-understood.

### 11.10 Scope Creep

**Risk:** The project is large and easy to over-scope, especially the "broad GBA compatibility" stretch goals.
**Mitigation:** The phased plan defines clear milestones. Each phase delivers a usable artifact. Phase 3's milestone (target ROMs boot) is the primary success criterion. Everything after that is incremental improvement. Resist the urge to add features not needed for the two target ROMs until after Phase 5.
**Severity:** High — this is the most likely risk. Discipline required.

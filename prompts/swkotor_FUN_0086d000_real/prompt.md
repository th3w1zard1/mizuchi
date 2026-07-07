# FUN_0086d000

Target: `swkotor.exe` `.bind` function at `0x0086d000`.

Success requires the C candidate to compile with the configured MSVC command and
match the target code bytes through objdiff with zero code differences.

## Assembly

```asm
0086d000: PUSH EBP
0086d001: MOV EBP,ESP
0086d003: POP EBP
0086d004: RET
```

## Ghidra Decompile

```c
void FUN_0086d000(void)

{
  return;
}
```

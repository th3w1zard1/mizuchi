__asm__(
".text\n"
".globl FUN_00148020\n"
".type FUN_00148020, @function\n"
"FUN_00148020:\n"
"    movl    (%ecx), %eax\n"
"    testl   %eax, %eax\n"
"    jnz     1f\n"
"    movl    $0x0040e180, %eax\n"
"1:\n"
"    ret\n"
);

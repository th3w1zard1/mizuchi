.text
.globl FUN_00148020
.type FUN_00148020, @function
FUN_00148020:
    movl    (%ecx), %eax
    testl   %eax, %eax
    jnz     .Lret
    movl    $0x0040e180, %eax
.Lret:
    ret

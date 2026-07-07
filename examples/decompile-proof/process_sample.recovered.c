
int process_sample(int param_1,int param_2,int param_3,int param_4)

{
  uint uVar1;
  int iVar2;
  long lVar3;
  int iVar4;
  long lVar5;
  int iVar6;

  lVar5 = (long)param_1 * (long)param_2 >> 0x10;
  iVar6 = (param_3 + param_4) / 2;
  if (lVar5 < param_3) {
    lVar5 = (long)param_3;
  }
  lVar3 = (long)param_4;
  if (lVar5 <= param_4) {
    lVar3 = lVar5;
  }
  iVar4 = (int)lVar3 - iVar6;
  uVar1 = iVar4 >> 0x1f;
  iVar2 = -uVar1;
  return (iVar2 + iVar4 ^ uVar1) + iVar6 + iVar2;
}

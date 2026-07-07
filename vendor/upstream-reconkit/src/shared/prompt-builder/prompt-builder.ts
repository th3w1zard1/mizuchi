import type { PlatformTarget } from '~/shared/config.js';
import type { ReconstructKitDb } from '~/shared/reconkit-db/reconkit-db.js';

import { getFuncContext } from './codebase-context.js';
import { craftPrompt } from './craft-prompt.js';

export async function createDecompilePrompt(params: {
  db: ReconstructKitDb;
  functionId: string;
  projectRoot: string;
  platform: PlatformTarget;
}): Promise<string> {
  const { db, functionId, projectRoot, platform } = params;

  const func = db.getFunctionById(functionId);
  if (!func) {
    throw new Error(`Function not found: ${functionId}`);
  }

  const { asmDeclaration, calledFunctionsDeclarations, sampling, typeDefinitions } = await getFuncContext(
    db,
    functionId,
    projectRoot,
  );

  return craftPrompt({
    platform,
    modulePath: func.asmModulePath,
    asmName: func.name,
    asmDeclaration,
    asmCode: func.asmCode,
    calledFunctionsDeclarations,
    sampling,
    typeDefinitions,
  });
}

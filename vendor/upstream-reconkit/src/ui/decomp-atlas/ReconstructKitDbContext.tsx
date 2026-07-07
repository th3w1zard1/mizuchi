import type { ReconstructKitDb } from '@shared/reconkit-db/reconkit-db';
import { createContext, useContext } from 'react';

const ReconstructKitDbContext = createContext<ReconstructKitDb | null>(null);

export function ReconstructKitDbProvider({ db, children }: { db: ReconstructKitDb; children: React.ReactNode }) {
  return <ReconstructKitDbContext.Provider value={db}>{children}</ReconstructKitDbContext.Provider>;
}

export function useReconstructKitDb(): ReconstructKitDb {
  const db = useContext(ReconstructKitDbContext);
  if (!db) {
    throw new Error('useReconstructKitDb must be used within a ReconstructKitDbProvider');
  }
  return db;
}
